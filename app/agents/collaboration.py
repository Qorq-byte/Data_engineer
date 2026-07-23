"""Collaboration modes — five strategies for multi-agent coordination.

See SPEC §4.11.5 and implementation plan §4.11.5 for the full specification.

Each mode is a standalone async function that orchestrates agent interactions.
They are called by ``OrchestratorAgent.run()`` based on the selected mode.
"""

from __future__ import annotations

import asyncio
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from app.agents.base import AgentResult, AgentStatus

if TYPE_CHECKING:
    from app.agents.orchestrator import OrchestratorAgent


class CollaborationMode(StrEnum):
    """The five collaboration strategies (SPEC §4.11.5)."""

    PIPELINE = "pipeline"
    RETRY_CORRECTION = "retry_correction"
    PARALLEL_DEBATE = "parallel_debate"
    NEGOTIATION = "negotiation"
    MULTI_STEP = "multi_step"


# ═══════════════════════════════════════════════════════════════════════════════
# Mode 1: Pipeline (default)
# ═══════════════════════════════════════════════════════════════════════════════


async def run_pipeline(
    orchestrator: OrchestratorAgent,
    query_text: str,
    context: dict[str, Any],
) -> list[AgentResult]:
    """Sequential handoff chain: NL → Schema → SQL → Validate → Tool Exec.

    Each agent's output becomes part of the context for the next agent.
    This is the default mode for most queries.
    """
    results: list[AgentResult] = []

    # ── Step 1: NL Understanding ───────────────────────────────────
    nl_agent = orchestrator.get_agent("nl_understander")
    if nl_agent is not None:
        r = await nl_agent.execute(query_text, context)
        results.append(r)
        if r.status == AgentStatus.DONE and r.data is not None:
            context["sqr"] = r.data  # Inject SQR into shared context
    else:
        results.append(_missing_agent("nl_understander"))

    # ── Step 2: Schema Retrieval ───────────────────────────────────
    schema_agent = orchestrator.get_agent("schema_retriever")
    if schema_agent is not None:
        r = await schema_agent.execute(query_text, context)
        results.append(r)
        if r.status == AgentStatus.DONE and isinstance(r.data, dict):
            context["filtered_schema"] = r.data.get("filtered_schema")
            context["tables"] = r.data.get("tables", [])
            context["terms"] = r.data.get("terms", [])
            context["rules"] = r.data.get("rules", [])
    else:
        results.append(_missing_agent("schema_retriever"))

    # ── Step 3: SQL Generation ─────────────────────────────────────
    sql_agent = orchestrator.get_agent("sql_generator")
    if sql_agent is not None:
        r = await sql_agent.execute(context, context)
        results.append(r)
        if r.status == AgentStatus.DONE and isinstance(r.data, list) and r.data:
            context["candidates"] = r.data
            sql_val = r.data[0].sql_text if hasattr(r.data[0], "sql_text") else str(r.data[0])
            context["sql"] = sql_val
    else:
        results.append(_missing_agent("sql_generator"))

    # ── Step 4: Validation ─────────────────────────────────────────
    val_agent = orchestrator.get_agent("sql_validator")
    if val_agent is not None:
        sql_to_validate = context.get("sql", "")
        r = await val_agent.execute(sql_to_validate, context)
        results.append(r)
        if r.status == AgentStatus.DONE and isinstance(r.data, dict):
            context["validation_passed"] = r.data.get("passed", False)
            context["validation_report"] = r.data.get("report")
    else:
        results.append(_missing_agent("sql_validator"))

    # ── Step 5: Tool Execution (if validation passed) ──────────────
    tool_agent = orchestrator.get_agent("tool_executor")
    if tool_agent is not None and context.get("sql"):
        r = await tool_agent.execute({"sql": context["sql"]}, context)
        results.append(r)
    elif tool_agent is not None:
        results.append(_stub_result("tool_executor", "No SQL to execute"))

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# Mode 2: Back-pass Correction (retry with correction)
# ═══════════════════════════════════════════════════════════════════════════════


