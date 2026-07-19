"""workflow.yml Plan template loader.

See SPEC §3.4.2 for the workflow Plan template specification.
"""

from pathlib import Path

import yaml

from app.models.workflow import (
    EvaluationConfig,
    FailurePolicy,
    NodeConfig,
    NodeDef,
    WorkflowPlan,
)


class PlanLoader:
    """Loads and caches workflow.yml Plan templates.

    Plans are stored as YAML files in app/workflow/plans/.
    Each file defines a DAG node_order that the WorkflowRunner follows.
    """

    def __init__(self, plans_dir: str = "app/workflow/plans"):
        self.plans_dir = Path(plans_dir)
        self._cache: dict[str, WorkflowPlan] = {}

    def load(self, plan_id: str) -> WorkflowPlan:
        """Load a plan template by ID.

        Caches in memory after first load. Use reload() to refresh.
        """
        if plan_id in self._cache:
            return self._cache[plan_id]

        path = self.plans_dir / f"{plan_id}.yml"
        if not path.exists():
            raise FileNotFoundError(
                f"Plan template not found: {path}. "
                f"Available plans: {self.list_available()}"
            )

        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)

        plan = self._parse(raw)
        self._cache[plan_id] = plan
        return plan

    def reload(self, plan_id: str) -> WorkflowPlan:
        """Force-reload a plan (clears cache first)."""
        self._cache.pop(plan_id, None)
        return self.load(plan_id)

    def list_available(self) -> list[str]:
        """List all available plan IDs."""
        if not self.plans_dir.exists():
            return []
        return sorted(
            p.stem for p in self.plans_dir.glob("*.yml")
        )

    # ── Parser ──────────────────────────────────────────────────

    @staticmethod
    def _parse(raw: dict) -> WorkflowPlan:
        plan_data = raw.get("plan", raw)

        node_order = []
        for nd in plan_data.get("node_order", []):
            node_order.append(
                NodeDef(
                    id=nd.get("id", ""),
                    node=nd.get("node", ""),
                    depends_on=nd.get("depends_on", []),
                    on_failure=nd.get("on_failure", "abort"),
                    on_pass=nd.get("on_pass", ""),
                    on_fail=nd.get("on_fail", ""),
                    config=PlanLoader._parse_node_config(nd.get("config", {})),
                )
            )

        fp = plan_data.get("failure_policy", {})
        failure_policy = FailurePolicy(
            max_retries=fp.get("max_retries", 1),
            on_exhausted=fp.get("on_exhausted", "abort"),
        )

        ev = plan_data.get("evaluation", {})
        evaluation = EvaluationConfig(
            enabled=ev.get("enabled", True),
            metrics=ev.get("metrics", []),
        )

        return WorkflowPlan(
            id=raw.get("id", ""),
            name=raw.get("name", ""),
            description=raw.get("description", ""),
            node_order=node_order,
            failure_policy=failure_policy,
            evaluation=evaluation,
            max_iterations=plan_data.get("max_iterations", 10),
            on_exhausted=plan_data.get("on_exhausted", "force_output"),
        )

    @staticmethod
    def _parse_node_config(raw: dict | None) -> NodeConfig:
        if raw is None:
            return NodeConfig()
        return NodeConfig(
            temperature=raw.get("temperature", 0.3),
            max_candidates=raw.get("max_candidates", 1),
            max_rows=raw.get("max_rows", 100),
            timeout_ms=raw.get("timeout_ms", 30000),
            mode=raw.get("mode", "default"),
            extras={
                k: v
                for k, v in raw.items()
                if k
                not in {
                    "temperature",
                    "max_candidates",
                    "max_rows",
                    "timeout_ms",
                    "mode",
                }
            },
        )
