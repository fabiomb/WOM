"""Backends de LLM: una interfaz, varios proveedores, solo stdlib.

`LLMBackend.complete(system, user) -> str` es la única operación. Las
implementaciones hablan la API REST de cada proveedor con `urllib` (sin SDKs ni
dependencias nuevas, coherente con el stack pygame-ce + stdlib):

- `OpenAICompatible`: endpoint estilo OpenAI `/chat/completions`. Cubre **Ollama**
  (`http://localhost:11434/v1`), **LM Studio** (`http://localhost:1234/v1`) y
  **OpenAI** (`https://api.openai.com/v1`) con el mismo código.
- `Gemini`: API de Google `generativelanguage`.
- `Anthropic`: API de Claude (`/v1/messages`).

`make_backend(BackendConfig)` arma el backend según el proveedor, resolviendo
URL base por defecto y la API key desde variable de entorno si no se pasa.
El parseo de la respuesta de cada proveedor está en funciones puras testeables
sin red (`parse_*_response`).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass

# Presets por proveedor: URL base por defecto y variable de entorno de la key.
_PROVIDER_PRESETS = {
    "ollama": ("http://localhost:11434/v1", None),
    "lmstudio": ("http://localhost:1234/v1", None),
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta", "GEMINI_API_KEY"),
    "anthropic": ("https://api.anthropic.com", "ANTHROPIC_API_KEY"),
}
# Variables de entorno alternativas para la key (se prueban en orden).
_ALT_KEY_ENVS = {"gemini": ["GEMINI_API_KEY", "GOOGLE_API_KEY"]}


@dataclass
class BackendConfig:
    """Configuración de un backend (de CLI, env o un JSON de config)."""

    provider: str
    model: str
    base_url: str | None = None
    api_key: str | None = None
    temperature: float = 0.3
    max_tokens: int = 2048
    timeout: float = 120.0
    # Razonamiento extendido (solo Anthropic por ahora): activa "adaptive
    # thinking" y, opcionalmente, el nivel de esfuerzo
    # (low|medium|high|xhigh|max). Pensado para exprimir modelos como Opus 4.8.
    thinking: bool = False
    effort: str | None = None


# Familias de modelos Anthropic que RECHAZAN los parámetros de sampling
# (temperature/top_p/top_k) y el thinking con budget_tokens: hay que omitirlos
# o la API devuelve 400. Opus 4.7/4.8, Fable 5, Mythos 5.
_ANTHROPIC_NO_SAMPLING = ("claude-opus-4-7", "claude-opus-4-8", "claude-fable", "claude-mythos")


def _anthropic_rejects_sampling(model: str) -> bool:
    return any(model.startswith(prefix) for prefix in _ANTHROPIC_NO_SAMPLING)


class LLMError(RuntimeError):
    """Falla al hablar con el LLM (red, HTTP, respuesta inesperada)."""


class LLMBackend(ABC):
    """Interfaz mínima: dado un prompt de sistema y uno de usuario, texto."""

    @abstractmethod
    def complete(self, system: str, user: str) -> str:
        """Devuelve la respuesta del modelo como texto (sin streaming)."""

    def describe(self) -> str:
        return self.__class__.__name__


# --- helper HTTP -----------------------------------------------------------


def _post_json(url: str, payload: dict, headers: dict, timeout: float) -> dict:
    """POST JSON → dict de respuesta. Traduce errores de red a `LLMError`."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    for key, value in headers.items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        raise LLMError(f"HTTP {exc.code} de {url}: {body[:500]}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise LLMError(f"no se pudo contactar {url}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise LLMError(f"respuesta no-JSON de {url}: {exc}") from exc


# --- adapters --------------------------------------------------------------


class OpenAICompatible(LLMBackend):
    """Chat completions estilo OpenAI (Ollama, LM Studio, OpenAI)."""

    def __init__(self, config: BackendConfig) -> None:
        self.config = config
        self.base_url = (config.base_url or "").rstrip("/")

    def complete(self, system: str, user: str) -> str:
        payload = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.config.temperature,
            "stream": False,
        }
        headers = {}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        data = _post_json(
            f"{self.base_url}/chat/completions", payload, headers, self.config.timeout
        )
        return parse_openai_response(data)

    def describe(self) -> str:
        return f"OpenAI-compatible {self.config.model} @ {self.base_url}"