async def run_retry_correction(
    orchestrator: OrchestratorAgent,
    query_text: str,
    context: dict[str, Any],
    max_rounds: int = 3,
) -> list[AgentResult]:
    """Pipeline with correction loop: Validation failure → back to SQL Agent.

    Runs the pipeline, and if validation fails, injects the error back into
    the SQL generation agent for correction (up to *max_rounds* times).
    """
    results: list[AgentResult] = []

    # First, run steps 1–3 (NL → Schema → SQL)
    nl_agent = orchestrator.get_agent("nl_understander")
    if nl_agent is not None:
        r = await nl_agent.execute(query_text, context)
        results.append(r)
        if r.status == AgentStatus.DONE and r.data is not None:
            context["sqr"] = r.data

    schema_agent = orchestrator.get_agent("schema_retriever")
    if schema_agent is not None:
        r = await schema_agent.execute(query_text, context)
        results.append(r)

    sql_agent = orchestrator.get_agent("sql_generator")
    val_agent = orchestrator.get_agent("sql_validator")

    if sql_agent is None:
        results.append(_missing_agent("sql_generator"))
        return results

    # Correction loop
    for round_num in range(1, max_rounds + 1):
        r = await sql_agent.execute(context, context)
        results.append(r)

        if r.status != AgentStatus.DONE or not isinstance(r.data, list) or not r.data:
            break  # Generation failed — can't retry

        sql = r.data[0].sql_text if hasattr(r.data[0], "sql_text") else str(r.data[0])
        context["sql"] = sql

        if val_agent is not None:
            vr = await val_agent.execute(sql, context)
            results.append(vr)
            if isinstance(vr.data, dict) and vr.data.get("passed"):
                break  # Validation passed — done

            # Inject error for next round
            error_text = ""
            report = vr.data.get("report") if isinstance(vr.data, dict) else None
            if report and hasattr(report, "errors"):
                error_text = "; ".join(report.errors)
            elif isinstance(vr.data, dict):
                error_text = str(vr.data.get("report", ""))
            context["correction_error"] = error_text or "Validation failed"

            if round_num == max_rounds:
                results.append(
                    AgentResult(
                        agent_name="orchestrator",
                        status=AgentStatus.ERROR,
                        errors=[f"Correction failed after {max_rounds} rounds: {error_text}"],
                    )
                )
        else:
            break  # No validator — accept first generation

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# Mode 3: Parallel Debate
# ═══════════════════════════════════════════════════════════════════════════════


async def run_parallel_debate(
    orchestrator: OrchestratorAgent,
    query_text: str,
    context: dict[str, Any],
    num_candidates: int = 3,
) -> list[AgentResult]:
    """Multiple SQL generation strategies run in parallel, then synthesise.

    Runs NL → Schema first, then fans out *num_candidates* SQL generation calls
    with different strategies, and collects the best result.
    """
    results: list[AgentResult] = []

    # ── NL Understanding ───────────────────────────────────────────
    nl_agent = orchestrator.get_agent("nl_understander")
    if nl_agent is not None:
        r = await nl_agent.execute(query_text, context)
        results.append(r)
        if r.status == AgentStatus.DONE and r.data is not None:
            context["sqr"] = r.data

    # ── Schema Retrieval ───────────────────────────────────────────
    schema_agent = orchestrator.get_agent("schema_retriever")
    if schema_agent is not None:
        r = await schema_agent.execute(query_text, context)
        results.append(r)

    # ── Parallel SQL generation ────────────────────────────────────
    sql_agent = orchestrator.get_agent("sql_generator")
    if sql_agent is None:
        results.append(_missing_agent("sql_generator"))
        return results

    strategies = ["temperature", "multi_perspective"]
    if num_candidates > len(strategies):
        strategies = strategies + ["temperature"] * (num_candidates - len(strategies))
    strategies = strategies[:num_candidates]

    async def generate_one(strategy: str) -> AgentResult:
        ctx = dict(context)
        ctx["strategy"] = strategy
        ctx["num_candidates"] = 1
        return await sql_agent.execute(ctx, ctx)

    parallel_results = await asyncio.gather(
        *[generate_one(s) for s in strategies],
        return_exceptions=True,
    )

    for pr in parallel_results:
        if isinstance(pr, Exception):
            results.append(
                AgentResult(
                    agent_name="sql_generator",
                    status=AgentStatus.ERROR,
                    errors=[str(pr)],
                )
            )
        elif pr is not None:
            results.append(pr)

    # ── Synthesis: pick best candidate ─────────────────────────────
    all_candidates = []
    for r in results:
        if (
            r.agent_name == "sql_generator"
            and r.status == AgentStatus.DONE
            and isinstance(r.data, list)
        ):
            all_candidates.extend(r.data)

    if all_candidates:
        # Sort by confidence descending
        all_candidates.sort(
            key=lambda c: c.confidence if hasattr(c, "confidence") else 0.0,
            reverse=True,
        )
        context["candidates"] = all_candidates
        context["sql"] = (
            all_candidates[0].sql_text
            if hasattr(all_candidates[0], "sql_text")
            else str(all_candidates[0])
        )
        results.append(
            AgentResult(
                agent_name="orchestrator",
                status=AgentStatus.DONE,
                data={
                    "synthesis": "parallel_debate_complete",
                    "candidate_count": len(all_candidates),
                },
                metadata={
                    "best_confidence": (
                        all_candidates[0].confidence
                        if hasattr(all_candidates[0], "confidence")
                        else None
                    ),
                },
            )
        )

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# Mode 4: Agent Negotiation
# ═══════════════════════════════════════════════════════════════════════════════


