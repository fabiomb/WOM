"""Driver del zoom de batalla en red (lado autoridad), puro y sin pygame.

La **autoridad** (host LAN = `NetGame` con `is_host`; servidor = `MatchRunner`)
es la única que corre la simulación táctica (`TacticalBattle.step`). Este módulo
encapsula el ciclo de una batalla en red para reusarlo entre ambas:

    voting → prep (cuenta regresiva) → fighting → done

- `voting` (solo modo `"agree"`): cada humano del combate vota dirigir o no; el
  zoom se abre solo si TODOS aceptan. En `"always"` arranca directo en `prep`.
- `prep`: cuenta regresiva; las fichas humanas empiezan en "aguantar". El humano
  elige formación y marca "listo"; al estar todos listos (o al llegar a 0)
  empieza el combate.
- `fighting`: la `TacticalAI` conduce los bandos no-humanos (ausentes/IA) y el
  input de los humanos llega por `apply_input`. Al terminar la sim, se calcula el
  `BattleResult`.
- `done`: terminó. `auto=True` ⇒ la batalla se resuelve con el RNG determinista
  del core (`resolve_one_battle` sin `result`); `auto=False` ⇒ se aplica
  `result` (el del combate dirigido).

El driver **produce mensajes del protocolo** para que la autoridad los difunda
(`drain()`), y expone su estado para que el host renderice su propia pantalla sin
ida y vuelta de red. Mantiene su propio RNG (no toca `Game.rng`), igual que el
zoom de single-player, así el camino determinista del core queda intacto.

Sin pygame a propósito (entra en el smoke test de `wom/net`): usa solo el modelo
puro (`tactical`, `tactical_ai`, `formations`, `battle`).
"""

from __future__ import annotations

import random

from wom.ai.tactical_ai import TacticalAI
from wom.core.battle import BattleOutcome, BattleResult
from wom.core.tactical import build_tactical_battle
from wom.net.protocol import (
    BattleBegin,
    BattleEnd,
    BattleOffer,
    BattleSnapshot,
)

# Fases del driver (strings, viajan en el snapshot).
VOTING = "voting"
PREP = "prep"
FIGHTING = "fighting"
DONE = "done"

PREP_SECONDS = 10.0       # cuenta regresiva de preparación (igual que el zoom SP)
VOTE_TIMEOUT = 25.0       # si un humano no vota en este tiempo, cuenta como "no"
SNAPSHOT_INTERVAL = 1 / 30  # ~30 Hz de snapshots a los clientes (el cliente
                            # además interpola entre snapshots para que el
                            # movimiento sea fluido; ver ClientBattle.interpolate)
DEFAULT_AI_LEVEL = "medio"  # nivel táctico para un bando ausente/IA


def encode_result(result: BattleResult) -> dict:
    """`BattleResult` → dict JSON-serializable (para `BattleEnd`)."""
    return {
        "outcome": result.outcome.name,
        "attacker_losses": dict(result.attacker_losses),
        "defender_losses": dict(result.defender_losses),
        "attacker_xp_delta": result.attacker_xp_delta,
        "defender_xp_delta": result.defender_xp_delta,
    }


def decode_result(data: dict) -> BattleResult:
    """dict → `BattleResult` (inversa de `encode_result`)."""
    return BattleResult(
        outcome=BattleOutcome[data["outcome"]],
        attacker_losses={k: int(v) for k, v in data["attacker_losses"].items()},
        defender_losses={k: int(v) for k, v in data["defender_losses"].items()},
        attacker_xp_delta=int(data["attacker_xp_delta"]),
        defender_xp_delta=int(data["defender_xp_delta"]),
    )