class Gemini(LLMBackend):
    """API generateContent de Google Gemini."""

    def __init__(self, config: BackendConfig) -> None:
        self.config = config
        self.base_url = (config.base_url or _PROVIDER_PRESETS["gemini"][0]).rstrip("/")

    def complete(self, system: str, user: str) -> str:
        if not self.config.api_key:
            raise LLMError("Gemini requiere una API key (GEMINI_API_KEY)")
        url = (
            f"{self.base_url}/models/{self.config.model}:generateContent"
            f"?key={self.config.api_key}"
        )
        payload = {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"temperature": self.config.temperature},
        }
        data = _post_json(url, payload, {}, self.config.timeout)
        return parse_gemini_response(data)

    def describe(self) -> str:
        return f"Gemini {self.config.model}"


class Anthropic(LLMBackend):
    """API Messages de Anthropic (Claude)."""

    def __init__(self, config: BackendConfig) -> None:
        self.config = config
        self.base_url = (config.base_url or _PROVIDER_PRESETS["anthropic"][0]).rstrip("/")

    def complete(self, system: str, user: str) -> str:
        if not self.config.api_key:
            raise LLMError("Anthropic requiere una API key (ANTHROPIC_API_KEY)")
        payload = self.build_payload(system, user)
        headers = {
            "x-api-key": self.config.api_key,
            "anthropic-version": "2023-06-01",
        }
        data = _post_json(
            f"{self.base_url}/v1/messages", payload, headers, self.config.timeout
        )
        return parse_anthropic_response(data)

    def build_payload(self, system: str, user: str) -> dict:
        """Arma el cuerpo del request (puro y testeable, sin red).

        En modelos que rechazan sampling (Opus 4.7/4.8, Fable 5) se omite
        `temperature`. Con `thinking`, activa "adaptive thinking" (+ `effort`)
        y da más headroom de `max_tokens` para que el razonamiento no trunque
        el JSON de salida.
        """
        cfg = self.config
        max_tokens = cfg.max_tokens
        payload: dict = {
            "model": cfg.model,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        if cfg.thinking:
            payload["thinking"] = {"type": "adaptive"}
            if cfg.effort:
                payload["output_config"] = {"effort": cfg.effort}
            max_tokens = max(max_tokens, 16000)  # el pensamiento cuenta para max_tokens
        elif not _anthropic_rejects_sampling(cfg.model):
            payload["temperature"] = cfg.temperature
        payload["max_tokens"] = max_tokens
        return payload

    def describe(self) -> str:
        return f"Anthropic {self.config.model}"


# --- parseo de respuestas (puro, testeable sin red) ------------------------


def parse_openai_response(data: dict) -> str:
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"respuesta OpenAI inesperada: {data!r}") from exc


def parse_gemini_response(data: dict) -> str:
    try:
        parts = data["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts)
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"respuesta Gemini inesperada: {data!r}") from exc


def parse_anthropic_response(data: dict) -> str:
    try:
        blocks = data["content"]
        return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"respuesta Anthropic inesperada: {data!r}") from exc


# --- fábrica ---------------------------------------------------------------


def make_backend(config: BackendConfig) -> LLMBackend:
    """Construye el backend según el proveedor, completando defaults.

    Resuelve la URL base por defecto del proveedor y, si no se pasó `api_key`,
    la toma de la variable de entorno correspondiente.
    """
    provider = config.provider.lower().strip()
    if provider not in _PROVIDER_PRESETS:
        raise LLMError(
            f"proveedor desconocido: {provider!r} "
            f"(opciones: {', '.join(sorted(_PROVIDER_PRESETS))})"
        )
    default_url, _ = _PROVIDER_PRESETS[provider]
    if config.base_url is None:
        config.base_url = default_url
    if config.api_key is None:
        config.api_key = _resolve_api_key(provider)

    if provider in ("ollama", "lmstudio", "openai"):
        return OpenAICompatible(config)
    if provider == "gemini":
        return Gemini(config)
    return Anthropic(config)


def _resolve_api_key(provider: str) -> str | None:
    envs = _ALT_KEY_ENVS.get(provider)
    if envs is None:
        env = _PROVIDER_PRESETS[provider][1]
        envs = [env] if env else []
    for name in envs:
        value = os.environ.get(name)
        if value:
            return value
    return None