async def run_negotiation(
    orchestrator: OrchestratorAgent,
    query_text: str,
    context: dict[str, Any],
) -> list[AgentResult]:
    """Ambiguity detected → collect proposals → vote → return to user.

    When the NL Understanding agent detects ambiguity, this mode collects
    interpretations from multiple agents, scores them, and presents options
    for user confirmation.
    """
    results: list[AgentResult] = []

    # ── NL Understanding (critical for ambiguity detection) ────────
    nl_agent = orchestrator.get_agent("nl_understander")
    if nl_agent is not None:
        r = await nl_agent.execute(query_text, context)
        results.append(r)
        if r.status == AgentStatus.DONE and r.data is not None:
            context["sqr"] = r.data
            # Check for ambiguities
            if hasattr(r.data, "ambiguities") and r.data.ambiguities:
                context["ambiguities_detected"] = len(r.data.ambiguities)
    else:
        results.append(_missing_agent("nl_understander"))

    # ── Schema Retrieval ───────────────────────────────────────────
    schema_agent = orchestrator.get_agent("schema_retriever")
    if schema_agent is not None:
        r = await schema_agent.execute(query_text, context)
        results.append(r)

    # ── Generate multiple interpretations ──────────────────────────
    sql_agent = orchestrator.get_agent("sql_generator")
    if sql_agent is not None:
        r = await sql_agent.execute(
            {"sqr": context.get("sqr"), "num_candidates": 3, "strategy": "multi_perspective"},
            context,
        )
        results.append(r)
        if r.status == AgentStatus.DONE and isinstance(r.data, list):
            context["candidates"] = r.data

    # ── Negotiation outcome ────────────────────────────────────────
    ambiguities_count = context.get("ambiguities_detected", 0)
    if ambiguities_count > 0:
        results.append(
            AgentResult(
                agent_name="orchestrator",
                status=AgentStatus.DONE,
                data={
                    "negotiation_required": True,
                    "ambiguity_count": ambiguities_count,
                    "message": "Ambiguity detected — user confirmation recommended",
                },
            )
        )
    else:
        results.append(
            AgentResult(
                agent_name="orchestrator",
                status=AgentStatus.DONE,
                data={"negotiation_required": False},
            )
        )

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# Mode 5: Multi-step Pipeline
# ═══════════════════════════════════════════════════════════════════════════════


async def run_multi_step(
    orchestrator: OrchestratorAgent,
    query_text: str,
    context: dict[str, Any],
) -> list[AgentResult]:
    """Decompose query into sub-tasks, run full NL→SQL→Exec for each.

    Phase 4: simple sequential sub-task processing.
    Phase 5: conditional branching and data flow between steps.
    """
    results: list[AgentResult] = []

    # ── Step 1: Decompose the query into sub-tasks ─────────────────
    orchestrator._analyze_complexity(query_text)

    # Simple heuristic: split on sentence boundaries
    sub_queries = _split_sub_queries(query_text)
    if len(sub_queries) <= 1:
        # Fall back to pipeline for single query
        return await run_pipeline(orchestrator, query_text, context)

    results.append(
        AgentResult(
            agent_name="orchestrator",
            status=AgentStatus.DONE,
            data={"decomposed": True, "sub_query_count": len(sub_queries)},
        )
    )

    # ── Step 2: Run pipeline for each sub-query ────────────────────
    accumulated_context = dict(context)
    for i, sub_q in enumerate(sub_queries):
        sub_ctx = dict(accumulated_context)
        sub_ctx["query_text"] = sub_q.strip()
        sub_ctx["sub_step"] = i + 1

        sub_results = await run_pipeline(orchestrator, sub_q.strip(), sub_ctx)
        for r in sub_results:
            r.metadata["sub_step"] = i + 1
        results.extend(sub_results)

        # Carry forward key outputs
        if sub_ctx.get("sql"):
            accumulated_context[f"step_{i + 1}_sql"] = sub_ctx["sql"]
        if sub_ctx.get("execution_result"):
            accumulated_context[f"step_{i + 1}_result"] = sub_ctx["execution_result"]

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════


def _missing_agent(agent_name: str) -> AgentResult:
    """Return a result indicating the agent was not registered."""
    return AgentResult(
        agent_name=agent_name,
        status=AgentStatus.ERROR,
        errors=[f"Agent '{agent_name}' is not registered in the orchestrator"],
    )


def _stub_result(agent_name: str, message: str) -> AgentResult:
    """Return a stub/skip result."""
    return AgentResult(
        agent_name=agent_name,
        status=AgentStatus.DONE,
        data={"stub": True, "message": message},
    )


def _split_sub_queries(query_text: str) -> list[str]:
    """Split a compound NL query into sub-queries on logical boundaries.

    Uses common Chinese and English sentence delimiters and conjunctions
    that indicate sequential steps.
    """
    import re

    # Split on explicit step indicators
    parts = re.split(
        r"(?:首先|然后|接着|之后|最后|第一步|第二步|第三步|第[一二三四五六七八九十]\s*步[：:])"
        r"|(?:first,?\s*|then,?\s*|next,?\s*|finally,?\s*|step\s*\d+[.:]\s*)",
        query_text,
        flags=re.IGNORECASE,
    )
    # Also try splitting on sentence boundaries
    if len(parts) <= 1:
        parts = re.split(r"[。；;]\s*", query_text)

    return [p.strip() for p in parts if p.strip()]


def select_mode(mode_name: str) -> CollaborationMode:
    """Convert a mode string to CollaborationMode enum."""
    try:
        return CollaborationMode(mode_name)
    except ValueError:
        return CollaborationMode.PIPELINE
