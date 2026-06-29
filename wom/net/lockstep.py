"""Controlador de la partida en red (lockstep) sobre una `Session`.

Topología estrella con el host como autoridad (hasta `MAX_PLAYERS`). Cada nodo
tiene su propia copia idéntica del `Game`. Por turno:

1. El humano local arma sus órdenes y las "envía" (`submit_local_orders`). El
   cliente se las manda al host; el host las guarda junto a las suyas.
2. El **host** espera tener las órdenes de TODOS (las suyas + las de cada
   cliente), arma el conjunto completo, lo reparte (`TurnOrders`) y resuelve el
   turno. Cada cliente, al recibir el conjunto (`TurnReady`), corre exactamente
   las mismas órdenes. Como todos aplican el mismo bundle, el lockstep no puede
   divergir por el orden de llegada.
3. Tras resolver, cada cliente le manda su `Hash` al host; el host compara con
   el suyo (autoritativo) y, si difieren, le reenvía su estado (`StateSync`).

El host valida las órdenes de cada rival (que toquen solo lo suyo) antes de
incluirlas en el bundle. No importa pygame: testeable headless. La UI
(`GameScreen` en modo red) lo conduce con `update()` por frame y consume el
resultado de cada turno para animarlo (`consume_resolved`).
"""

from __future__ import annotations

import math
import random
import time
from collections import defaultdict
from enum import Enum, auto

from wom.core.game import Game
from wom.core.orders import (
    CreateArmyOrder,
    MergeArmyOrder,
    MoveOrder,
    Order,
    SplitArmyOrder,
    TransferTroopsOrder,
)
from wom.core.tactical import build_tactical_battle
from wom.core.victory import VictoryResult
from wom.net.net_battle import NetBattleDriver, decode_result
from wom.net.orders_codec import decode_orders, encode_orders
from wom.net.protocol import BattleEnd, BattleInput
from wom.net.rules import TACTICAL_OFF
from wom.net.session import (
    BattleBeginReceived,
    BattleEndReceived,
    BattleInputReceived,
    BattleOfferReceived,
    BattleSnapshotReceived,
    BattleVoteReceived,
    ChatReceived,
    Disconnected,
    HashReceived,
    OrdersReceived,
    PlayerLeft,
    PlayerRejoined,
    StateSyncReceived,
    TurnReady,
)
from wom.net.state_hash import state_digest


# Constante de tiempo del suavizado de posiciones en el cliente (ver
# ClientBattle.interpolate): cuanto más chica, más pegado al snapshot (menos
# retardo) pero menos suave; ~0.06s sigue bien un combate a 30 Hz sin saltos.
INTERP_TAU = 0.06


class ClientBattle:
    """Espejo local de una batalla en red para renderizar (lado cliente).

    El cliente no simula: arma un `TacticalBattle` local (mismo estado + seed que
    la autoridad) para el layout y guarda la última posición autoritativa de cada
    ficha (`_target`) que llega en los `BattleSnapshot`. Cada frame, `interpolate`
    acerca la posición dibujada a ese objetivo con suavizado exponencial, así el
    movimiento es **fluido** (sin microsaltos entre snapshots) aunque la red
    entregue posiciones a 30 Hz."""

    def __init__(self, battle, battle_id, attacker_id, defender_id, human_owners):
        self.battle = battle  # TacticalBattle (None mientras solo se vota)
        self.battle_id = battle_id
        self.attacker_id = attacker_id
        self.defender_id = defender_id
        self.human_owners = list(human_owners)
        self.phase = "voting"
        self.countdown = 0.0
        self.arrows: list = []
        self._target: dict[int, tuple[float, float]] = {}  # uid → posición autoritativa

    def apply_snapshot(self, msg) -> None:
        self.phase = msg.phase
        self.countdown = msg.countdown
        if self.battle is not None:
            by_id = {u.id: u for u in self.battle.units}
            for uid, x, y, hp, fled in msg.units:
                u = by_id.get(uid)
                if u is None:
                    continue
                u.hp, u.fled = hp, bool(fled)
                if uid not in self._target:
                    u.x, u.y = x, y  # primera posición: sin interpolar (calza con el deploy)
                self._target[uid] = (x, y)
        self.arrows = list(msg.arrows)

    def interpolate(self, dt: float) -> None:
        """Acerca cada ficha dibujada a su última posición autoritativa.

        Suavizado exponencial: con un objetivo que avanza a velocidad constante,
        la salida lo sigue a la MISMA velocidad con un pequeño retardo fijo →
        movimiento continuo, sin los saltos de pintar el snapshot crudo."""
        if self.battle is None or dt <= 0:
            return
        alpha = 1.0 - math.exp(-dt / INTERP_TAU)
        for u in self.battle.units:
            tgt = self._target.get(u.id)
            if tgt is None:
                continue
            u.x += (tgt[0] - u.x) * alpha
            u.y += (tgt[1] - u.y) * alpha


