"""SLM tier reasoner — the small language model behind the OPA agent.

A tiny, dependency-light client that asks a *small* local model (Ollama-compatible,
e.g. gemma2:2b) to REASON about ONE tool call and emit its risk tier. It is the
single place the OPA agent forms an opinion about how dangerous a tool is; the OPA
agent then turns that tier into an allow/block decision.

Why a small model: the job is a short, bounded classification (pick one of a handful
of tiers + a one-line reason), so the smallest capable local model keeps latency and
cost down while staying fully on-box. Everything is env-driven — the model, base URL
and tier vocabulary are injected, nothing is hardcoded.

The model is constrained to return JSON ({"tier": ..., "reason": ...}); the tier is
validated against the caller's vocabulary. On ANY failure (model down, timeout, junk
output) it falls back to the configured default tier — the PDP never crashes a task
on a model hiccup.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

import httpx


@dataclass
class SLMResult:
    tier: str
    reason: str
    engine: str          # "slm" | "slm-fallback" | "disabled"
    model: str


class SLM:
    """Classifies a tool call into one of `tiers` by reasoning with a small model."""

    def __init__(self, tiers: list[str], default_tier: str):
        self.tiers = [t.lower() for t in tiers]
        self.default_tier = default_tier.lower()
        self.enabled = os.getenv("OPA_SLM_ENABLED", "true").strip().lower() == "true"
        # Talk to the REAL model runtime directly (not the budget-gated /llm proxy):
        # the OPA agent is hidden infra and must not be metered as a worker agent.
        base = (os.getenv("OPA_SLM_BASE")
                or os.getenv("AWCP_GATEWAY_UPSTREAM")
                or os.getenv("OLLAMA_BASE")
                or "http://localhost:11434")
        self.base = base.rstrip("/")
        self.model = os.getenv("OPA_SLM_MODEL", "gemma2:2b").strip()
        self.timeout = float(os.getenv("OPA_SLM_TIMEOUT", "30"))
        self.temperature = float(os.getenv("OPA_SLM_TEMPERATURE", "0"))

    # ── introspection (shown on /health and /tiers) ──────────────────────────
    def info(self) -> dict:
        return {"enabled": self.enabled, "model": self.model, "base": self.base}

    # ── prompt (tier names injected — nothing hardcoded) ──────────────────────
    def _system_prompt(self) -> str:
        vocab = ", ".join(self.tiers)
        return (
            "You are a security risk classifier for autonomous AI-agent tool calls. "
            "Given ONE tool call, decide how dangerous it is and assign exactly one "
            f"risk tier from this ordered, least-to-most severe list: {vocab}.\n"
            "Reason about what the tool can actually DO:\n"
            "- least severe: read-only, local, no side effects (e.g. read a file, "
            "fetch public data, search).\n"
            "- moderate: pulls in external/untrusted data, or non-trivial local effects.\n"
            "- high: writes/mutates state, sends data to the network, or posts externally.\n"
            "- most severe: destructive or irreversible, touches secrets/credentials, "
            "or takes high-impact external actions.\n"
            'Respond ONLY with strict JSON: {"tier": "<one of the tiers>", '
            '"reason": "<one short sentence>"}. No prose, no code fences.'
        )

    def _user_prompt(self, tool: str, tool_input: dict | None, question: str) -> str:
        try:
            inp = json.dumps(tool_input or {}, ensure_ascii=False)[:1500]
        except Exception:                       # noqa: BLE001
            inp = str(tool_input)[:1500]
        lines = [f"Tool name: {tool}", f"Tool input: {inp}"]
        if question:
            lines.append(f"User question being served: {question[:500]}")
        lines.append("Classify this tool call's risk tier.")
        return "\n".join(lines)

    # ── classification ────────────────────────────────────────────────────────
    def classify(self, tool: str, tool_input: dict | None, question: str) -> SLMResult:
        if not self.enabled:
            return SLMResult(self.default_tier, "SLM disabled — default tier",
                             "disabled", self.model)
        try:
            content = self._call_ollama(
                self._system_prompt(), self._user_prompt(tool, tool_input, question))
            tier, reason = self._parse(content)
            if tier in self.tiers:
                return SLMResult(tier, reason or f"{self.model} classified as {tier}",
                                 "slm", self.model)
        except Exception:                       # noqa: BLE001 — never crash on the model
            pass
        return SLMResult(self.default_tier,
                         f"SLM unavailable/unclear — fell back to '{self.default_tier}'",
                         "slm-fallback", self.model)

    def _call_ollama(self, system: str, user: str) -> str:
        """One Ollama /api/chat round-trip, JSON-formatted, deterministic."""
        r = httpx.post(
            f"{self.base}/api/chat",
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "stream": False,
                "format": "json",
                "options": {"temperature": self.temperature},
            },
            timeout=self.timeout,
        )
        r.raise_for_status()
        return (r.json().get("message") or {}).get("content", "") or ""

    def _parse(self, content: str) -> tuple[str, str]:
        """Pull (tier, reason) out of the model's JSON reply, tolerantly."""
        text = (content or "").strip()
        try:
            obj = json.loads(text)
        except Exception:                       # noqa: BLE001 — salvage an embedded object
            start, end = text.find("{"), text.rfind("}")
            if start == -1 or end <= start:
                return "", ""
            obj = json.loads(text[start:end + 1])
        tier = str(obj.get("tier", "")).strip().lower()
        reason = str(obj.get("reason", "")).strip()
        return tier, reason
