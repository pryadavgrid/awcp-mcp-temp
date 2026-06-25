"""Operator-authored policy — the Radar "Policy" tab.

An operator types ONE JSON document in the Radar UI that declares, by hand:

  * which DETECTED agents are RECOGNISED (allowed) and at what risk tier, and
  * which TOOLS are allowed and at what risk tier,

plus optional defaults for anything the document doesn't name. The document is
persisted in Postgres (``governance.operator_policy`` via :mod:`awcp.radar.db`),
append-only + versioned, so prior policies are kept as history and the ACTIVE
policy is always the most-recent row.

Where this sits in the decision flow (the operator's mental model):

    scanner detects the agent   (unchanged — detection is NOT gated by this)
        → the OPA agent assigns a baseline RISK TIER to the tool/agent
            → THIS policy is consulted AFTER, as an operator OVERRIDE / allowlist:
              it can relabel the risk tier and/or force allow / deny.

Design rules that keep it safe to ship:

  * **Inert by default.** With no policy row stored (or the DB unavailable) every
    helper returns "no opinion" (``None``) and nothing changes — detection,
    onboarding and the gate behave exactly as before.
  * **Only an explicit rule constrains.** An agent/tool the policy does not name
    is governed by ``defaults`` (which itself defaults to *allow*), so turning on
    a policy never silently denies everything. To run a STRICT allowlist the
    operator sets ``defaults.agents.allow = false`` on purpose.
  * **Operator is authoritative.** Unlike a self-declaring agent (which may only
    tighten its own risk — see :func:`awcp.radar.policy.authoritative_risk`), the
    human operator may set ANY recognised tier here, up or down.

Nothing here raises into a request handler or the gate hot-path: every public
helper is wrapped fail-open.
"""

from __future__ import annotations

import fnmatch
import threading

from awcp.radar import db as _db
from awcp.radar.telemetry import log

# Recognised risk tiers. Agents must stay within the registry.agents CHECK
# (low|medium|high); tools relabel the OPA tier vocabulary, which adds "severe".
# Kept as small explicit sets so an invalid paste can't smuggle in a tier the
# downstream stores would reject.
AGENT_RISK_TIERS: frozenset[str] = frozenset({"low", "medium", "high"})
TOOL_RISK_TIERS: frozenset[str] = frozenset({"low", "medium", "high", "severe"})

_lock = threading.Lock()
_active: dict | None = None      # {id, ts, version, updated_by, note, policy} or None
_loaded = False                  # have we tried to load from the DB yet?


# ── load / cache ──────────────────────────────────────────────────────────────
def reload() -> dict | None:
    """(Re)load the active policy from Postgres into the in-process cache and
    return it. Safe to call repeatedly; the gate/tool hot-paths read the cache,
    not the DB."""
    global _active, _loaded
    try:
        loaded = _db.load_operator_policy()
    except Exception as exc:  # noqa: BLE001 — never break startup / a request
        log.warning("radar.operator_policy.reload failed error=%r", exc)
        loaded = None
    with _lock:
        _active = loaded
        _loaded = True
    return loaded


def _ensure_loaded() -> None:
    if not _loaded:
        reload()


def active() -> dict | None:
    """The active policy envelope {id, ts, version, updated_by, note, policy}, or
    None when none is stored."""
    _ensure_loaded()
    return _active


def _doc() -> dict:
    """Just the policy JSON body (``{}`` when none is stored)."""
    env = active()
    return (env or {}).get("policy") or {}


def enabled() -> bool:
    """True once a non-empty policy document is stored — the switch every
    enforcement point checks first so an absent policy is a guaranteed no-op."""
    d = _doc()
    return bool(d.get("agents") or d.get("tools") or d.get("defaults"))


# ── validation ────────────────────────────────────────────────────────────────
def _is_rule(v: object) -> bool:
    return isinstance(v, dict)


def _is_default(v: object) -> bool:
    """True when a field is the literal opt-out ``"default"`` — meaning 'no opinion,
    use the slider / OPA-suggested value'. Treated identically to the field being
    absent, for both ``allow`` and ``risk``."""
    return isinstance(v, str) and v.strip().lower() == "default"


def _explicit_allow(rule: dict) -> bool | None:
    """The rule's explicit allow as a bool, or None when absent / ``"default"``."""
    a = rule.get("allow")
    return a if isinstance(a, bool) else None


def _explicit_risk(*vals: object) -> str:
    """First usable risk tier among `vals` (lowercased), skipping absent / ``"default"``."""
    for v in vals:
        if v in (None, "") or _is_default(v):
            continue
        return str(v).strip().lower()
    return ""