class Phase(Enum):
    COLLECTING = auto()  # el humano arma sus órdenes
    WAITING = auto()     # órdenes locales enviadas, falta el resto
    ENDED = auto()       # fin de partida o desconexión


# Orden canónico de aplicación: ambos clientes deben combinar sus órdenes en
# EXACTAMENTE la misma secuencia, o el resultado de un turno podría diferir
# (p. ej. una fusión y una división que se afectan). Se ordena por tipo y luego
# por id/posición. El tipo manda primero, así reorganizaciones y movimientos no
# se comparan entre sí por su sub-clave.
_KIND_RANK = {
    CreateArmyOrder: 0,
    MergeArmyOrder: 1,
    SplitArmyOrder: 2,
    TransferTroopsOrder: 3,
    MoveOrder: 4,
}


def _order_key(order: Order):
    rank = _KIND_RANK[type(order)]
    if isinstance(order, CreateArmyOrder):
        return (rank, order.position)
    if isinstance(order, MoveOrder):
        return (rank, (order.army_id,))
    if isinstance(order, MergeArmyOrder):
        return (rank, (order.source_id, order.target_id))
    if isinstance(order, SplitArmyOrder):
        return (rank, (order.source_id,))
    return (rank, (order.source_id, order.target_id))  # TransferTroopsOrder


def canonical_order(orders: list[Order]) -> list[Order]:
    """Devuelve las órdenes en el orden canónico de aplicación (determinista)."""
    return sorted(orders, key=_order_key)


