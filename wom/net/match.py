"""Partida del servidor dedicado: autoridad del lockstep, **sin ser jugador**.

`MatchRunner` es el lado host del lockstep (como `NetGame`/`HostSession` en LAN)
pero **player-less**: no aporta órdenes propias, solo junta las de los jugadores
(humanos + IA de relleno), arma el conjunto, corre `Game.run_turn` como
**autoridad** y arbitra los hashes (resincroniza al que diverja). Ver
`docs/server.md` §4.

Lo conduce el servidor (fase S4): traduce los eventos del `LobbyServer`
(`SeatTaken`/`SeatVacated`/`SeatRejoined`/`MatchMessage`) a llamadas
(`player_joined`/`player_left`/`player_rejoined`/`handle`) y lo tickea con
`update()`. Para mandar mensajes usa un *sink* mínimo —`send_to(lobby_id, msg)`—
que en producción es el propio `LobbyServer`. No importa pygame: testeable
headless.

Reusa los helpers puros del lockstep (`canonical_order`, `state_digest`,
`encode_orders`/`decode_orders`) y la validación por dueño (anti-cheat): una
orden de un jugador solo puede tocar lo suyo.
"""

from __future__ import annotations

import time
from collections import defaultdict
from enum import Enum, auto
from typing import Callable, Protocol

from wom.core.game import Game, Player
from wom.core.mapgen import map_params_for
from wom.core.orders import (
    CreateArmyOrder,
    MoveOrder,
    Order,
    SplitArmyOrder,
)
from wom.core.victory import VictoryMode, VictoryResult
from wom.net.lobby import MatchSpec
from wom.net.lockstep import canonical_order
from wom.net.net_battle import NetBattleDriver
from wom.net.orders_codec import decode_orders, encode_orders
from wom.net.protocol import (
    BattleEnd,
    BattleInput,
    BattleVote,
    Chat,
    GameSetup,
    Hash,
    Orders,
    Ready,
    Start,
    StateSync,
    TurnOrders,
)
from wom.net.rules import MatchRules, TACTICAL_OFF
from wom.net.state_hash import state_digest


class Sink(Protocol):
    """Lo único que el runner necesita del entorno para mandar mensajes."""

    def send_to(self, lobby_id: int, message) -> bool: ...


class Phase(Enum):
    ROOM = auto()      # esperando a que se llene la sala y todos marquen listo
    PLAYING = auto()   # lockstep en curso
    FINISHED = auto()  # terminó (victoria/límite) o se vació


# --- construcción del Game inicial ----------------------------------------


def default_game_builder(
    spec: MatchSpec,
    seed: int,
    names: dict[int, str],
    *,
    scenario_loader: Callable[[str], object] | None = None,
) -> Game:
    """Arma el `Game` inicial de una partida según su `MatchSpec`.

    - `random`: genera un mapa para `spec.max_players` jugadores.
    - `scenario`: carga el `.wom` (`scenario_loader(map_ref) -> ScenarioDoc`) y
      arranca con su terreno/tropas/victoria.

    Los jugadores son todos humanos (la IA solo cubre ausencias en vivo); el
    nombre de cada asiento sale de `names`.
    """
    n = spec.max_players
    players = [Player(i, names.get(i, f"Jugador {i + 1}"), is_ai=False) for i in range(n)]
    if spec.map_source == "scenario":
        if scenario_loader is None:
            raise ValueError("falta el cargador de escenarios")
        doc = scenario_loader(spec.map_ref)
        game = Game.from_setup(doc.world, players, doc.army_specs, doc.victory_mode, seed=seed)
    else:
        size = str(spec.rules.get("map_size", "medio"))
        game = Game.new(map_params_for(size, n, seed), players, VictoryMode.TOTAL)
    rules = MatchRules.from_dict(spec.rules)
    game.turn_limit = rules.max_turns
    return game


# --- runner ----------------------------------------------------------------


