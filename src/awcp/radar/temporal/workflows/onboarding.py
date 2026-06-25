"""The per-agent onboarding workflow (visible in the Temporal UI)."""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from awcp.radar.temporal.activities.onboarding import (
        fetch_card,
        map_identity,
        quarantine_check,
        link_mcp,
        admit,
    )


@workflow.defn
class AgentOnboardingWorkflow:
    """Fetch card -> Map identity -> Quarantine-check -> Link MCP -> Admit.

    fetch_card runs FIRST (it only needs entry.endpoint, set at registration) so
    entry.card is populated before map_identity — letting identity mapping read any
    advisory card metadata. It is best-effort: a missing/unreachable card returns a
    note and the workflow continues unchanged."""

    @workflow.run
    async def run(self, agent_id: str) -> dict:
        short = timedelta(seconds=30)
        link_to = timedelta(seconds=45)
        card_to = timedelta(seconds=20)  # HTTP GET + SSRF guard; lighter than MCP link

        card = await workflow.execute_activity(
            fetch_card, agent_id, start_to_close_timeout=card_to
        )
        await workflow.execute_activity(
            map_identity, agent_id, start_to_close_timeout=short
        )
        status = await workflow.execute_activity(
            quarantine_check, agent_id, start_to_close_timeout=short
        )
        link = await workflow.execute_activity(
            link_mcp, agent_id, start_to_close_timeout=link_to
        )
        final = await workflow.execute_activity(
            admit, agent_id, start_to_close_timeout=short
        )
        return {"agent_id": agent_id, "status": final or status,
                "card": card, "link": link}