class NetGame:
    """Conduce el lockstep de una partida en red para un jugador."""

    def __init__(
        self,
        session,
        game: Game,
        human_id: int,
        is_host: bool,
        peer_name: str = "",
        ai_factory=None,
        tactical_mode: str = TACTICAL_OFF,
    ) -> None:
        self.session = session
        self.game = game
        self.human_id = human_id
        self.is_host = is_host
        self.tactical_mode = tactical_mode
        # Nombre(s) de los rivales para el HUD: los demás jugadores de la partida.
        self.peer_name = peer_name or ", ".join(
            p.name for p in game.players if p.id != human_id
        )
        self.n_players = len(game.players)

        # Toma por IA de jugadores caídos (solo el host): `ai_factory(pid)`
        # devuelve un controlador con `decide_orders(game)`. Mientras un jugador
        # está ausente, el host genera sus órdenes con la IA y las mete en el
        # bundle; los clientes las reciben ya hechas (no rompe el lockstep).
        self.ai_factory = ai_factory
        self._absent: set[int] = set()
        self._ai: dict[int, object] = {}
        if is_host and hasattr(session, "live_state_provider"):
            session.live_state_provider = lambda: (
                self.game.to_dict(), [p.name for p in self.game.players]
            )

        self.phase = Phase.COLLECTING
        self._turn = game.turn  # turno que se está jugando ahora
        self._local_orders: list[Order] | None = None
        # host: órdenes recibidas de cada rival, por turno → {player_id: [Order]}
        self._client_orders: dict[int, dict[int, list[Order]]] = defaultdict(dict)
        self._local_hashes: dict[int, str] = {}
        # host: hashes recibidos de los clientes, por turno → {player_id: digest}
        self._peer_hashes: dict[int, dict[int, str]] = defaultdict(dict)

        self.chat_log: list[tuple[str, str]] = []
        self.disconnected = False
        self.disconnect_reason = ""
        self.desync = False
        self._sync_sent: set[tuple[int, int]] = set()  # (turn, player_id) ya resincronizados
        self.resynced = False    # el cliente adoptó un estado autoritativo
        # (resultado, snapshot pre-turno) del último turno resuelto, para la UI.
        self._resolved: tuple[VictoryResult, list[dict]] | None = None

        # --- resolución de turno con zoom de batalla -----------------------
        # La resolución de un turno puede abarcar varios frames mientras se
        # dirige una batalla en tiempo real. `_resolving` marca que estamos en
        # medio de la cola; `_battle_queue`/`_battle_idx` recorren las batallas.
        self._resolving = False
        self._battle_queue: list[tuple[int, int]] = []
        self._battle_idx = 0
        self._turn_being_resolved = self._turn
        self._pre_turn: list[dict] = []
        # Autoridad: driver de la batalla activa. Cliente: espejo + id esperado.
        self.active_battle: NetBattleDriver | None = None  # host/autoridad
        self.client_battle: ClientBattle | None = None     # cliente
        self._awaiting_battle: int | None = None
        # Reloj para el dt del combate en tiempo real (inyectable en tests).
        self._clock = time.monotonic
        self._last_tick = self._clock()

    # --- API que usa la UI -------------------------------------------------

    def submit_local_orders(self, orders: list[Order]) -> None:
        """Finaliza el turno del humano: envía sus órdenes y espera al resto."""
        if self.phase is not Phase.COLLECTING:
            return
        self._local_orders = list(orders)
        self.phase = Phase.WAITING
        if self.is_host:
            self._try_resolve_host()
        else:
            self.session.submit_orders(self._turn, encode_orders(orders))

    def update(self) -> None:
        """Drena la red y resuelve el turno si ya están todas las órdenes."""
        now = self._clock()
        dt = max(0.0, now - self._last_tick)
        self._last_tick = now
        if self.phase is Phase.ENDED:
            return
        for event in self.session.update():
            self._handle(event)
        if self.is_host:
            self._tick_active_battle(dt)
            self._try_resolve_host()

    def _tick_active_battle(self, dt: float) -> None:
        """Autoridad: avanza la batalla en curso y difunde su estado."""
        driver = self.active_battle
        if driver is None:
            return
        driver.tick(dt)
        for msg in driver.drain():
            self.session.broadcast_battle(msg)
        if driver.finished:
            self._finish_authority_battle()

    def consume_resolved(self) -> tuple[VictoryResult, list[dict]] | None:
        """Devuelve (y limpia) el resultado del último turno resuelto, o None."""
        resolved, self._resolved = self._resolved, None
        return resolved

    # --- API del zoom de batalla para la UI -------------------------------

    def battle_present(self) -> bool:
        """Hay una batalla en red activa para mostrar/dirigir."""
        return self.active_battle is not None or self.client_battle is not None

    def battle_render_target(self):
        """`TacticalBattle` a dibujar (o None si todavía solo se está votando)."""
        if self.active_battle is not None:
            return self.active_battle.battle
        if self.client_battle is not None:
            return self.client_battle.battle
        return None

    def battle_phase(self) -> str:
        if self.active_battle is not None:
            return self.active_battle.phase
        if self.client_battle is not None:
            return self.client_battle.phase
        return ""

    def battle_countdown(self) -> float:
        if self.active_battle is not None:
            return self.active_battle.countdown
        if self.client_battle is not None:
            return self.client_battle.countdown
        return 0.0

    def battle_human_owners(self) -> list[int]:
        if self.active_battle is not None:
            return self.active_battle.human_owners
        if self.client_battle is not None:
            return self.client_battle.human_owners
        return []

    def battle_arrows(self) -> list:
        """Flechas de arquero a dibujar este frame (solo lado cliente)."""
        if self.client_battle is not None:
            arrows, self.client_battle.arrows = self.client_battle.arrows, []
            return arrows
        return []

    def battle_interpolate(self, dt: float) -> None:
        """Suaviza el movimiento del espejo del cliente entre snapshots. En el
        host (sim vivo a ritmo de frame) es no-op."""
        if self.client_battle is not None:
            self.client_battle.interpolate(dt)

    def battle_is_participant(self) -> bool:
        return self.battle_present() and self.human_id in self.battle_human_owners()

    def battle_army_ids(self) -> tuple[int, int] | None:
        if self.active_battle is not None:
            return (self.active_battle.attacker_id, self.active_battle.defender_id)
        if self.client_battle is not None:
            return (self.client_battle.attacker_id, self.client_battle.defender_id)
        return None

    def _current_battle_id(self) -> int | None:
        if self.active_battle is not None:
            return self.active_battle.battle_id
        if self.client_battle is not None:
            return self.client_battle.battle_id
        return None

    def battle_vote(self, zoom: bool) -> None:
        bid = self._current_battle_id()
        if bid is None:
            return
        if self.is_host and self.active_battle is not None:
            self.active_battle.apply_vote(self.human_id, zoom)
        else:
            self.session.send_battle_vote(bid, zoom)

    def battle_command(self, unit_ids, order_kind: str, target) -> None:
        self._battle_input("command", unit_ids=list(unit_ids), order_kind=order_kind, target=target)

    def battle_set_formation(self, formation: str) -> None:
        self._battle_input("formation", formation=formation)

    def battle_ready(self) -> None:
        self._battle_input("ready")

    def _battle_input(self, kind: str, **kw) -> None:
        bid = self._current_battle_id()
        if bid is None:
            return
        if self.is_host and self.active_battle is not None:
            self.active_battle.apply_input(
                self.human_id, kind,
                unit_ids=kw.get("unit_ids"), order_kind=kw.get("order_kind", ""),
                target=kw.get("target"), formation=kw.get("formation", ""),
            )
        else:
            self.session.send_battle_input(
                BattleInput(
                    battle_id=bid, kind=kind,
                    unit_ids=kw.get("unit_ids") or [], order_kind=kw.get("order_kind", ""),
                    target=kw.get("target"), formation=kw.get("formation", ""),
                )
            )

    def send_chat(self, text: str) -> None:
        self.session.send_chat(text)
        # El emisor ve su propio mensaje en el mismo log que los recibidos.
        self.chat_log.append((self.game.players[self.human_id].name, text))

    @property
    def waiting(self) -> bool:
        return self.phase is Phase.WAITING

    # --- internos ----------------------------------------------------------

    def _handle(self, event) -> None:
        if isinstance(event, OrdersReceived):  # solo host
            self._client_orders[event.turn][event.player_id] = self._validate_owned(
                decode_orders(event.orders), event.player_id
            )
        elif isinstance(event, TurnReady):  # solo cliente
            self._run_bundle(event.turn, event.bundle)
        elif isinstance(event, HashReceived):  # solo host
            self._peer_hashes[event.turn][event.player_id] = event.digest
            self._check_hash(event.turn, event.player_id)
        elif isinstance(event, ChatReceived):
            self.chat_log.append((event.name, event.text))
        elif isinstance(event, StateSyncReceived):  # solo cliente
            self._apply_state_sync(event.state)
        elif isinstance(event, BattleVoteReceived):  # solo host
            if self.active_battle is not None:
                self.active_battle.apply_vote(event.player_id, event.message.zoom)
        elif isinstance(event, BattleInputReceived):  # solo host
            self._apply_remote_input(event.player_id, event.message)
        elif isinstance(event, BattleOfferReceived):  # solo cliente
            self._on_battle_offer(event.message)
        elif isinstance(event, BattleBeginReceived):  # solo cliente
            self._on_battle_begin(event.message)
        elif isinstance(event, BattleSnapshotReceived):  # solo cliente
            self._on_battle_snapshot(event.message)
        elif isinstance(event, BattleEndReceived):  # solo cliente
            self._on_battle_end(event.message)
        elif isinstance(event, PlayerLeft):  # solo host
            self._on_player_left(event.player_id, event.reason)
        elif isinstance(event, PlayerRejoined):  # solo host
            self._on_player_rejoined(event.player_id, event.name)
        elif isinstance(event, Disconnected):
            self.disconnected = True
            self.disconnect_reason = event.reason
            self.phase = Phase.ENDED

    def _on_player_left(self, player_id: int, reason: str) -> None:
        """Host: un rival se cayó. La IA lo toma; se descartan sus órdenes
        pendientes para que no se dupliquen con las de la IA."""
        self._absent.add(player_id)
        for orders in self._client_orders.values():
            orders.pop(player_id, None)
        if self.ai_factory is not None and player_id not in self._ai:
            self._ai[player_id] = self.ai_factory(player_id)
        # Si el caído está dirigiendo la batalla en curso, se auto-resuelve.
        if self.active_battle is not None:
            self.active_battle.mark_absent(player_id)
        name = self._player_name(player_id)
        self._system_chat(f"{name} se desconectó; lo controla la IA")

    def _on_player_rejoined(self, player_id: int, name: str) -> None:
        """Host: el rival volvió y retoma el control de su jugador."""
        self._absent.discard(player_id)
        self._system_chat(f"{name} volvió a la partida")

    def _player_name(self, player_id: int) -> str:
        player = next((p for p in self.game.players if p.id == player_id), None)
        return player.name if player is not None else f"Jugador {player_id}"

    def _system_chat(self, text: str) -> None:
        self.chat_log.append(("Sistema", text))
        if self.is_host and hasattr(self.session, "send_system_chat"):
            self.session.send_system_chat(text)

    def _try_resolve_host(self) -> None:
        """Host: si ya tiene las órdenes propias y las de todos los rivales para
        el turno en curso, arma el bundle, lo reparte y empieza a resolver."""
        if self._resolving or self.active_battle is not None:
            return  # ya estamos resolviendo este turno (quizás dirigiendo una batalla)
        if self.phase is not Phase.WAITING or self._local_orders is None:
            return
        client_orders = self._client_orders.get(self._turn, {})
        present_clients = (self.n_players - 1) - len(self._absent)
        if len(client_orders) < present_clients:
            return
        bundle = {self.human_id: self._local_orders, **client_orders}
        for player_id in self._absent:  # la IA cubre a los jugadores caídos
            bundle[player_id] = self._ai_orders(player_id)
        self.session.broadcast_turn_orders(
            self._turn, {pid: encode_orders(od) for pid, od in bundle.items()}
        )
        self._begin_resolution(self._turn, bundle)

    def _ai_orders(self, player_id: int) -> list[Order]:
        """Órdenes de la IA para un jugador ausente (solo en el host)."""
        controller = self._ai.get(player_id)
        return controller.decide_orders(self.game) if controller is not None else []

    def _run_bundle(self, turn: int, bundle: dict) -> None:
        """Cliente: corre el conjunto completo de órdenes que repartió el host."""
        if self.phase is Phase.ENDED or turn != self._turn or self._resolving:
            return
        decoded = {int(pid): decode_orders(od) for pid, od in bundle.items()}
        self._begin_resolution(turn, decoded)

    # --- resolución del turno con cola de batallas ------------------------

    def _begin_resolution(self, turn: int, bundle: dict[int, list[Order]]) -> None:
        """Aplica órdenes+movimiento y empieza a recorrer la cola de batallas.

        En modo `off` resuelve todo de corrido (equivale a `run_turn`); con zoom,
        se pausa en cada batalla con humanos hasta que la autoridad la resuelve."""
        combined = canonical_order([o for orders in bundle.values() for o in orders])
        self._resolving = True
        self._turn_being_resolved = turn
        self._pre_turn = [a.to_dict() for a in self.game.armies]
        self._battle_queue = self.game.begin_turn(combined)
        self._battle_idx = 0
        self._advance_battles()

    def _advance_battles(self) -> None:
        """Recorre la cola; pausa en la primera batalla con zoom pendiente."""
        while self._battle_idx < len(self._battle_queue):
            attacker_id, defender_id = self._battle_queue[self._battle_idx]
            if self.tactical_mode == TACTICAL_OFF:
                self.game.resolve_one_battle(attacker_id, defender_id)
                self._battle_idx += 1
                continue
            # Con zoom habilitado, cada batalla necesita la decisión de la
            # autoridad (dirigir o auto) para mantener el lockstep.
            if self.is_host:
                if self._start_authority_battle(attacker_id, defender_id):
                    return  # batalla dirigida en curso: pausa la cola
                self._battle_idx += 1
                continue
            else:
                self._awaiting_battle = self._battle_idx
                return  # cliente: espera los mensajes de la autoridad
        self._finish_resolution()

    def _finish_resolution(self) -> None:
        result = self.game.finish_turn()
        resolved_turn = self.game.turn  # ya incrementado por finish_turn
        digest = state_digest(self.game)
        self._local_hashes[resolved_turn] = digest
        if not self.is_host:
            self.session.submit_hash(resolved_turn, digest)
        self._resolved = (result, self._pre_turn)
        self._client_orders.pop(self._turn_being_resolved, None)
        self._local_orders = None
        self._turn = self.game.turn
        self._resolving = False
        self._battle_queue = []
        self._awaiting_battle = None
        self.phase = Phase.ENDED if result.is_over else Phase.COLLECTING
        if self.is_host:
            # Hashes de clientes que pudieron llegar antes de terminar el turno.
            for player_id in list(self._peer_hashes.get(resolved_turn, {})):
                self._check_hash(resolved_turn, player_id)

    # --- batalla dirigida: lado autoridad (host) --------------------------

    def _start_authority_battle(self, attacker_id: int, defender_id: int) -> bool:
        """Arranca una batalla con zoom como autoridad. Devuelve True si quedó una
        batalla dirigida en curso (la cola se pausa); False si se auto-resolvió."""
        attacker = self.game.army_by_id(attacker_id)
        defender = self.game.army_by_id(defender_id)
        if attacker is None or defender is None:
            self.game.resolve_one_battle(attacker_id, defender_id)
            return False
        humans = self._present_human_owners(attacker.owner, defender.owner)
        if not humans:
            # Sin humanos presentes (ambos ausentes/IA): auto, pero igual avisamos
            # a los clientes para que sincronicen la decisión por esta batalla.
            self.session.broadcast_battle(BattleEnd(battle_id=self._battle_idx, result={}))
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
            seed=self._battle_seed(),
        )
        for msg in self.active_battle.drain():
            self.session.broadcast_battle(msg)
        self._last_tick = self._clock()  # arranca el reloj del combate
        return True

    def _finish_authority_battle(self) -> None:
        """La batalla dirigida terminó: aplica el resultado y sigue la cola."""
        driver = self.active_battle
        attacker_id, defender_id = self._battle_queue[self._battle_idx]
        self.game.resolve_one_battle(attacker_id, defender_id, result=driver.result)
        self.active_battle = None
        self._battle_idx += 1
        self._advance_battles()

    def _apply_remote_input(self, player_id: int, msg: BattleInput) -> None:
        if self.active_battle is None:
            return
        self.active_battle.apply_input(
            player_id,
            msg.kind,
            unit_ids=msg.unit_ids,
            order_kind=msg.order_kind,
            target=msg.target,
            formation=msg.formation,
        )

    def _present_human_owners(self, *owners: int) -> list[int]:
        """Dueños humanos presentes entre los dados (sin los ausentes/IA)."""
        return [o for o in dict.fromkeys(owners) if o not in self._absent]

    def _battle_seed(self) -> int:
        return self._turn_being_resolved * 1000 + self._battle_idx

    # --- batalla dirigida: lado cliente -----------------------------------

    def _on_battle_offer(self, msg) -> None:
        if self._awaiting_battle != msg.battle_id:
            return
        self.client_battle = ClientBattle(
            None, msg.battle_id, msg.attacker_id, msg.defender_id, msg.human_owners
        )

    def _on_battle_begin(self, msg) -> None:
        if self._awaiting_battle != msg.battle_id:
            return
        attacker = self.game.army_by_id(msg.attacker_id)
        defender = self.game.army_by_id(msg.defender_id)
        battle = build_tactical_battle(
            attacker, defender, self.game.world, self.game.classes,
            self.game.config["batalla"], random.Random(msg.seed),
        )
        self.client_battle = ClientBattle(
            battle, msg.battle_id, msg.attacker_id, msg.defender_id, msg.human_owners
        )
        self.client_battle.phase = "prep"

    def _on_battle_snapshot(self, msg) -> None:
        if self.client_battle is not None and self._awaiting_battle == msg.battle_id:
            self.client_battle.apply_snapshot(msg)

    def _on_battle_end(self, msg) -> None:
        if self._awaiting_battle != msg.battle_id:
            return
        attacker_id, defender_id = self._battle_queue[self._battle_idx]
        result = decode_result(msg.result) if msg.result else None
        self.game.resolve_one_battle(attacker_id, defender_id, result=result)
        self.client_battle = None
        self._awaiting_battle = None
        self._battle_idx += 1
        self._advance_battles()

    def _validate_owned(self, orders: list[Order], player_id: int) -> list[Order]:
        """Descarta órdenes que afecten ejércitos/fuertes que no son del jugador
        indicado (defensa ante un cliente con bug o malicioso)."""
        return [o for o in orders if self._owned_by(o, player_id)]

    def _owned_by(self, order: Order, player_id: int) -> bool:
        if isinstance(order, CreateArmyOrder):
            fort = self.game.world.fort_at(order.position)
            return fort is not None and fort.owner == player_id
        if isinstance(order, MoveOrder):
            return self._army_owner(order.army_id) == player_id
        if isinstance(order, SplitArmyOrder):
            return self._army_owner(order.source_id) == player_id
        # Merge / Transfer: ambos extremos deben ser del jugador.
        return (
            self._army_owner(order.source_id) == player_id
            and self._army_owner(order.target_id) == player_id
        )

    def _army_owner(self, army_id: int) -> int | None:
        army = self.game.army_by_id(army_id)
        return army.owner if army is not None else None

    def _check_hash(self, turn: int, player_id: int) -> None:
        local = self._local_hashes.get(turn)
        peer = self._peer_hashes.get(turn, {}).get(player_id)
        if local is not None and peer is not None and local != peer:
            self.desync = True
            # El host es la autoridad: reenvía su estado completo al cliente que
            # divergió (red de seguridad ante un bug raro de determinismo). Uno
            # solo por (turno, cliente), para no spamear.
            key = (turn, player_id)
            if self.is_host and key not in self._sync_sent:
                self._sync_sent.add(key)
                self.session.send_state_sync(player_id, self.game.turn, self.game.to_dict())

    def _apply_state_sync(self, state: dict) -> None:
        """Cliente: adopta el estado autoritativo del host y reanuda limpio."""
        if self.is_host:
            return  # el host no se resincroniza con nadie
        self.game.load_state(state)
        self._turn = self.game.turn
        self._local_orders = None
        self._client_orders.clear()
        self.desync = False
        self.resynced = True
        self.phase = Phase.COLLECTING
