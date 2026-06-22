"""Built-in reference hooks — one per *kind* of hook this project needs.

| Hook              | Category | Fires on                          | Default |
|-------------------|----------|-----------------------------------|---------|
| LoggingHook       | observer | every lifecycle point             | on      |
| AuditHook         | observer | governance points (persistent)    | on      |
| MetricsHook       | observer | every point (→ OTel/Prometheus)   | on      |
| TimingHook        | observer | task start/step/end (durations)   | on      |
| NotifyHook        | observer | approval / blocked / breach (webhook) | off |
| PolicyGuardHook   | guard    | gate evaluation (can veto)        | off     |

Import these to register your own subclasses, or let ``agent_hooks.init_hooks()``
load them from config.
"""

from awcp.agent_hooks.builtin.audit_hook import AuditHook
from awcp.agent_hooks.builtin.logging_hook import LoggingHook
from awcp.agent_hooks.builtin.metrics_hook import MetricsHook
from awcp.agent_hooks.builtin.notify_hook import NotifyHook
from awcp.agent_hooks.builtin.policy_hook import PolicyGuardHook
from awcp.agent_hooks.builtin.timing_hook import TimingHook

__all__ = [
    "AuditHook",
    "LoggingHook",
    "MetricsHook",
    "NotifyHook",
    "PolicyGuardHook",
    "TimingHook",
]
