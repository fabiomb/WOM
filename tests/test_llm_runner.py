"""`LLMRunner`: el rival LLM embebido juega por loopback contra un host.

Mismo espíritu que `test_llm_net`, pero del lado cliente corre el hilo real del
runner (el que usa la UI en "Jugar contra LLM") con un backend guionado (sin
HTTP): decide órdenes vacías (pasa el turno) y responde el chat. Verifica el
ciclo completo — lobby automático, lockstep en sincronía, respuesta de chat,
estado observable y apagado limpio.
"""

import time

from wom.ai.ai_player import AIPlayer
from wom.core.game import Game, Player
from wom.core.mapgen import MapParams
from wom.core.victory import VictoryMode
from wom.llm.backend import BackendConfig, LLMError, LLMBackend
from wom.llm.runner import LLMRunner, chat_reply_prompt, probe_backend
from wom.net.lockstep import NetGame, Phase
from wom.net.protocol import GameSetup
from wom.net.session import HostSession, SessionState
from wom.net.transport import Server


class ScriptedBackend(LLMBackend):
    """Sin red: pasa el turno ("[]") y contesta el chat con un saludo fijo."""

    def __init__(self, think_seconds: float = 0.0) -> None:
        self.think_seconds = think_seconds
        self.order_calls = 0
        self.chat_calls = 0

    def complete(self, system: str, user: str) -> str:
        if "Chat de la partida" in user:
            self.chat_calls += 1
            return "¡Buena suerte, humano!"
        self.order_calls += 1
        if self.think_seconds:
            time.sleep(self.think_seconds)
        return "[]"


def _host_setup(seed: int = 5):
    """Server + HostSession listos; el provider arma la partida 1v1."""
    server = Server(host="127.0.0.1", port=0, max_clients=1)
    state = {}

    def provider(names):
        players = [Player(i, names[i]) for i in range(2)]
        game = Game.new(MapParams(20, 14, 3, 4, seed), players, VictoryMode.TOTAL)
        state["game"] = game
        return GameSetup(state=game.to_dict(), rules={}, names=list(names), human_id=0)

    host = HostSession(2, "Humano", provider, server=server)
    host.set_ready(True)
    return server, host, state


def _pump_until(host, predicate, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        host.update()
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_runner_juega_y_chatea_en_loopback():
    server, host, state = _host_setup()
    backend = ScriptedBackend(think_seconds=0.4)
    runner = LLMRunner(None, name="Bot", port=server.port, backend=backend)
    runner.start()
    try:
        assert _pump_until(host, lambda: host.state is SessionState.PLAYING), (
            f"no arrancó la partida (runner: {runner.status} {runner.error})"
        )
        game = state["game"]
        net = NetGame(host, game, human_id=0, is_host=True)
        ai = AIPlayer(0, level="facil")

        # Un turno completo: el host manda órdenes de la IA; el runner "piensa".
        net.submit_local_orders(ai.decide_orders(net.game))
        saw_thinking = False
        deadline = time.time() + 5.0
        t0 = game.turn
        while time.time() < deadline and game.turn == t0:
            net.update()
            saw_thinking = saw_thinking or runner.thinking
            time.sleep(0.005)
        assert game.turn > t0, "el turno no se resolvió"
        assert backend.order_calls >= 1
        assert saw_thinking, "nunca se observó el estado 'pensando'"

        # Chat: el humano saluda, el runner responde por el mismo canal.
        # (Solo net.update() bombea la sesión: si no, se robarían los eventos.)
        net.send_chat("hola bot")
        deadline = time.time() + 5.0
        while time.time() < deadline and not any(w == "Bot" for w, _ in net.chat_log):
            net.update()
            time.sleep(0.01)
        assert any(w == "Bot" for w, _ in net.chat_log), (
            f"el runner no respondió el chat: {net.chat_log}"
        )
        assert backend.chat_calls >= 1
    finally:
        runner.stop()
        host.cancel()
        server.close()
    deadline = time.time() + 3.0
    while runner.alive and time.time() < deadline:
        time.sleep(0.01)
    assert not runner.alive, "el hilo del runner no terminó"


def test_runner_no_se_responde_a_si_mismo():
    """Mensajes propios/de sistema no disparan una respuesta del backend."""
    server, host, state = _host_setup(seed=7)
    backend = ScriptedBackend()
    runner = LLMRunner(None, name="Bot", port=server.port, backend=backend)
    runner.start()
    try:
        assert _pump_until(host, lambda: host.state is SessionState.PLAYING)
        game = state["game"]
        net = NetGame(host, game, human_id=0, is_host=True)
        # Sin chat del humano: dejamos correr un rato y no debe haber llamadas.
        deadline = time.time() + 1.0
        while time.time() < deadline:
            net.update()
            time.sleep(0.01)
        assert backend.chat_calls == 0
    finally:
        runner.stop()
        host.cancel()
        server.close()


def test_chat_reply_prompt_incluye_historia_y_nombre():
    system, user = chat_reply_prompt("Gemma", [("Ana", "hola"), ("Gemma", "hola Ana")])
    assert "Gemma" in system
    assert "Ana: hola" in user and "Gemma: hola Ana" in user


def test_probe_backend_falla_con_proveedor_desconocido():
    try:
        probe_backend(BackendConfig(provider="nope", model="x"))
        raise AssertionError("debía fallar")
    except LLMError:
        pass
