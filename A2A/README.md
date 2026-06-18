# A2A — Agent Card registration + AWCP onboarding (self-contained prototype)

This folder is a **standalone** prototype. It does **not** touch the agents or
`control_panel.py` — run it on its own. It shows how to **borrow the A2A *Agent
Card* schema** as our registration format and layer AWCP governance on top.

> We borrow the **Agent Card shape only**. We do **not** implement the A2A wire
> protocol (no JSON-RPC `message/send`, no task state machine, no SSE).

## The one idea

```
registration body  =  A2A Agent Card   +   AWCP governance envelope
                       (what the agent is)   (owner, write scopes, risk, intake)
```

The Card is the borrowed standard (camelCase, like a real `.well-known`
Agent Card). The `awcp` block is ours — it carries the facts A2A omits and that
governance needs.

## Onboarding lifecycle

```
 self-register (push) ┐
 operator (by URL)    ├─▶ POST /v1/agents ─▶ QUARANTINED ─(approve)─▶ APPROVED + scopes
 scan (pull, stub)    ┘                          └────────(deny)────▶ DENIED
```

**Enforcement gate** (your rule — *active + approved → allow, else block*):

```
can_execute(agent, scope)  allowed  ⟺  status == approved
                                     ∧  agent is ACTIVE (fresh heartbeat)
                                     ∧  scope ∈ granted_scopes
```

## Files

| File | What it is |
|---|---|
| `agent_card.py` | A2A Agent Card + AWCP envelope schema, validation, and a `meta` → card builder (uses your existing `agent_runtime.py` meta shape) |
| `registry.py` | The registry: intake, authenticity check, quarantine/approve/deny, heartbeat, and the `can_execute` gate |
| `server.py` | Stdlib HTTP server: `POST /v1/agents` API + a small onboarding panel UI |
| `demo.py` | End-to-end walkthrough, no server needed — proves the gate blocks/allows |
| `cards/crewai_writer.json` | Example registration body (`{card, awcp}`) |

## Run it

```bash
cd A2A

# 1) See the whole lifecycle print out (no server):
python3 demo.py

# 2) Or run the registry + onboarding panel:
python3 server.py          # → http://localhost:8090

# register the sample card:
curl -s -X POST localhost:8090/v1/agents \
     -H 'content-type: application/json' \
     --data @cards/crewai_writer.json | python3 -m json.tool
```

## "Genuine vs forged"

`registry.verify_authentic()` is the authenticity step, separate from schema
validation. Prototype mechanism = **shared token** (set `AWCP_REGISTRY_TOKEN`) for
agents we deploy. The drop-in upgrade for external/vendor agents is a **signed
card** (A2A `signatures`, JWS) verified at the same call site.

## How this maps to the real system

- Each agent's `meta={...}` dict (in every `agent_runtime.py`) → `card_from_meta()`.
- `awcp_kit._radar_register()` would POST `{card, awcp}` to `/v1/agents` (push).
- `awcp_kit._govern()` (the write gate) would call `can_execute()` before any
  `save_artifact` / `external_post` — closing today's gap where writes aren't
  gated on registration/approval.

None of that is wired into the agents here — this folder only demonstrates the
shape so you can adopt it deliberately.