def validate_policy(doc: object) -> tuple[bool, str]:
    """Structurally validate a policy document before it is stored, so a bad paste
    is rejected with a clear reason instead of corrupting enforcement.

    Shape (every part optional):
        {
          "version": <int>, "updated_by": <str>, "note": <str>,
          "defaults": {"agents": {"allow": <bool>, "risk": <tier|null>},
                       "tools":  {"allow": <bool>, "risk": <tier|null>}},
          "agents": { "<id|name|glob>": {"allow": <bool>, "risk": <low|medium|high|null>, "note": <str>} },
          "tools":  { "<tool|glob>":    {"allow": <bool>, "risk": <low|medium|high|severe|null>, "note": <str>} }
        }
    """
    if not isinstance(doc, dict):
        return False, "policy must be a JSON object"

    def _check_rule(where: str, key: str, rule: object, tiers: frozenset[str]) -> str:
        if not _is_rule(rule):
            return f"{where}['{key}'] must be an object"
        if "allow" in rule and not (isinstance(rule["allow"], bool) or _is_default(rule["allow"])):
            return f"{where}['{key}'].allow must be true/false (or \"default\")"
        risk = rule.get("risk")
        if risk not in (None, "") and not _is_default(risk) and str(risk).strip().lower() not in tiers:
            return (f"{where}['{key}'].risk '{risk}' must be one of "
                    f"{sorted(tiers)} (or null / \"default\")")
        if "note" in rule and not isinstance(rule["note"], str):
            return f"{where}['{key}'].note must be a string"
        return ""

    defaults = doc.get("defaults", {})
    if not isinstance(defaults, dict):
        return False, "defaults must be an object"
    for grp, tiers in (("agents", AGENT_RISK_TIERS), ("tools", TOOL_RISK_TIERS)):
        gd = defaults.get(grp, {})
        if gd and (err := _check_rule("defaults", grp, gd, tiers)):
            return False, err

    for grp, tiers in (("agents", AGENT_RISK_TIERS), ("tools", TOOL_RISK_TIERS)):
        section = doc.get(grp, {})
        if not isinstance(section, dict):
            return False, f"'{grp}' must be an object of rules"
        for key, rule in section.items():
            if err := _check_rule(grp, key, rule, tiers):
                return False, err
    return True, ""


def save(policy: dict, updated_by: str = "", note: str = "") -> dict:
    """Validate + persist a new policy version, then refresh the cache.

    Returns {ok, ...}. On a validation error ok is False with a reason and nothing
    is stored. On a DB failure ok is False (the operator must not believe an
    unpersisted policy is live)."""
    ok, err = validate_policy(policy)
    if not ok:
        return {"ok": False, "error": err}
    version = int((policy or {}).get("version") or (((active() or {}).get("version") or 0) + 1))
    stored = _db.save_operator_policy(policy, updated_by=updated_by, note=note, version=version)
    if stored is None:
        return {"ok": False,
                "error": "operator policy not persisted — no governance DB "
                         "(AGENT_RADAR_DATABASE_URL unset or unreachable)"}
    reload()
    log.info("radar.operator_policy.saved id=%s version=%s by=%s agents=%d tools=%d",
             stored.get("id"), stored.get("version"), updated_by or "—",
             len((policy or {}).get("agents") or {}), len((policy or {}).get("tools") or {}))
    return {"ok": True, **stored}


# ── rule resolution ───────────────────────────────────────────────────────────
def _match(section: dict, *keys: str) -> dict | None:
    """Resolve a rule for any of `keys` (e.g. an agent's id AND name): an EXACT
    key wins, else the first glob pattern (fnmatch) that matches any key. Returns
    the rule dict or None."""
    cand = [k for k in keys if k]
    for k in cand:                                   # exact match first
        if k in section:
            return section[k]
    for pat, rule in section.items():                # then glob patterns
        if any(ch in pat for ch in "*?[") and any(fnmatch.fnmatch(k, pat) for k in cand):
            return rule
    return None


def _default(group: str) -> dict:
    return (_doc().get("defaults") or {}).get(group) or {}


def agent_rule(agent_id: str, name: str = "") -> dict:
    """The resolved rule for an agent (its explicit rule merged over the agents
    default), or ``{}`` when the policy is inert. Never raises."""
    if not enabled():
        return {}
    try:
        rule = _match(_doc().get("agents") or {}, agent_id or "", name or "")
        merged = dict(_default("agents"))
        if rule:
            merged.update(rule)
        return merged
    except Exception as exc:  # noqa: BLE001
        log.warning("radar.operator_policy.agent_rule failed id=%s error=%r", agent_id, exc)
        return {}


