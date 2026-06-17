"""Provider-agnostic token estimator for pre-execution budget pre-checks.

Used ONLY to estimate input token consumption BEFORE a model call executes.
NOT used for ledger accounting — provider-reported usage stays the source of truth.

Hierarchy
---------
TokenEstimator  (ABC)
├── TiktokenEstimator  — backs OpenAI, Anthropic (approx), Llama-family (approx)
└── FallbackEstimator  — chars÷4 heuristic, zero external dependencies

Routing
-------
Model name → tiktoken encoding, by longest-prefix match (same pattern as
ledger.py's price_table).  New model families need only a row in _MODEL_ENCODING.

tiktoken is optional.  When not installed, TiktokenEstimator silently degrades
to FallbackEstimator, so the governance layer stays functional and never raises.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod


# ── abstract base ─────────────────────────────────────────────────────────────

class TokenEstimator(ABC):
    @abstractmethod
    def estimate(self, text: str) -> int: ...


# ── concrete backends ─────────────────────────────────────────────────────────

class FallbackEstimator(TokenEstimator):
    """Character-count heuristic: 4 chars ≈ 1 token.  No dependencies, always works."""

    _CHARS_PER_TOKEN = 4

    def estimate(self, text: str) -> int:
        return max(1, len(text) // self._CHARS_PER_TOKEN)


class TiktokenEstimator(TokenEstimator):
    """BPE estimator backed by tiktoken.  Falls back silently on import failure."""

    def __init__(self, encoding_name: str) -> None:
        self._enc = None
        self._fb = FallbackEstimator()
        try:
            import tiktoken
            self._enc = tiktoken.get_encoding(encoding_name)
        except Exception:  # noqa: BLE001 — tiktoken absent or encoding unknown
            pass

    def estimate(self, text: str) -> int:
        if self._enc is None:
            return self._fb.estimate(text)
        try:
            return len(self._enc.encode(text, disallowed_special=()))
        except Exception:  # noqa: BLE001
            return self._fb.estimate(text)


# ── singleton cache (one instance per encoding name) ─────────────────────────

_cache: dict[str, TiktokenEstimator] = {}


def _get(encoding_name: str) -> TiktokenEstimator:
    if encoding_name not in _cache:
        _cache[encoding_name] = TiktokenEstimator(encoding_name)
    return _cache[encoding_name]


# ── model → encoding routing ──────────────────────────────────────────────────
# Checked in definition order; put MORE-SPECIFIC prefixes before shorter ones
# so "gpt-4o" wins over "gpt-4".  Longest matching prefix takes precedence.

_MODEL_ENCODING: list[tuple[str, str]] = [
    # OpenAI family — more-specific prefixes first
    ("gpt-4o",            "o200k_base"),
    ("o1-",               "o200k_base"),
    ("o1",                "o200k_base"),   # bare "o1" (no suffix variant)
    ("o3-",               "o200k_base"),
    ("o4-",               "o200k_base"),
    ("gpt-4",             "cl100k_base"),
    ("gpt-3.5",           "cl100k_base"),
    ("text-embedding-3",  "cl100k_base"),
    ("text-embedding-ada","cl100k_base"),
    ("text-davinci",      "cl100k_base"),  # GPT-3.5 era completion models
    # Anthropic — SentencePiece in reality; cl100k_base is a reasonable approx
    ("claude-",           "cl100k_base"),
    # Google — SentencePiece in reality; cl100k_base is a reasonable approx
    ("gemini",            "cl100k_base"),
    # Cohere
    ("command-r",         "cl100k_base"),
    # Llama / open-weight family — gpt2 vocab is closest available approximation
    ("llama",             "gpt2"),
    ("mistral",           "gpt2"),
    ("mixtral",           "gpt2"),
    ("deepseek",          "gpt2"),
    ("gemma",             "gpt2"),
    ("qwen",              "gpt2"),
    ("phi",               "gpt2"),
    ("falcon",            "gpt2"),
    ("vicuna",            "gpt2"),
    ("yi-",               "gpt2"),
]


def _encoding_for(model: str) -> str:
    """Longest-prefix match → tiktoken encoding name, defaulting to 'gpt2'."""
    m = model.lower()
    best_len = 0
    best_enc = "gpt2"
    for prefix, enc in _MODEL_ENCODING:
        if m.startswith(prefix) and len(prefix) > best_len:
            best_len = len(prefix)
            best_enc = enc
    return best_enc


# ── message extraction helpers ────────────────────────────────────────────────

def _messages_to_text(messages: list) -> str:
    """Flatten a messages array to plain text for token counting."""
    parts: list[str] = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        role = m.get("role") or ""
        content = m.get("content") or ""
        if isinstance(content, list):
            # Multimodal: extract text blocks only (images have no BPE tokens)
            content = " ".join(
                p.get("text", "") for p in content
                if isinstance(p, dict) and p.get("type") == "text"
            )
        parts.append(f"{role}: {content}")
        # tool_calls in assistant messages carry function name + JSON args — tokens
        for tc in (m.get("tool_calls") or []):
            if isinstance(tc, dict):
                fn = tc.get("function") or {}
                parts.append(str(fn.get("name") or ""))
                parts.append(str(fn.get("arguments") or ""))
    return "\n".join(parts)


def _max_output(payload: dict) -> int:
    """Extract the caller's declared output cap, or fall back to OUTPUT_BUFFER env.
    Included in pre-check estimates so projected output spend is accounted for."""
    explicit = (
        payload.get("max_tokens")
        or payload.get("max_completion_tokens")
        or (payload.get("options") or {}).get("num_predict")
    )
    if isinstance(explicit, (int, float)) and explicit > 0:
        return int(explicit)
    try:
        from awcp.laminar import config as _cfg
        return _cfg.OUTPUT_BUFFER
    except Exception:  # noqa: BLE001
        return 0


# ── public entry point ────────────────────────────────────────────────────────

def estimate_request(body: bytes, model_hint: str = "") -> int:
    """Estimate input token count from a raw provider request body.

    Handles:
      * Ollama   api/chat   — {"model":..., "messages":[...]}
      * Ollama   api/generate — {"model":..., "prompt":"..."}
      * OpenAI   v1/chat/completions — {"model":..., "messages":[...]}
      * OpenAI   v1/completions — {"model":..., "prompt":"..."}
      * Anthropic messages — {"model":..., "system":"...", "messages":[...]}
      * Embedding APIs — {"input": "..." | [...]}

    Returns 0 on any parse failure so callers can fail-open without raising.
    """
    if not body:
        return 0
    try:
        payload = json.loads(body)
    except Exception:  # noqa: BLE001
        return 0
    if not isinstance(payload, dict):
        return 0

    model = str(payload.get("model") or model_hint or "").strip()
    # strip Ollama :tag suffix (e.g. "llama3.1:8b" → "llama3.1", "mistral:latest" → "mistral")
    if ":" in model:
        model = model.split(":")[0]
    est = _get(_encoding_for(model))

    # ── Chat / messages format ──────────────────────────────────────────────
    messages = payload.get("messages")
    if isinstance(messages, list):
        text = _messages_to_text(messages)
        # Per-message overhead: ~4 tokens (role + separator) per message,
        # plus 3 tokens for the assistant reply-prime OpenAI/Ollama prepend.
        overhead = len(messages) * 4 + 3
        # Anthropic puts system prompt outside messages at top level
        system = payload.get("system")
        if isinstance(system, str) and system:
            text = system + "\n" + text
        return est.estimate(text) + overhead + _max_output(payload)

    # ── Completion / generate format ────────────────────────────────────────
    prompt = payload.get("prompt")
    if isinstance(prompt, str):
        return est.estimate(prompt) + _max_output(payload)

    # ── Embedding / generic input format ───────────────────────────────────
    inp = payload.get("input")
    if isinstance(inp, str):
        return est.estimate(inp)
    if isinstance(inp, list):
        return est.estimate(" ".join(str(x) for x in inp))

    return 0