class NetBattleDriver:
    """Conduce una batalla del zoom en red desde la autoridad."""

    def __init__(
        self,
        *,
        battle_id: int,
        attacker,
        defender,
        world,
        classes,
        battle_config: dict,
        mode: str,            # "agree" | "always"
        human_owners: list[int],
        seed: int,
        ai_levels: dict[int, str] | None = None,
    ) -> None:
        self.battle_id = battle_id
        self.attacker_id = attacker.id
        self.defender_id = defender.id
        self.attacker_owner = attacker.owner
        self.defender_owner = defender.owner
        self.mode = mode
        self.human_owners = list(human_owners)
        self.seed = seed

        self.battle = build_tactical_battle(
            attacker, defender, world, classes, battle_config, random.Random(seed)
        )
        # Bandos no-humanos del combate (ausentes / IA): los conduce TacticalAI.
        ai_levels = ai_levels or {}
        self._ais: list[TacticalAI] = []
        for owner in (self.attacker_owner, self.defender_owner):
            if owner not in self.human_owners:
                ai = TacticalAI.for_level(owner, ai_levels.get(owner, DEFAULT_AI_LEVEL))
                ai.plan_formation(self.battle)
                self._ais.append(ai)
        # Las fichas humanas arrancan en "aguantar" (no atacan hasta la orden),
        # igual que en la preparación del zoom single-player.
        for owner in self.human_owners:
            self.battle.command(
                [u.id for u in self.battle.units if u.owner == owner], "hold"
            )

        self.phase = VOTING if mode == "agree" else PREP
        self.countdown = PREP_SECONDS
        self._vote_clock = 0.0
        self._votes: dict[int, bool] = {}
        self._ready: set[int] = set()
        self._snapshot_clock = SNAPSHOT_INTERVAL  # primer snapshot ya
        self._arrow_buf: list[list[int]] = []

        self.finished = False
        self.auto = False
        self.result: BattleResult | None = None
        self.zoomed = mode == "always"  # ya se difundió/difundirá un BattleBegin

        self._out: list = []
        if mode == "always":
            self._out.append(self._begin_msg())
        else:
            self._out.append(
                BattleOffer(
                    battle_id=battle_id,
                    attacker_id=self.attacker_id,
                    defender_id=self.defender_id,
                    mode=mode,
                    human_owners=list(self.human_owners),
                )
            )

    # --- API que usa la autoridad ----------------------------------------

    def drain(self) -> list:
        """Mensajes a difundir acumulados desde la última llamada."""
        out, self._out = self._out, []
        return out

    def apply_vote(self, owner: int, zoom: bool) -> None:
        if self.phase is not VOTING or owner not in self.human_owners:
            return
        self._votes[owner] = bool(zoom)
        if len(self._votes) >= len(self.human_owners):
            self._resolve_vote()

    def apply_input(
        self,
        owner: int,
        kind: str,
        *,
        unit_ids: list[int] | None = None,
        order_kind: str = "",
        target=None,
        formation: str = "",
    ) -> None:
        if self.finished or owner not in self.human_owners:
            return
        if kind == "formation":
            if self.phase is PREP and formation:
                self.battle.set_formation(owner, formation)
        elif kind == "ready":
            self._ready.add(owner)
            if self.phase is PREP and self._all_ready():
                self._start_fight()
        elif kind == "command":
            ids = [uid for uid in (unit_ids or []) if self._owns_unit(owner, uid)]
            if ids and order_kind:
                self.battle.command(ids, order_kind, target)

    def mark_absent(self, owner: int) -> None:
        """Un humano del combate se cayó: se auto-resuelve (requisito 6)."""
        if not self.finished and owner in self.human_owners:
            self._finish_auto()

    def tick(self, dt: float) -> None:
        if self.finished:
            return
        if self.phase is VOTING:
            self._vote_clock += dt
            if self._vote_clock >= VOTE_TIMEOUT:
                self._resolve_vote()  # los que no votaron cuentan como "no"
            return
        if self.phase is PREP:
            self.countdown = max(0.0, self.countdown - min(dt, 0.2))
            self._tick_snapshot(dt)
            if self.countdown <= 0.0:
                self._start_fight()
            return
        if self.phase is FIGHTING:
            step_dt = min(dt, 0.05)
            for ai in self._ais:
                ai.update(self.battle, step_dt)
            self.battle.step(step_dt)
            self._collect_arrows()
            self._tick_snapshot(dt)
            if self.battle.finished:
                self._finish_zoom()

    # --- estado para el render del host ----------------------------------

    def snapshot_units(self) -> list:
        return [
            [u.id, round(u.x, 3), round(u.y, 3), round(u.hp, 3), int(u.fled)]
            for u in self.battle.units
        ]

    # --- internos --------------------------------------------------------

    def _all_ready(self) -> bool:
        return all(o in self._ready for o in self.human_owners)

    def _owns_unit(self, owner: int, uid: int) -> bool:
        u = self.battle.unit_by_id(uid)
        return u is not None and u.owner == owner

    def _resolve_vote(self) -> None:
        # Zoom solo si TODOS los humanos votaron que sí (los que no votaron, no).
        if all(self._votes.get(o, False) for o in self.human_owners):
            self.phase = PREP
            self.zoomed = True
            self._out.append(self._begin_msg())
            self._snapshot_clock = SNAPSHOT_INTERVAL
        else:
            self._finish_auto()

    def _start_fight(self) -> None:
        self.phase = FIGHTING
        self.countdown = 0.0

    def _begin_msg(self) -> BattleBegin:
        return BattleBegin(
            battle_id=self.battle_id,
            attacker_id=self.attacker_id,
            defender_id=self.defender_id,
            human_owners=list(self.human_owners),
            seed=self.seed,
        )

    def _collect_arrows(self) -> None:
        """Junta los disparos de arquero de este step (para el snapshot)."""
        classes = self.battle.classes
        for attacker_id, target_id in self.battle.attack_events:
            atk = self.battle.unit_by_id(attacker_id)
            tgt = self.battle.unit_by_id(target_id)
            if atk is None or tgt is None:
                continue
            if not classes[atk.class_id].ignora_bonus_fort:
                continue  # solo los arqueros disparan flechas
            self._arrow_buf.append(
                [round(atk.x, 2), round(atk.y, 2), round(tgt.x, 2), round(tgt.y, 2)]
            )

    def _tick_snapshot(self, dt: float) -> None:
        self._snapshot_clock += dt
        if self._snapshot_clock < SNAPSHOT_INTERVAL:
            return
        self._snapshot_clock = 0.0
        self._out.append(
            BattleSnapshot(
                battle_id=self.battle_id,
                phase=self.phase,
                countdown=round(self.countdown, 2),
                units=self.snapshot_units(),
                arrows=self._arrow_buf,
            )
        )
        self._arrow_buf = []

    def _finish_zoom(self) -> None:
        self.result = self.battle.to_battle_result()
        self.auto = False
        self._finish()

    def _finish_auto(self) -> None:
        self.result = None
        self.auto = True
        self._finish()

    def _finish(self) -> None:
        self.finished = True
        self.phase = DONE
        self._out.append(
            BattleEnd(
                battle_id=self.battle_id,
                result={} if self.auto else encode_result(self.result),
            )
        )