def tool_rule(tool: str) -> dict:
    """The resolved rule for a tool (its explicit rule merged over the tools
    default), or ``{}`` when the policy is inert. Never raises."""
    if not enabled():
        return {}
    try:
        rule = _match(_doc().get("tools") or {}, tool or "")
        merged = dict(_default("tools"))
        if rule:
            merged.update(rule)
        return merged
    except Exception as exc:  # noqa: BLE001
        log.warning("radar.operator_policy.tool_rule failed tool=%s error=%r", tool, exc)
        return {}


def _rank(tier: str | None, tiers: list[str]) -> int:
    """Position of a tier in the (slider's) ordered vocabulary, or -1 if unknown."""
    t = (tier or "").strip().lower()
    return tiers.index(t) if t in tiers else -1


def agent_explicit(agent_id: str, name: str = "") -> bool | None:
    """An EXPLICIT per-entry allow for an agent (ignores defaults + threshold).
    True / False when the matching rule names `allow`; None otherwise. This is the
    only thing that OVERRIDES the slider-threshold default."""
    if not enabled():
        return None
    try:
        rule = _match(_doc().get("agents") or {}, agent_id or "", name or "")
        if rule:
            return _explicit_allow(rule)             # bool, or None for absent / "default"
    except Exception as exc:  # noqa: BLE001
        log.warning("radar.operator_policy.agent_explicit failed id=%s error=%r", agent_id, exc)
    return None


def agent_recognised(agent_id: str, name: str, risk_tier: str | None,
                     threshold: str | None, tiers: list[str] | None) -> bool | None:
    """Is this agent RECOGNISED (allowed)? Unified decision model:

      * an EXPLICIT per-entry ``allow`` wins (force allow / deny);
      * else the SLIDER THRESHOLD is the default — allowed iff the agent's risk tier
        is BELOW the threshold (tier rank < threshold rank), denied at or above.

    Returns None (no opinion → callers leave behaviour untouched) when the policy is
    inert, or there's no explicit rule AND no usable threshold/tier to compare."""
    if not enabled():
        return None
    exp = agent_explicit(agent_id, name)
    if exp is not None:
        return exp
    tiers = tiers or []
    if threshold and _rank(risk_tier, tiers) >= 0 and _rank(threshold, tiers) >= 0:
        return _rank(risk_tier, tiers) < _rank(threshold, tiers)
    return None


def agent_risk_override(agent_id: str, name: str = "") -> str | None:
    """An operator-set risk tier for this agent (low|medium|high), or None.
    Operator-authoritative: this OVERRIDES the declared/magazine tier."""
    risk = _explicit_risk(agent_rule(agent_id, name).get("risk"))   # skips absent / "default"
    return risk if risk in AGENT_RISK_TIERS else None


def tool_decision(tool: str, slm_tier: str | None = None) -> dict | None:
    """Apply the operator policy to ONE tool call AFTER the OPA agent assigned
    ``slm_tier``. Returns the operator's adjustments, or None when inert/no rule:

        {"risk_tier": <relabelled tier or slm_tier>,
         "block": <bool|None>,   # True=force block, False=force allow, None=defer to OPA
         "source": "operator_policy",
         "note": <operator note>}

    The OPA agent owns the baseline tier + the slider-threshold block (the DEFAULT
    check); this only layers the operator's overrides on top. Semantics:

      * an EXPLICIT per-tool ``allow`` is the ONLY override — true force-allows
        (whitelist, overriding a threshold block), false force-blocks;
      * ``risk`` RELABELS the tier the threshold compares against;
      * with no explicit ``allow``, ``block`` is None → defer to the slider threshold.
    """
    if not enabled():
        return None
    try:
        explicit = _match(_doc().get("tools") or {}, tool or "") or {}
        default = _default("tools")
        if not explicit and not default:
            return None
        risk = _explicit_risk(explicit.get("risk"), default.get("risk"))  # skips "default"
        tier = risk if risk in TOOL_RISK_TIERS else (slm_tier or None)
        a = _explicit_allow(explicit)                    # bool, or None for absent / "default"
        block: bool | None = (not a) if a is not None else None
        return {"risk_tier": tier, "block": block,
                "source": "operator_policy", "note": explicit.get("note") or ""}
    except Exception as exc:  # noqa: BLE001
        log.warning("radar.operator_policy.tool_decision failed tool=%s error=%r", tool, exc)
        return None