class MatchRunner:
    """Conduce el lockstep autoritativo de una partida del servidor."""

    def __init__(
        self,
        match_id: int,
        spec: MatchSpec,
        sink: Sink,
        *,
        seed: int,
        ai_factory: Callable[[int], object] | None = None,
        game_builder: Callable[..., Game] = default_game_builder,
        scenario_loader: Callable[[str], object] | None = None,
    ) -> None:
        self.match_id = match_id
        self.spec = spec
        self.sink = sink
        self.seed = seed
        self.ai_factory = ai_factory
        self._build = game_builder
        self._scenario_loader = scenario_loader
        self.rules = MatchRules.from_dict(spec.rules)

        self.phase = Phase.ROOM
        self.game: Game | None = None
        self.seats: dict[int, int] = {}   # seat → lobby_id (humano presente)
        self.names: dict[int, str] = {}   # seat → nombre
        self._ready: set[int] = set()
        self._absent: set[int] = set()    # asientos sin humano (los cubre la IA)
        self._ai: dict[int, object] = {}
        self._setup_sent = False

        # Contabilidad del lockstep (lado autoridad).
        self._turn = 0
        self._collected: dict[int, dict[int, list[Order]]] = defaultdict(dict)
        self._auth_hashes: dict[int, str] = {}       # turno → hash autoritativo
        self._peer_hashes: dict[int, dict[int, str]] = defaultdict(dict)
        self._sync_sent: set[tuple[int, int]] = set()

        # Zoom de batalla: resolución de turno por cola (puede abarcar varios
        # ticks mientras se dirige una batalla en tiempo real).
        self.tactical_mode = self.rules.tactical_mode
        self._resolving = False
        self._battle_queue: list[tuple[int, int]] = []
        self._battle_idx = 0
        self._turn_being_resolved = 0
        self.active_battle: NetBattleDriver | None = None
        # Reloj para el dt del combate en tiempo real (inyectable en tests).
        self._clock = time.monotonic
        self._last_tick = self._clock()

        self.chat_log: list[tuple[str, str]] = []
        self.result: VictoryResult | None = None

    # --- altas / bajas de jugadores (las llama el servidor) --------------

    def player_joined(self, seat: int, lobby_id: int, name: str) -> None:
        """Un jugador ocupó un asiento (al crear o unirse en la sala)."""
        self.seats[seat] = lobby_id
        self.names[seat] = name
        if self.game is None:
            # La sala se arma (y se manda el estado inicial a TODOS) recién al
            # completarse: ahí ya se conocen todos los nombres.
            if len(self.seats) >= self.spec.max_players:
                self._build_game()
                for present_seat in self.seats:
                    self._send_setup(present_seat)
        else:
            # Sala ya armada y entra alguien (p. ej. tomó un asiento liberado en
            # la sala antes de empezar): solo a él.
            self._send_setup(seat)

    def player_left(self, seat: int, lobby_id: int) -> None:
        """Un jugador dejó su asiento. En sala se quita; en partida lo cubre la
        IA (asiento reservado)."""
        self.seats.pop(seat, None)
        self._ready.discard(seat)
        if self.phase is Phase.PLAYING:
            self._absent.add(seat)
            if self.ai_factory is not None and seat not in self._ai:
                self._ai[seat] = self.ai_factory(seat)
            self._collected.get(self._turn, {}).pop(seat, None)
            # Si dirigía la batalla en curso, se auto-resuelve (requisito 6).
            if self.active_battle is not None:
                self.active_battle.mark_absent(seat)
            self._system_chat(f"{self.names.get(seat, f'Jugador {seat + 1}')} se desconectó; lo cubre la IA")
            self._try_resolve()

    def player_rejoined(self, seat: int, lobby_id: int, name: str) -> None:
        """Un jugador reconectó y retoma su asiento: recibe el estado vivo."""
        self.seats[seat] = lobby_id
        self.names[seat] = name
        self._absent.discard(seat)
        self._ai.pop(seat, None)
        if self.game is not None:
            self.sink.send_to(lobby_id, self._setup_message(seat))
            if self.phase is Phase.PLAYING:
                self.sink.send_to(lobby_id, Start())
        self._system_chat(f"{name} volvió a la partida")

    # --- mensajes de los jugadores ---------------------------------------

    def handle(self, seat: int, lobby_id: int, message) -> None:
        if isinstance(message, Ready):
            if self.phase is Phase.ROOM:
                if message.ready:
                    self._ready.add(seat)
                else:
                    self._ready.discard(seat)
                self._maybe_start()
        elif isinstance(message, Orders):
            self._on_orders(seat, message)
        elif isinstance(message, Hash):
            self._peer_hashes[message.turn][seat] = message.digest
            self._check_hash(message.turn, seat)
        elif isinstance(message, BattleVote):
            if self.active_battle is not None:
                self.active_battle.apply_vote(seat, message.zoom)
        elif isinstance(message, BattleInput):
            if self.active_battle is not None:
                self.active_battle.apply_input(
                    seat, message.kind,
                    unit_ids=message.unit_ids, order_kind=message.order_kind,
                    target=message.target, formation=message.formation,
                )
        elif isinstance(message, Chat):
            self.chat_log.append((message.name, message.text))
            self._relay_chat(message, exclude=seat)

    def update(self) -> None:
        """Tick: avanza la batalla en curso y resuelve el turno cuando se puede."""
        now = self._clock()
        dt = max(0.0, now - self._last_tick)
        self._last_tick = now
        if self.phase is not Phase.PLAYING:
            return
        self._tick_active_battle(dt)
        self._try_resolve()

    def _tick_active_battle(self, dt: float) -> None:
        driver = self.active_battle
        if driver is None:
            return
        driver.tick(dt)
        for msg in driver.drain():
            self._broadcast(msg)
        if driver.finished:
            self._finish_authority_battle()

    @property
    def finished(self) -> bool:
        return self.phase is Phase.FINISHED

    # --- arranque de la partida ------------------------------------------

    def _build_game(self) -> None:
        self.game = self._build(
            self.spec, self.seed, dict(self.names), scenario_loader=self._scenario_loader
        )
        self._turn = self.game.turn

    def _send_setup(self, seat: int) -> None:
        lobby_id = self.seats.get(seat)
        if lobby_id is not None:
            self.sink.send_to(lobby_id, self._setup_message(seat))

    def _setup_message(self, seat: int) -> GameSetup:
        return GameSetup(
            state=self.game.to_dict(),
            rules=self.rules.to_dict(),
            names=[self.names.get(i, f"Jugador {i + 1}") for i in range(self.spec.max_players)],
            human_id=seat,
        )

    def _maybe_start(self) -> None:
        if self.phase is not Phase.ROOM or self.game is None:
            return
        if len(self.seats) < self.spec.max_players:
            return
        if any(seat not in self._ready for seat in self.seats):
            return
        for lobby_id in self.seats.values():
            self.sink.send_to(lobby_id, Start())
        self.phase = Phase.PLAYING

    # --- lockstep (autoridad) --------------------------------------------

    def _on_orders(self, seat: int, message: Orders) -> None:
        if self.phase is not Phase.PLAYING or message.turn != self._turn:
            return
        orders = self._validate_owned(decode_orders(message.orders), seat)
        self._collected[message.turn][seat] = orders
        self._try_resolve()

    def _try_resolve(self) -> None:
        if self.phase is not Phase.PLAYING or self.game is None:
            return
        if self._resolving or self.active_battle is not None:
            return  # ya resolviendo este turno (quizás dirigiendo una batalla)
        present = [s for s in self.seats if s not in self._absent]
        collected = self._collected.get(self._turn, {})
        if any(seat not in collected for seat in present):
            return  # falta algún humano presente
        bundle: dict[int, list[Order]] = {seat: collected.get(seat, []) for seat in present}
        for seat in self._absent:  # la IA cubre a los ausentes
            bundle[seat] = self._ai_orders(seat)
        self._broadcast(
            TurnOrders(turn=self._turn, orders=[[s, encode_orders(od)] for s, od in bundle.items()])
        )
        self._begin_resolution(self._turn, bundle)

    # --- resolución del turno con cola de batallas ------------------------

    def _begin_resolution(self, turn: int, bundle: dict[int, list[Order]]) -> None:
        combined = canonical_order([o for orders in bundle.values() for o in orders])
        self._resolving = True
        self._turn_being_resolved = turn
        self._battle_queue = self.game.begin_turn(combined)
        self._battle_idx = 0
        self._advance_battles()

    def _advance_battles(self) -> None:
        while self._battle_idx < len(self._battle_queue):
            attacker_id, defender_id = self._battle_queue[self._battle_idx]
            if self.tactical_mode == TACTICAL_OFF:
                self.game.resolve_one_battle(attacker_id, defender_id)
                self._battle_idx += 1
                continue
            if self._start_authority_battle(attacker_id, defender_id):
                return  # batalla dirigida en curso
            self._battle_idx += 1
        self._finish_resolution()

    def _start_authority_battle(self, attacker_id: int, defender_id: int) -> bool:
        attacker = self.game.army_by_id(attacker_id)
        defender = self.game.army_by_id(defender_id)
        if attacker is None or defender is None:
            self.game.resolve_one_battle(attacker_id, defender_id)
            return False
        humans = [o for o in dict.fromkeys((attacker.owner, defender.owner)) if o in self.seats]
        if not humans:
            self._broadcast(BattleEnd(battle_id=self._battle_idx, result={}))
            self.game.resolve_one_battle(attacker_id, defender_id)
            return False
        self.active_battle = NetBattleDriver(
            battle_id=self._battle_idx,
            attacker=attacker,
            defender=defender,
            world=self.game.world,
            classes=self.game.classes,
            battle_config=self.game.config["batalla"],
            mode=self.tactical_mode,
            human_owners=humans,
            seed=self._turn_being_resolved * 1000 + self._battle_idx,
        )
        for msg in self.active_battle.drain():
            self._broadcast(msg)
        self._last_tick = self._clock()
        return True

    def _finish_authority_battle(self) -> None:
        driver = self.active_battle
        attacker_id, defender_id = self._battle_queue[self._battle_idx]
        self.game.resolve_one_battle(attacker_id, defender_id, result=driver.result)
        self.active_battle = None
        self._battle_idx += 1
        self._advance_battles()

    def _finish_resolution(self) -> None:
        result = self.game.finish_turn()
        resolved_turn = self.game.turn  # ya incrementado
        self._auth_hashes[resolved_turn] = state_digest(self.game)
        self._collected.pop(self._turn_being_resolved, None)
        self._turn = self.game.turn
        self._resolving = False
        self._battle_queue = []
        if result.is_over:
            self.result = result
            self.phase = Phase.FINISHED
        # Hashes de clientes que pudieron llegar antes de resolver.
        for seat in list(self._peer_hashes.get(resolved_turn, {})):
            self._check_hash(resolved_turn, seat)

    def _ai_orders(self, seat: int) -> list[Order]:
        controller = self._ai.get(seat)
        return controller.decide_orders(self.game) if controller is not None else []

    def _check_hash(self, turn: int, seat: int) -> None:
        auth = self._auth_hashes.get(turn)
        peer = self._peer_hashes.get(turn, {}).get(seat)
        if auth is None or peer is None or auth == peer:
            return
        key = (turn, seat)
        if key in self._sync_sent:
            return
        self._sync_sent.add(key)
        lobby_id = self.seats.get(seat)
        if lobby_id is not None:
            self.sink.send_to(lobby_id, StateSync(turn=self.game.turn, state=self.game.to_dict()))

    # --- validación por dueño (anti-cheat) -------------------------------

    def _validate_owned(self, orders: list[Order], seat: int) -> list[Order]:
        return [o for o in orders if self._owned_by(o, seat)]

    def _owned_by(self, order: Order, seat: int) -> bool:
        if isinstance(order, CreateArmyOrder):
            fort = self.game.world.fort_at(order.position)
            return fort is not None and fort.owner == seat
        if isinstance(order, MoveOrder):
            return self._army_owner(order.army_id) == seat
        if isinstance(order, SplitArmyOrder):
            return self._army_owner(order.source_id) == seat
        # Merge / Transfer: ambos extremos deben ser del jugador.
        return self._army_owner(order.source_id) == seat and self._army_owner(order.target_id) == seat

    def _army_owner(self, army_id: int) -> int | None:
        army = self.game.army_by_id(army_id)
        return army.owner if army is not None else None

    # --- helpers ----------------------------------------------------------

    def _broadcast(self, message) -> None:
        for seat, lobby_id in self.seats.items():
            if seat not in self._absent:
                self.sink.send_to(lobby_id, message)

    def _relay_chat(self, message: Chat, exclude: int) -> None:
        for seat, lobby_id in self.seats.items():
            if seat != exclude and seat not in self._absent:
                self.sink.send_to(lobby_id, message)

    def _system_chat(self, text: str) -> None:
        self.chat_log.append(("Sistema", text))
        self._broadcast(Chat(name="Sistema", text=text, ts=0.0))
