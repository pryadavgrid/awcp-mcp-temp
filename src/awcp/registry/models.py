from pydantic import BaseModel


class AgentEntry(BaseModel):
    agent_id: str
    name: str
    route: str
    endpoint_url: str
    runtime: str
    version: str
    owner: str
    write_scopes: list[str]
    feature_flags: dict[str, bool]
    status: str = "active"

    # AgentCard (A2A) — AWCP's own in-process agents publish a self-describing
    # manifest so external A2A discoverers can read what each agent is for. Generated
    # from the AgentSpec at build time (card_url/card_fetched_at stay None: the card
    # is served from this process, not fetched). Additive + optional.
    card: dict | None = None
    card_url: str | None = None
    card_fetched_at: float | None = None
    skills: list[str] = []
