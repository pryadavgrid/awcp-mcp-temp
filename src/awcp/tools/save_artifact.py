"""Governed LOCAL write tool — persists a result artifact to the control plane's
artifact store. Declared `medium` risk, so the MCP governance plane routes it
through the radar write-action gate before it runs.

Nothing here is agent-specific: any agent that calls execute_tool("save_artifact",
...) over MCP gets the same governed write. The destination directory and the
declared risk are both env-driven.

The write scope is `file_system` (declared on the @tool decorator) so the radar's
magazine-driven scope check governs which agents may persist artifacts — this is
the scope the magazine grants as "file_system".
"""

import os
import time

from awcp.runtime.tool_runtime import tool

# Where governed artifacts land on the server. Env-driven so a deployment can
# point this at a shared volume without code changes.
ARTIFACT_DIR = os.getenv(
    "AWCP_ARTIFACT_DIR",
    os.path.join(os.getcwd(), "artifacts"),
)


@tool("save_artifact", risk=os.getenv("AWCP_SAVE_ARTIFACT_RISK", "medium"), scope="file_system")
def save_artifact(name: str, content: str) -> str:
    """Persist a result artifact to the control plane's artifact store.

    GOVERNED local write (gated by the radar before execution).

    Args:
        name: A short artifact name (sanitised; non-alphanumerics dropped).
        content: The artifact body to write.

    Returns:
        The path the artifact was written to.
    """
    os.makedirs(ARTIFACT_DIR, exist_ok=True)
    safe = "".join(c for c in (name or "") if c.isalnum() or c in "-_.") or "artifact"
    path = os.path.join(ARTIFACT_DIR, f"{int(time.time())}-{safe}")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content or "")
    return f"saved artifact: {path}"
