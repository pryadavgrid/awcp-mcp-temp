"""End-to-end walkthrough of the registry lifecycle — no server needed.

Run:  python3 demo.py     (from inside the A2A/ folder)

It builds a registration from a sample agent `meta` dict (the exact shape your
agent_runtime.py files already use), pushes it through onboarding, and proves the
enforcement gate blocks/allows writes at each stage.
"""

from __future__ import annotations

import json

from agent_card import registration_from_meta, scan_stub
from registry import Registry


def show(title: str, obj) -> None:
    print(f"\n── {title} " + "─" * max(0, 60 - len(title)))
    print(json.dumps(obj, indent=2) if not isinstance(obj, str) else obj)


def gate(reg: Registry, eid: str, scope: str) -> None:
    ok, reason = reg.can_execute(eid, scope)
    mark = "✅ ALLOW" if ok else "⛔ BLOCK"
    print(f"   {mark}  execute({scope!r}) → {reason}")


def main() -> None:
    reg = Registry()

    # A sample agent meta dict — identical shape to crewai_agent/agent_runtime.py
    meta = {
        "agent": "CrewAI Writer", "framework": "crewai", "model": "ollama/llama3.1:8b",
        "tools": ["web_search", "save_artifact", "external_post"],
        "purpose": "Researches a topic, then drafts a structured write-up.",
        "format": "markdown",
        "examples": ["Write a short brief on the benefits of solar energy."],
    }

    # 1) Build the A2A-shaped registration body from that meta (push path).
    registration = registration_from_meta(
        meta, url="http://localhost:8101", owner="prateek", intake="self-register")
    show("1. registration body  =  A2A card  +  awcp envelope", registration)

    # 2) Register → lands QUARANTINED.
    res = reg.register(registration)
    show("2. POST /v1/agents", res)
    eid = res["id"]

    # 3) Try to act BEFORE approval → blocked by status.
    print("\n── 3. enforcement BEFORE approval " + "─" * 27)
    gate(reg, eid, "external_post")
    gate(reg, eid, "save_artifact")

    # 4) Approve (grants the requested write scopes).
    show("4. POST /v1/agents/{id}/approve", reg.approve(eid))

    # 5) Now allowed for granted scopes, still blocked for ungranted ones.
    print("\n── 5. enforcement AFTER approval " + "─" * 28)
    gate(reg, eid, "external_post")          # granted → allow
    gate(reg, eid, "delete_everything")      # never requested → block

    # 6) Agent goes stale (no heartbeat) → ACTIVE check blocks it.
    reg.get(eid).heartbeat_at = 0.0          # simulate a long-dead agent
    print("\n── 6. agent went stale (no heartbeat) " + "─" * 23)
    gate(reg, eid, "external_post")
    reg.heartbeat(eid)                        # agent checks back in
    print("   …heartbeat received…")
    gate(reg, eid, "external_post")          # active again → allow

    # 7) Scan path: a thin, unverified stub (no owner/scopes/skills). It is
    #    admitted so it stays VISIBLE (quarantined), but cannot be approved until
    #    its card is completed with an owner.
    stub = scan_stub("mystery_runtime.py", url="http://localhost:9999", pid=12345)
    res2 = reg.register(stub, intake="scan")
    show("7. scan-discovered stub (visible but incomplete)", res2)
    show("   approve attempt on the stub", reg.approve(res2["id"]))

    show("FINAL registry state", reg.list())


if __name__ == "__main__":
    main()
