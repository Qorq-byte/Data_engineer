"""Subagent system — scoped, domain-specific multi-agent instances.

See SPEC §4.10 and implementation plan §4.12 for the full specification.

Exports:
  - SubagentInstance, SubagentStatus, SubagentResult
  - SubagentManager, subagent_manager singleton
  - SubagentRouter, subagent_router singleton
"""

from app.subagent.instance import SubagentInstance, SubagentResult, SubagentStatus
from app.subagent.manager import SubagentManager, subagent_manager
from app.subagent.router import SubagentRouter, subagent_router

__all__ = [
    "SubagentInstance",
    "SubagentResult",
    "SubagentStatus",
    "SubagentManager",
    "subagent_manager",
    "SubagentRouter",
    "subagent_router",
]
