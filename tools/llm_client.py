"""Cliente de red headless: conecta un LLM a una partida multijugador de WOM.

El host (un humano) crea la partida desde el menú **Multijugador → Crear** y
espera conexiones. Este script se conecta como cliente (jugador 1), pasa el
lobby, y en cada turno deja que un LLM decida las órdenes — reutiliza toda la
infraestructura de red (`ClientSession` + `NetGame`, lockstep determinista); el
LLM solo reemplaza al humano que arma las órdenes.

Ejemplos:

    # Gemma3 local por Ollama (sin API key)
    python tools/llm_client.py --provider ollama --model gemma3 --name Gemma

    # Conectando a otra máquina de la LAN
    python tools/llm_client.py --host 192.168.1.20 --port 50000 \
        --provider ollama --model gemma3

    # Gemini / Anthropic / OpenAI (la key se toma del entorno si no se pasa)
    python tools/llm_client.py --provider gemini   --model gemini-2.5-flash --name Gemini
    python tools/llm_client.py --provider anthropic --model claude-opus-4-8 --name Claude

Requiere el root del proyecto en PYTHONPATH (se agrega solo abajo).
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wom.core.game import Game  # noqa: E402
from wom.llm.agent import LLMPlayer  # noqa: E402
from wom.llm.backend import BackendConfig, LLMError, make_backend  # noqa: E402
from wom.net.lockstep import NetGame, Phase  # noqa: E402
from wom.net.session import (  # noqa: E402
    ClientSession,
    Connected,
    Disconnected,
    GameReady,
    ReadyChanged,
    Rejected,
    Started,
)
from wom.net.transport import DEFAULT_PORT, connect  # noqa: E402

HUMAN_ID = 1  # el cliente siempre es el jugador 1 (el host es el 0)
_POLL = 0.05  # segundos entre vueltas del loop cuando no hay nada que hacer


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Cliente LLM para WOM multijugador")
    p.add_argument("--host", default="127.0.0.1", help="IP del host (default localhost)")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--name", default="LLM", help="nombre del jugador")
    p.add_argument(
        "--provider",
        default="ollama",
        help="ollama | lmstudio | openai | gemini | anthropic",
    )
    p.add_argument("--model", default="gemma3", help="nombre del modelo")
    p.add_argument("--base-url", default=None, help="URL base (default según proveedor)")
    p.add_argument("--api-key", default=None, help="API key (default: variable de entorno)")
    p.add_argument("--temperature", type=float, default=0.3)
    p.add_argument("--timeout", type=float, default=120.0, help="timeout HTTP por llamada")
    p.add_argument(
        "--thinking",
        action="store_true",
        help="Anthropic: activa el razonamiento extendido (adaptive thinking)",
    )
    p.add_argument(
        "--effort",
        default=None,
        help="Anthropic con --thinking: nivel de esfuerzo (low|medium|high|xhigh|max)",
    )
    p.add_argument("--debug", action="store_true", help="loguea órdenes y descartes")
    return p.parse_args(argv)


def build_player(args: argparse.Namespace) -> LLMPlayer:
    config = BackendConfig(
        provider=args.provider,
        model=args.model,
        base_url=args.base_url,
        api_key=args.api_key,
        temperature=args.temperature,
        timeout=args.timeout,
        thinking=args.thinking,
        effort=args.effort,
    )
    backend = make_backend(config)
    print(f"Backend: {backend.describe()}")
    return LLMPlayer(HUMAN_ID, backend, debug=args.debug)


def run_lobby(session: ClientSession) -> Game | None:
    """Maneja handshake + lobby. Devuelve el `Game` inicial al arrancar, o None."""
    setup = None
    while True:
        for event in session.update():
            if isinstance(event, Connected):
                print(f"Conectado al host «{event.peer_name}». Esperando partida…")
            elif isinstance(event, Rejected):
                print(f"Rechazado: {event.reason}")
                return None
            elif isinstance(event, GameReady):
                setup = event.setup
                session.set_ready(True)
                print("Partida recibida. Marqué «Listo», esperando al host…")
            elif isinstance(event, ReadyChanged):
                print(f"El host {'está listo' if event.ready else 'canceló su listo'}.")
            elif isinstance(event, Started):
                print("¡Arranca la partida!")
                return Game.from_dict(setup.state)
            elif isinstance(event, Disconnected):
                print(f"Desconectado: {event.reason}")
                return None
        time.sleep(_POLL)


def play(session: ClientSession, game: Game, player: LLMPlayer, peer_name: str) -> None:
    """Bucle de juego: lockstep con el LLM aportando las órdenes del turno."""
    net = NetGame(session, game, human_id=HUMAN_ID, is_host=False, peer_name=peer_name)
    chat_seen = 0
    while net.phase is not Phase.ENDED:
        net.update()

        # Eco del chat recibido.
        while chat_seen < len(net.chat_log):
            who, text = net.chat_log[chat_seen]
            print(f"  💬 {who}: {text}")
            chat_seen += 1

        if net.phase is Phase.COLLECTING:
            print(f"\n── Turno {net.game.turn}: pensando… ──")
            t0 = time.time()
            orders = player.decide_orders(net.game)
            print(f"  → {len(orders)} órdenes ({time.time() - t0:.1f}s). Esperando al rival…")
            net.submit_local_orders(orders)

        resolved = net.consume_resolved()
        if resolved is not None:
            result, _ = resolved
            if net.desync:
                print("  ⚠ divergencia de estado detectada (resync por el host).")
            if result.is_over:
                _announce_end(result)
        time.sleep(_POLL)

    if net.disconnected:
        print(f"\nPartida terminada: {net.disconnect_reason}")


def _announce_end(result) -> None:
    winner = getattr(result, "winner", None)
    if winner == HUMAN_ID:
        print("\n🏆 El LLM ganó la partida.")
    elif winner is None:
        print("\n🤝 La partida terminó en empate.")
    else:
        print("\n💀 El LLM perdió la partida.")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        player = build_player(args)
    except LLMError as exc:
        print(f"No se pudo configurar el backend: {exc}")
        return 2

    print(f"Conectando a {args.host}:{args.port} como «{args.name}»…")
    try:
        connection = connect(args.host, args.port)
    except OSError as exc:
        print(f"No se pudo conectar: {exc}")
        return 2

    session = ClientSession(connection, args.name)
    try:
        game = run_lobby(session)
        if game is None:
            return 1
        play(session, game, player, session.peer_name or "host")
    except KeyboardInterrupt:
        print("\nCancelado por el usuario.")
        session.cancel("cliente cerró")
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
