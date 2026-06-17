"""Jugador controlado por un LLM (módulo headless, sin pygame).

Permite que un modelo de lenguaje juegue WOM como un jugador más: observa el
tablero, planifica y emite las mismas `Order` que el humano o la AID. El núcleo
(`LLMPlayer.decide_orders`) tiene la misma firma que `wom.ai.ai_player.AIPlayer`,
así que es intercambiable: sirve como rival en una partida en red
(`tools/llm_client.py`) y como jugador en un `Game` local para benchmarks.

Submódulos:

- `observation` — `Game` → descripción textual compacta del tablero (lo que un
  jugador "ve": mapa, sitios, ejércitos propios y enemigos, comida).
- `actions` — gramática de acciones de alto nivel ↔ `Order`. Traduce intenciones
  ("mover ejército 7 a (12,4)") en órdenes válidas usando el pathfinding del
  core; descarta y reporta las inválidas.
- `prompt` — el system prompt (reglas, clases, formato de salida JSON).
- `backend` — `LLMBackend` (ABC) + adapters: `OpenAICompatible` (Ollama,
  LM Studio, OpenAI), `Gemini`, `Anthropic`. Solo stdlib (urllib), sin SDKs.
- `agent` — `LLMPlayer`: arma el prompt, llama al backend, parsea la respuesta y
  devuelve `list[Order]`.
"""
