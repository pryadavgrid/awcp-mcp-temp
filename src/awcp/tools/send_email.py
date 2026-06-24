"""Governed EXTERNAL-EGRESS write tool — sends an email. Declared `high` risk, so
the MCP governance plane routes it through the radar write-action gate (and, on
the agent side, behind an operator approval token) BEFORE it runs.

This introduces a NEW governed action class — outbound email egress — distinct
from save_artifact (local) and external_post (generic HTTP). It is the canonical
"agent reaches outside the boundary" action the magazine's Step 03 exists to gate.

Two backends, env-driven, nothing agent-specific:
  * DEFAULT (no SMTP configured) -> MOCK: the message is written to a human-readable
    OUTBOX file so you can SEE exactly what would have been sent (visual demo, no
    real mail, no creds, reversible).
  * REAL (set AWCP_SMTP_HOST + AWCP_SMTP_FROM) -> sends via SMTP. Opt-in only.

The risk tier is declared on the @tool decorator; the write scope is `notify:email`
so the radar's magazine-driven scope check governs which agents may send.
"""

import os
import time

from awcp.runtime.tool_runtime import tool

# Where mock emails land so they are visible. Env-driven; defaults next to the
# server's artifact store as a single readable log + one file per message.
OUTBOX_DIR = os.getenv(
    "AWCP_EMAIL_OUTBOX",
    os.path.join(os.getenv("AWCP_ARTIFACT_DIR", os.path.join(os.getcwd(), "artifacts")), "outbox"),
)

SMTP_HOST = os.getenv("AWCP_SMTP_HOST", "")
SMTP_PORT = int(os.getenv("AWCP_SMTP_PORT", "587"))
SMTP_USER = os.getenv("AWCP_SMTP_USER", "")
SMTP_PASS = os.getenv("AWCP_SMTP_PASS", "")
SMTP_FROM = os.getenv("AWCP_SMTP_FROM", "")


def _send_real(to: str, subject: str, body: str) -> str:
    """Send via SMTP. Only reached when AWCP_SMTP_HOST + AWCP_SMTP_FROM are set."""
    import smtplib
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["From"] = SMTP_FROM
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as s:
        s.starttls()
        if SMTP_USER:
            s.login(SMTP_USER, SMTP_PASS)
        s.send_message(msg)
    return f"sent email -> {to} via {SMTP_HOST} (subject: {subject!r})"


def _send_mock(to: str, subject: str, body: str) -> str:
    """Write the email to a visible OUTBOX instead of sending it."""
    os.makedirs(OUTBOX_DIR, exist_ok=True)
    ts = int(time.time())
    safe = "".join(c for c in (to or "to") if c.isalnum() or c in "-_.@") or "to"
    rendered = (
        f"To: {to}\nSubject: {subject}\nDate: {time.ctime(ts)}\n"
        f"{'-' * 48}\n{body}\n"
    )
    # one file per message (open it to read the email) ...
    path = os.path.join(OUTBOX_DIR, f"{ts}-{safe}.eml")
    with open(path, "w", encoding="utf-8") as f:
        f.write(rendered)
    # ... and append to a single rolling outbox you can tail
    with open(os.path.join(OUTBOX_DIR, "outbox.log"), "a", encoding="utf-8") as f:
        f.write(rendered + "\n")
    return f"queued email (MOCK outbox) -> {to}  |  written to {path}"


@tool("send_email", risk=os.getenv("AWCP_SEND_EMAIL_RISK", "high"), scope="notify:email")
def send_email(to: str, subject: str, body: str) -> str:
    """Send an email to a recipient.

    HIGH-RISK governed external egress (gated by the radar; approval-gated agent-side).
    Uses real SMTP when AWCP_SMTP_HOST + AWCP_SMTP_FROM are configured, otherwise
    writes the message to a visible mock OUTBOX (no real mail is sent).

    Args:
        to: Recipient email address.
        subject: Email subject line.
        body: Email body (plain text / markdown).

    Returns:
        A status string describing where the message went.
    """
    if SMTP_HOST and SMTP_FROM:
        return _send_real(to, subject, body)
    return _send_mock(to, subject, body)
