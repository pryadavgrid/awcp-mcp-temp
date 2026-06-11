"""Temporal settings for the onboarding workflow (best-effort)."""

import os

TEMPORAL_SERVER_URL = os.getenv("TEMPORAL_SERVER_URL", "localhost:7233")
TASK_QUEUE = os.getenv("AGENT_RADAR_TASK_QUEUE", "agent-radar-onboarding")
# Used only to build deep links from the web view.
TEMPORAL_UI_BASE = os.getenv("AGENT_RADAR_TEMPORAL_UI", "http://localhost:8233")
