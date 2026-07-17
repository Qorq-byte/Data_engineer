"""Workflow orchestration models — WorkflowPlan, NodeDef, Evaluation, etc.

See SPEC §3.4.2-3.4.3 for the full specification.
"""

import contextlib
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


def _json_default(value: Any) -> str:
    """JSON fallback serializer: datetimes to ISO-8601, everything else to str."""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


@dataclass
class NodeConfig:
    """Configuration for a single node within a workflow plan."""

    temperature: float = 0.3
    max_candidates: int = 1
    max_rows: int = 100
    timeout_ms: int = 30000
    mode: str = "default"  # "default", "self_heal"
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass
class NodeDef:
    """A single node definition in a workflow plan's node_order."""

    id: str
    node: str  # maps to NodeRegistry key (e.g., "schema_linking")
    depends_on: list[str] = field(default_factory=list)
    on_failure: str = "abort"  # "abort", "skip", "fallback"
    on_pass: str = ""  # "output" for reflection nodes
    on_fail: str = ""  # "revise" for reflection nodes
    config: NodeConfig = field(default_factory=NodeConfig)


@dataclass
class FailurePolicy:
    """How the workflow handles node failures."""

    max_retries: int = 1
    on_exhausted: str = "abort"  # "abort", "skip", "fallback"


@dataclass
class EvaluationConfig:
    """Configuration for node output evaluation."""

    enabled: bool = True
    metrics: list[str] = field(default_factory=lambda: [
        "syntax_valid", "schema_compliant", "execution_success"
    ])


@dataclass
class WorkflowPlan:
    """A workflow plan template loaded from workflow.yml."""

    id: str
    name: str
    description: str = ""
    node_order: list[NodeDef] = field(default_factory=list)
    failure_policy: FailurePolicy = field(default_factory=FailurePolicy)
    evaluation: EvaluationConfig = field(default_factory=EvaluationConfig)
    max_iterations: int = 10
    on_exhausted: str = "force_output"  # "force_output", "abort"


@dataclass
class MetricResult:
    """Result of a single evaluation metric check."""

    name: str
    passed: bool
    details: str = ""


@dataclass
class Evaluation:
    """Result of evaluating a node's output against quality metrics."""

    passed: bool = True
    metrics: dict[str, MetricResult] = field(default_factory=dict)
    needs_revision: bool = False
    notes: str = ""


@dataclass
class NodeTrace:
    """Trace record for a single node execution."""

    node_id: str
    node_name: str
    input_summary: str = ""
    output_summary: str = ""
    evaluation: Evaluation | None = None
    started_at: datetime = field(default_factory=datetime.now)
    completed_at: datetime | None = None
    error: str | None = None
    latency_ms: float = 0.0


@dataclass
class WorkflowTrace:
    """Complete trace of a workflow execution for audit/replay."""

    workflow_id: str
    plan_id: str
    nodes: list[NodeTrace] = field(default_factory=list)
    started_at: datetime = field(default_factory=datetime.now)
    completed_at: datetime | None = None
    session_id: str = ""

    def record(
        self,
        node_id: str,
        output_summary: str,
        evaluation: Evaluation | None = None,
    ) -> None:
        """Append a simplified NodeTrace entry for auditability."""
        self.nodes.append(
            NodeTrace(
                node_id=node_id,
                node_name=node_id,
                output_summary=output_summary,
                evaluation=evaluation,
                completed_at=datetime.now(),
            )
        )

    async def persist(self) -> None:
        """Persist the trace to disk as JSONL for audit/replay.

        Directory: ``$DE_TRACE_DIR`` when set, otherwise
        ``data/traces/{YYYYMMDD}/``. File name: ``{workflow_id}.jsonl``.
        The first line holds workflow metadata (workflow_id, plan_id,
        session_id, started_at, completed_at); each following line is one
        serialized node trace. Missing directories are created.

        No-op when ``workflow_id`` is empty. IO errors are suppressed —
        audit persistence must never interrupt a query.
        """
        if not self.workflow_id:
            return

        trace_dir = Path(
            os.environ.get("DE_TRACE_DIR")
            or Path("data") / "traces" / datetime.now().strftime("%Y%m%d")
        )
        header = {
            "workflow_id": self.workflow_id,
            "plan_id": self.plan_id,
            "session_id": self.session_id,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }
        lines = [json.dumps(header, ensure_ascii=False, default=_json_default)]
        lines.extend(
            json.dumps(asdict(node), ensure_ascii=False, default=_json_default)
            for node in self.nodes
        )
        with contextlib.suppress(OSError):
            trace_dir.mkdir(parents=True, exist_ok=True)
            target = trace_dir / f"{self.workflow_id}.jsonl"
            target.write_text("\n".join(lines) + "\n", encoding="utf-8")


@dataclass
class WorkflowResult:
    """Final result of a workflow execution."""

    status: str  # "completed", "failed", "aborted"
    context: dict[str, Any] = field(default_factory=dict)
    trace: WorkflowTrace | None = None
    final_output: Any = None
    error: str | None = None
