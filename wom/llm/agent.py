"""`LLMPlayer`: un jugador cuyas órdenes las decide un LLM.

Misma firma que `wom.ai.ai_player.AIPlayer` (`decide_orders(game) -> list[Order]`),
así que es intercambiable como rival en red o en un `Game` local. Por turno:

1. Arma el system prompt (reglas) y el prompt de usuario (observación del tablero).
2. Llama al backend y recibe texto.
3. Extrae el arreglo JSON de acciones de forma tolerante (los modelos suelen
   envolverlo en prosa o en bloques ```).
4. Traduce las acciones a `Order` válidas (`actions.translate_actions`).

Si el modelo falla (red, JSON irrecuperable), reintenta una vez con un mensaje
correctivo y, si vuelve a fallar, pasa el turno (lista vacía) en vez de romper la
partida — el lockstep tolera órdenes vacías.
"""

from __future__ import annotations

import json
import re
from typing import Callable

from wom.core.game import Game
from wom.core.orders import Order
from wom.llm.actions import translate_actions
from wom.llm.backend import LLMBackend, LLMError
from wom.llm.observation import render_text
from wom.llm.prompt import system_prompt

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class LLMPlayer:
    """Decide las órdenes de un jugador consultando un LLM."""

    def __init__(
        self,
        player_id: int,
        backend: LLMBackend,
        debug: bool = False,
        logger: Callable[[str], None] | None = None,
    ) -> None:
        self.player_id = player_id
        self.backend = backend
        self.debug = debug
        self._log = logger or (lambda msg: print(f"[LLM p{player_id}] {msg}"))
        self.last_warnings: list[str] = []
        self.last_raw: str = ""

    def decide_orders(self, game: Game) -> list[Order]:
        """Órdenes del turno (igual interfaz que la AI)."""
        system = system_prompt(game, self.player_id)
        user = render_text(game, self.player_id)
        actions = self._query_actions(system, user)
        if actions is None:
            self._log("sin acciones utilizables: paso el turno")
            return []
        orders, warnings = translate_actions(actions, game, self.player_id)
        self.last_warnings = warnings
        if self.debug:
            self._log(f"{len(orders)} órdenes; {len(warnings)} descartadas")
            for w in warnings:
                self._log(f"  descartada: {w}")
        return orders

    def _query_actions(self, system: str, user: str) -> list[dict] | None:
        """Pide acciones al backend; reintenta una vez si el JSON no parsea."""
        prompt = user
        for attempt in (1, 2):
            try:
                raw = self.backend.complete(system, prompt)
            except LLMError as exc:
                self._log(f"error del backend (intento {attempt}): {exc}")
                return None
            self.last_raw = raw
            actions = extract_actions(raw)
            if actions is not None:
                return actions
            if self.debug:
                self._log(f"no pude extraer JSON (intento {attempt}): {raw[:200]!r}")
            prompt = (
                user
                + "\n\nTu respuesta anterior no era un arreglo JSON válido. "
                "Respondé SOLO con el arreglo JSON de acciones, sin texto extra."
            )
        return None


def extract_actions(text: str) -> list[dict] | None:
    """Extrae la lista de acciones del texto del modelo, tolerante al formato.

    Acepta: un arreglo JSON pelado, un objeto `{"actions": [...]}`, un objeto de
    una sola acción, o cualquiera de esos envuelto en un bloque ```json```/prosa.
    Devuelve `None` si no encuentra JSON utilizable.
    """
    if not text:
        return None
    candidates = [text]
    fenced = _FENCE_RE.search(text)
    if fenced:
        candidates.insert(0, fenced.group(1))
    # También probamos el primer bloque [..] o {..} que aparezca.
    for opener, closer in (("[", "]"), ("{", "}")):
        start = text.find(opener)
        end = text.rfind(closer)
        if 0 <= start < end:
            candidates.append(text[start : end + 1])

    for candidate in candidates:
        parsed = _try_json(candidate.strip())
        if parsed is not None:
            return parsed
    return None


def _try_json(blob: str) -> list[dict] | None:
    try:
        data = json.loads(blob)
    except (json.JSONDecodeError, ValueError):
        return None
    return _coerce_actions(data)


def _coerce_actions(data) -> list[dict] | None:
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)]
    if isinstance(data, dict):
        if isinstance(data.get("actions"), list):
            return [d for d in data["actions"] if isinstance(d, dict)]
        if "action" in data:
            return [data]
    return None
