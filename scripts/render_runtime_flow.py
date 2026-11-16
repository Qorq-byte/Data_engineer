# ruff: noqa: E501
"""Render the README runtime diagram as animated and static SVG.

Run from the repository root::

    python scripts/render_runtime_flow.py

Writes ``assets/runtime-flow.svg`` (animated, used by the README) and
``assets/runtime-flow-static.svg`` (motion-free fallback). Standard library only: no network,
no fonts, no model or database access.

The drawing describes the shipped code path:

* boot: ``main.py`` -> uvicorn factory -> ``create_app`` -> FastAPI lifespan
* request: operator gate -> Pydantic -> admission -> ``run_query`` -> ``WorkflowRunner``
* nodes: ``request_query.yml`` fixes parse -> link -> generate -> validate -> execute
* branches: skips, blocks, exceptions and HTTP error classes
* end state: aggregate status, one ``QueryResponse``, persisted trace

It is an illustration, not a trace recording or a performance measurement.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"

W, H = 1440, 1846
DUR = 16.0

INK = "#E8EFF9"
MUT = "#9AABC3"
DIM = "#677992"
FAINT = "#26344A"
PANEL = "#0F1828"
PANEL_HI = "#152238"
TEAL = "#2DD4BF"
BLUE = "#7FB2F0"
PURPLE = "#A5B4FC"
AMBER = "#F5C451"
GREEN = "#4ADE80"
RED = "#F87171"

SANS = "'Segoe UI', -apple-system, BlinkMacSystemFont, 'Helvetica Neue', Arial, sans-serif"
MONO = "ui-monospace, SFMono-Regular, Consolas, 'Liberation Mono', Menlo, monospace"

LEFT, RIGHT = 64.0, 1400.0
INNER = RIGHT - LEFT
RAIL_X = 26.0

_overflow: list[str] = []


def esc(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _width(value: str, size: float, family: str) -> float:
    factor = 0.60 if family == MONO else 0.525
    return len(value) * size * factor


def txt(
    x: float,
    y: float,
    value: str,
    size: float = 10.0,
    fill: str = INK,
    weight: int = 400,
    anchor: str = "start",
    family: str = SANS,
    spacing: float = 0.0,
    opacity: float = 1.0,
) -> str:
    extra = f' letter-spacing="{spacing}"' if spacing else ""
    extra += f' opacity="{opacity}"' if opacity != 1 else ""
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{fill}" '
        f'font-weight="{weight}" text-anchor="{anchor}" font-family="{family}"{extra}>'
        f"{esc(value)}</text>"
    )


def block(
    x: float,
    y: float,
    rows: list[str],
    size: float = 9.2,
    fill: str = MUT,
    leading: float = 13.2,
    family: str = SANS,
    limit: float | None = None,
) -> str:
    if limit is not None:
        for row in rows:
            if _width(row, size, family) > limit:
                _overflow.append(f"{row!r} needs {_width(row, size, family):.0f}px > {limit:.0f}px")
    head = (
        f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{fill}" '
        f'font-weight="400" font-family="{family}">'
    )
    spans = [
        f'<tspan x="{x:.1f}" dy="{0 if index == 0 else leading}">{esc(row)}</tspan>'
        for index, row in enumerate(rows)
    ]
    return "".join([head, *spans, "</text>"])


def box(
    x: float,
    y: float,
    w: float,
    h: float,
    rx: float = 8.0,
    fill: str = PANEL,
    stroke: str = FAINT,
    sw: float = 1.0,
    opacity: float = 1.0,
    dash: str | None = None,
) -> str:
    dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
    op = f' opacity="{opacity}"' if opacity != 1 else ""
    return (
        f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{rx}" '
        f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{dash_attr}{op}/>'
    )


def ln(d: str, stroke: str = FAINT, sw: float = 1.0, dash: str | None = None, opacity: float = 1.0) -> str:
    dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
    op = f' opacity="{opacity}"' if opacity != 1 else ""
    return (
        f'<path d="{d}" fill="none" stroke="{stroke}" stroke-width="{sw}" '
        f'stroke-linecap="round" stroke-linejoin="round"{dash_attr}{op}/>'
    )


def head(x: float, y: float, direction: str = "down", color: str = FAINT, size: float = 5) -> str:
    if direction == "down":
        points = f"{x - size},{y - size * 1.4} {x + size},{y - size * 1.4} {x},{y}"
    elif direction == "right":
        points = f"{x - size * 1.4},{y - size} {x - size * 1.4},{y + size} {x},{y}"
    elif direction == "left":
        points = f"{x + size * 1.4},{y - size} {x + size * 1.4},{y + size} {x},{y}"
    else:
        points = f"{x - size},{y + size * 1.4} {x + size},{y + size * 1.4} {x},{y}"
    return f'<polygon points="{points}" fill="{color}"/>'


def pulse(t0: float, t1: float, peak: float = 1.0, animated: bool = True) -> str:
    """Opacity envelope across one loop, expressed as a single <animate>."""
    if not animated:
        return ""
    fade_in, fade_out = 0.14, 0.24
    start = t0 / DUR
    rise = min((t0 + fade_in) / DUR, (t1 - fade_out) / DUR)
    fall = max((t1 - fade_out) / DUR, (t0 + fade_in) / DUR)
    key_times = [0.0, start, rise, fall, t1 / DUR, 1.0]
    kts = ";".join(f"{max(0.0, min(1.0, k)):.4f}" for k in key_times)
    return (
        f'<animate attributeName="opacity" values="0;0;{peak};{peak};0;0" keyTimes="{kts}" '
        f'dur="{DUR}s" repeatCount="indefinite"/>'
    )


def glow(x: float, y: float, w: float, h: float, color: str, t0: float, t1: float, animated: bool) -> str:
    if not animated:
        return ""
    return (
        f'<g opacity="0">{pulse(t0, t1, 1.0, animated)}'
        f'{box(x, y, w, h, 9, color, "none", 0, 0.055)}'
        f'{box(x, y, w, h, 9, "none", color, 1.5, 0.8)}'
        f"</g>"
    )


def flow(d: str, color: str = TEAL, animated: bool = True, t0: float = 0.0) -> str:
    """Animated dashes over an already drawn connector."""
    if not animated:
        return ""
    return (
        f'<g opacity="0.55">{pulse(t0, t0 + 1.4, 0.9, animated)}'
        f'<path d="{d}" fill="none" stroke="{color}" stroke-width="1.6" '
        f'stroke-dasharray="4 16" stroke-linecap="round">'
        f'<animate attributeName="stroke-dashoffset" from="0" to="-40" dur="1.4s" '
        f'repeatCount="indefinite"/></path></g>'
    )


def harrow(x1: float, x2: float, y: float, color: str = FAINT, animated: bool = False, t0: float = 0.0) -> str:
    d = f"M {x1:.1f} {y:.1f} H {x2 - 7:.1f}"
    return "".join(
        [
            ln(d, color, 1.2),
            head(x2, y, "right", color),
            flow(d, color if color != FAINT else TEAL, animated, t0),
        ]
    )


def varrow(x: float, y1: float, y2: float, color: str = FAINT, animated: bool = False, t0: float = 0.0) -> str:
    d = f"M {x:.1f} {y1:.1f} V {y2 - 7:.1f}"
    return "".join(
        [
            ln(d, color, 1.2),
            head(x, y2, "down", color),
            flow(d, color if color != FAINT else TEAL, animated, t0),
        ]
    )


def section(y: float, index: str, title: str, caption: str, color: str = TEAL) -> str:
    rule_x = LEFT + 40 + len(title) * 6.3 + 12
    return "".join(
        [
            txt(LEFT - 22, y, index, 9.5, color, 700, family=MONO, spacing=1.2),
            txt(LEFT, y, title.upper(), 10.0, MUT, 600, spacing=1.7),
            ln(f"M {rule_x:.0f} {y - 3.4} H {RIGHT}", FAINT, 1),
            txt(RIGHT, y, caption, 8.6, DIM, 400, anchor="end", family=MONO),
        ]
    )


def card(
    x: float,
    y: float,
    w: float,
    h: float,
    kicker: str,
    title: str,
    subtitle: str,
    rows: list[str],
    accent: str,
    *,
    animated: bool,
    t0: float,
    t1: float,
    mono_rows: bool = False,
    row_color: str = MUT,
) -> str:
    family = MONO if mono_rows else SANS
    body = block(
        x + 14,
        y + (70 if subtitle else 50),
        rows,
        size=9.0 if mono_rows else 9.2,
        fill=row_color,
        leading=13.0,
        family=family,
        limit=w - 28,
    )
    parts = [
        box(x, y, w, h, 9, PANEL_HI, FAINT),
        box(x, y, 3.0, h, 1.5, accent, "none", 0),
        txt(x + 14, y + 20, kicker, 8.4, accent, 700, family=MONO, spacing=1.1),
        txt(x + 14, y + 38, title, 12.2, INK, 600, family=MONO if any(c in title for c in "._()") else SANS),
    ]
    if subtitle:
        parts.append(txt(x + 14, y + 52, subtitle, 8.4, DIM, 400, family=MONO))
        parts.append(ln(f"M {x + 14:.0f} {y + 58} H {x + w - 14:.0f}", FAINT, 0.9))
    parts.append(body)
    parts.append(glow(x, y, w, h, accent, t0, t1, animated))
    return "".join(parts)


def panel(
    x: float,
    y: float,
    w: float,
    h: float,
    title: str,
    rows: list[str],
    accent: str,
    *,
    animated: bool,
    t0: float,
    t1: float,
    title_size: float = 10.6,
    leading: float = 14.2,
    mono: bool = False,
) -> str:
    family = MONO if mono else SANS
    return "".join(
        [
            box(x, y, w, h, 9, PANEL, FAINT),
            txt(x + 14, y + 21, title, title_size, accent, 600),
            block(
                x + 14,
                y + 40,
                rows,
                size=8.9 if mono else 9.2,
                fill=MUT,
                leading=leading,
                family=family,
                limit=w - 28,
            ),
            glow(x, y, w, h, accent, t0, t1, animated),
        ]
    )


# ── boot band ──────────────────────────────────────────────────────────────

BOOT = [
    (
        "01 · PROCESS",
        "python main.py",
        "asyncio entry",
        ["asyncio.run(main())", "uvicorn.Config(", "  'app.api:create_app',", "  factory=True)"],
    ),
    (
        "02 · GUARD",
        "create_app()",
        "app factory",
        ["JWT secret < 32 chars", "or default → ValueError", "exits before bind", "13 routers · CORS · static"],
    ),
    (
        "03 · LIFESPAN",
        "lifespan",
        "startup owner",
        ["SHARED_WORKER_RELEASE", "empty → _single_lifespan", "set → immutable worker", "sole owner of start/stop"],
    ),
    (
        "04 · CATALOGS",
        "initialize_catalogs()",
        "catalog registry",
        ["12 nodes · 4 MCP servers", "6 agents · subagents", "subagent configs", "idempotent per process"],
    ),
    (
        "05 · PROBES",
        "startup components",
        "bounded probes",
        ["storage 10s · auth 10s", "RAG 10s · LLM 10s", "each failure degrades", "flags on app.state.components"],
    ),
    (
        "06 · READY",
        "app.state.initialized",
        "uvicorn server",
        ["= true → serve :8001", "GET /health", "GET /health/ready", "readiness reflects components"],
    ),
]

NODES = [
    (
        "01",
        "parse_nl",
        "ParseNLNode",
        ["reads   nl_text · domain", "        history (8 turns)", "writes  sqr{language, intent,", "        entities, ambiguities}", "LLM     parse + entities"],
    ),
    (
        "02",
        "schema_linking",
        "SchemaLinkingNode",
        ["reads   sqr · snapshot · glossary", "writes  linked_tables,", "        linked_columns, warnings", "retrieval  BM25 + vector →", "        RRF fusion"],
    ),
    (
        "03",
        "generate_sql",
        "GenerateSQLNode",
        ["reads   linked schema · dialect", "        num_candidates 1..5", "writes  candidates[], primary_sql,", "        generation_warnings", "LLM     BudgetedRouter"],
    ),
    (
        "04",
        "validate_sql",
        "ValidateSQLNode",
        ["reads   candidates · dialect · rules", "writes  validation_report,", "        validation_passed", "checks  syntax · refs · types ·", "        plan · business rules"],
    ),
    (
        "05",
        "execute_sql",
        "ExecuteSQLNode",
        ["reads   primary_sql · connection", "writes  last_execution{columns,", "        rows, row_count, truncated}", "gates   SQL policy → write", "        check → row cap"],
    ),
]

BRANCHES = [
    ("401 / 403 · no operator", "missing JWT; valid but not allow-listed", "denied before admission; nothing runs", RED, "01"),
    ("422 · invalid request", "bad dialect, bad DDL, disabled model", "Pydantic rejects; no workflow starts", RED, "02"),
    ("503 · 409 · admission", "slots full; session changed mid-flight", "HTTPException from classify_query_failure", RED, "03"),
    ("504 · deadline", "QUERY_TIMEOUT_SECONDS elapsed", "asyncio.wait_for cancels run_query", RED, "04"),
    ("skip · execution off", "execute=false or no db_id → null result", "node status skipped; execution stays null", AMBER, "05"),
    ("block · validation", "report failed → execute blocked → failed", "execute returns blocked; runner aborts", AMBER, "06"),
    ("abort · node exception", "evaluation failed · retries 0 · trace kept", "trace.error set, then on_failure abort", AMBER, "07"),
    ("degrade · never silent", "RAG ✗ keyword · mock LLM · write blocked", "warning[] carries the degrade reason", AMBER, "08"),
]


def build(animated: bool) -> str:
    out: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
        f'role="img" aria-labelledby="flowTitle flowDesc">',
        "<title id=\"flowTitle\">Runtime flow: boot, request, five nodes, branches, terminal state</title>",
        "<desc id=\"flowDesc\">A labelled illustration of the NL2SQL request path. Boot starts main.py, "
        "builds the FastAPI app, runs the lifespan probes and serves. A request passes the operator "
        "gate, request validation, admission and session binding, then request_query.yml drives five "
        "nodes through WorkflowRunner. Guard rails apply per step. Branches cover skips, validation "
        "blocks, node exceptions and HTTP error classes. Every path ends in an explicit status, one "
        "shared result contract and a persisted trace.</desc>",
        f'<rect width="{W}" height="{H}" fill="#0A1220"/>',
    ]

    # header
    out += [
        txt(LEFT, 44, "Runtime, end to end", 23, INK, 600),
        txt(
            LEFT,
            68,
            "Boot → request → five nodes → branches → terminal state. An illustration of the shipped path, not telemetry.",
            11.0,
            MUT,
        ),
        txt(RIGHT, 44, "request_query.yml · 5 nodes · 1 contract", 9.2, TEAL, 500, anchor="end", family=MONO),
        txt(RIGHT, 66, "generated by scripts/render_runtime_flow.py", 8.6, DIM, 400, anchor="end", family=MONO),
    ]
    # legend
    ly = 96
    out += [
        txt(LEFT, ly, "LEGEND", 8.2, DIM, 600, family=MONO, spacing=1.2),
        ln(f"M {LEFT + 62:.0f} {ly - 4} H {LEFT + 102:.0f}", TEAL, 1.2),
        txt(LEFT + 110, ly, "ordered call", 8.8, MUT),
        ln(f"M {LEFT + 202:.0f} {ly - 4} H {LEFT + 242:.0f}", AMBER, 1.2, "4 3"),
        txt(LEFT + 250, ly, "skip / degrade", 8.8, MUT),
        ln(f"M {LEFT + 350:.0f} {ly - 4} H {LEFT + 390:.0f}", RED, 1.2, "4 3"),
        txt(LEFT + 398, ly, "block / error", 8.8, MUT),
        ln(f"M {LEFT} 112 H {RIGHT}", FAINT, 1),
    ]

    # progress rail
    out += [
        ln(f"M {RAIL_X} 142 V 1796", FAINT, 1.2),
    ]
    for y in [160, 432, 746, 938, 1342, 1568]:
        out.append(f'<circle cx="{RAIL_X}" cy="{y}" r="2.4" fill="{DIM}"/>')
    if animated:
        out += [
            f'<circle cx="{RAIL_X}" cy="142" r="3.4" fill="{TEAL}" opacity="0.95">'
            f'<animate attributeName="cy" values="142;1796" dur="{DUR}s" repeatCount="indefinite"/>'
            f"</circle>",
            f'<circle cx="{RAIL_X}" cy="142" r="7" fill="none" stroke="{TEAL}" stroke-width="1" opacity="0.4">'
            f'<animate attributeName="cy" values="142;1796" dur="{DUR}s" repeatCount="indefinite"/>'
            f"</circle>",
        ]

    # ── 00 BOOT ───────────────────────────────────────────────────────────
    out.append(section(160, "00", "BOOT / 启动", "process → app → lifespan → ready", BLUE))
    card_w, gap = 200.0, 27.0
    for index, (kicker, title, subtitle, rows) in enumerate(BOOT):
        x = LEFT + index * (card_w + gap)
        t0 = 0.15 + index * 0.34
        out.append(
            card(
                x,
                178,
                card_w,
                132,
                kicker,
                title,
                subtitle,
                rows,
                BLUE if index < 5 else GREEN,
                animated=animated,
                t0=t0,
                t1=t0 + 0.55,
                mono_rows=True,
            )
        )
        if index < 5:
            out.append(harrow(x + card_w + 1, x + card_w + gap - 1, 244, FAINT, animated, t0 + 0.2))

    # degrade panel
    out += [box(LEFT, 318, 764, 88, 9, PANEL, FAINT)]
    out += [
        txt(LEFT + 14, 339, "DEGRADE, DO NOT CRASH", 9.4, AMBER, 600, spacing=1.1),
        txt(LEFT + 14, 355, "a failed probe sets components[name]=false and keeps serving", 8.7, DIM, 400, family=MONO),
    ]
    degrade = [
        ("storage ✗", "query history skipped"),
        ("auth ✗", "login / register disabled"),
        ("RAG ✗", "keyword linking + warning"),
        ("LLM ✗", "mock router published"),
    ]
    for index, (name, note) in enumerate(degrade):
        cx = LEFT + 14 + (index % 2) * 372
        cy = 368 + (index // 2) * 30
        out += [
            box(cx, cy, 354, 26, 6, PANEL_HI, FAINT),
            txt(cx + 10, cy + 17, name, 9.0, AMBER, 600, family=MONO),
            txt(cx + 92, cy + 17, note, 9.0, MUT),
        ]
    out.append(glow(LEFT, 318, 764, 88, AMBER, 1.9, 2.4, animated))

    # shutdown rail
    out += [box(852, 318, 548, 88, 9, PANEL, FAINT, 1, 1, "5 4")]
    out += [
        txt(866, 339, "SHUTDOWN RAIL · awaited, not best effort", 9.4, PURPLE, 600, spacing=0.8),
        block(
            866,
            357,
            [
                "SIGTERM → uvicorn drains → initialized=false",
                "background · subagents · DB leases · auth pool",
                "storage · RAG · router reset → process exit",
            ],
            8.8,
            MUT,
            13.4,
            MONO,
            limit=520,
        ),
        glow(852, 318, 548, 88, PURPLE, 1.5, 2.0, animated),
    ]

    # ── 01 ENTRY ──────────────────────────────────────────────────────────
    out.append(section(432, "01", "ENTRY & ADMISSION / 入口与准入", "four surfaces, one request service", TEAL))
    entries = [
        ("WEB", "Web workspace", "chat · schema · SQL workbench"),
        ("JSON", "POST /api/v1/query", "one QueryResponse"),
        ("SSE", "POST /api/v1/query/stream", "stage events → done | error"),
        ("MCP", "nl_query tool", "four FastMCP servers → same service"),
    ]
    ew = 325.0
    egap = 12.0
    for index, (kind, name, note) in enumerate(entries):
        x = LEFT + index * (ew + egap)
        out += [
            box(x, 452, ew, 42, 7, PANEL_HI, FAINT),
            txt(x + 12, 468, kind, 8.4, TEAL, 700, family=MONO, spacing=1.1),
            txt(x + 12, 484, name, 9.6, INK, 500, family=MONO),
            txt(x + ew - 12, 484, note, 8.4, DIM, 400, anchor="end"),
            glow(x, 452, ew, 42, TEAL, 2.55 + index * 0.12, 3.0 + index * 0.12, animated),
        ]
        out.append(ln(f"M {x + ew / 2:.0f} 494 V 512", FAINT, 1.1))
    out.append(ln(f"M {LEFT + ew / 2:.0f} 512 H {RIGHT - ew / 2:.0f}", FAINT, 1.1))

    guards = [
        ("GATE", "require_operator", ["JWT + administrator allow-list", "all /api/v1 except auth/health", "deny → 401 / 403"]),
        ("BIND", "QueryRequest", ["nl_text 1..10000 · dialect enum", "model must be enabled", "reject → 422"]),
        ("DEADLINE", "submit_query", ["asyncio.wait_for(run_query, N)", "QUERY_TIMEOUT_SECONDS = 120", "deadline → 504"]),
        ("ADMIT", "run_query", ["admission gate · session scope", "audit collector · usage tokens", "then _run_query"]),
    ]
    gw = 322.0
    ggap = 16.0
    for index, (kicker, title, rows) in enumerate(guards):
        x = LEFT + index * (gw + ggap)
        out.append(varrow(x + gw / 2, 512, 528, FAINT, animated, 2.8 + index * 0.18))
        out.append(
            card(
                x,
                528,
                gw,
                92,
                kicker,
                title,
                "",
                rows,
                TEAL,
                animated=animated,
                t0=2.8 + index * 0.18,
                t1=3.45 + index * 0.18,
            )
        )
        if index < 3:
            out.append(harrow(x + gw + 1, x + gw + ggap - 1, 574, FAINT, animated, 3.0 + index * 0.18))

    # admission strip
    out += [box(LEFT, 636, INNER, 76, 9, PANEL, FAINT)]
    out += [
        txt(LEFT + 14, 656, "ADMISSION · ORDER IS FIXED", 9.4, TEAL, 600, spacing=1.0),
        txt(
            RIGHT - 14,
            656,
            "full → 503 QUERY_CAPACITY · conflict → 409 SESSION_CONFLICT",
            8.6,
            RED,
            500,
            anchor="end",
            family=MONO,
        ),
    ]
    admit = [
        ("gate(\"query-admission\", 8)", "one slot per in-flight request"),
        ("shared_query_scope(session)", "install shared runtime when configured"),
        ("gate((\"session\", id))", "serialise one session's writes"),
        ("UsageAuditStore + outcome", "audit persists after the response is built"),
    ]
    aw = 300.0
    agap = 24.0
    for index, (name, note) in enumerate(admit):
        x = LEFT + 14 + index * (aw + agap)
        out += [
            box(x, 668, aw, 32, 6, PANEL_HI, FAINT),
            txt(x + 10, 681, name, 8.5, INK, 600, family=MONO),
            txt(x + 10, 694, note, 8.3, DIM),
        ]
        if index < 3:
            out.append(ln(f"M {x + aw + 2:.0f} 684 H {x + aw + agap - 4:.0f}", FAINT, 1.1))
            out.append(head(x + aw + agap - 4, 684, "right", FAINT, 4))
    out.append(glow(LEFT, 636, INNER, 76, TEAL, 3.7, 4.3, animated))

    # ── 02 CONTROL ────────────────────────────────────────────────────────
    out.append(section(746, "02", "CONTROL PLANE / 控制面", "the plan decides; the model does not", PURPLE))
    out.append(
        panel(
            LEFT,
            764,
            420,
            148,
            "request_query.yml",
            [
                "node_order   parse → link → gen →",
                "             validate → exec",
                "depends_on   chained · no insertion",
                "failure      max_retries 0 · abort",
                "evaluation   schema_compliant,",
                "             execution_success",
                "iterations   5",
            ],
            PURPLE,
            animated=animated,
            t0=4.45,
            t1=5.05,
            mono=True,
            leading=14.0,
        )
    )
    out.append(
        panel(
            LEFT + 442,
            764,
            424,
            148,
            "request-scoped bindings",
            [
                "ConfigLoader.load()   mtime hot reload",
                "ConstraintEnforcer    agent.yml + Settings",
                "resolve_query_targets pin db_id, dialect,",
                "                      connection, schema",
                "session history       last 8 turns",
                "NodeRegistry(5)       fresh per request",
            ],
            BLUE,
            animated=animated,
            t0=4.7,
            t1=5.3,
            mono=True,
            leading=14.4,
        )
    )
    out.append(
        panel(
            LEFT + 888,
            764,
            448,
            148,
            "WorkflowRunner.run()",
            [
                "empty node_order          → failed",
                "unknown metric            → failed",
                "missing / duplicate node  → failed",
                "unsatisfied depends_on    → failed",
                "all checks pass           → walk node_order",
            ],
            TEAL,
            animated=animated,
            t0=4.95,
            t1=5.55,
            mono=True,
            leading=14.4,
        )
    )
    out += [
        harrow(LEFT + 420, LEFT + 440, 838, FAINT, animated, 4.9),
        harrow(LEFT + 866, LEFT + 886, 838, FAINT, animated, 5.1),
        varrow(LEFT + 1112, 912, 930, FAINT, animated, 5.4),
    ]

    # ── 03 NODES ──────────────────────────────────────────────────────────
    out.append(section(938, "03", "FIVE NODES / 五个节点", "WorkflowContext is the only channel", TEAL))
    nw = 254.0
    ngap = 16.5
    ctx_keys = ["sqr", "linked_tables", "candidates", "validation_report", "last_execution"]
    for index, (kicker, title, subtitle, rows) in enumerate(NODES):
        x = LEFT + index * (nw + ngap)
        t0 = 5.7 + index * 0.56
        accent = TEAL if index % 2 == 0 else BLUE
        out.append(
            card(
                x,
                956,
                nw,
                214,
                kicker,
                title,
                subtitle,
                rows,
                accent,
                animated=animated,
                t0=t0,
                t1=t0 + 0.62,
                mono_rows=True,
            )
        )
        out += [
            ln(f"M {x + 14:.0f} 1092 H {x + nw - 14:.0f}", FAINT, 0.9),
            txt(x + 14, 1109, "→ ctx." + ctx_keys[index], 8.8, accent, 600, family=MONO),
        ]
        # lifecycle strip
        steps = ["setup", "execute", "update", "evaluate"]
        for step_index, step in enumerate(steps):
            sx = x + 10 + step_index * 59
            out += [
                box(sx, 1132, 54, 18, 4, PANEL, FAINT),
                txt(sx + 27, 1144, step, 7.4, DIM, 500, anchor="middle", family=MONO),
            ]
            if step_index < 3:
                out.append(
                    f'<polygon points="{sx + 55},{1141} {sx + 58},{1137} {sx + 58},{1145}" fill="{FAINT}"/>'
                )
        if index < 4:
            out.append(harrow(x + nw + 1, x + nw + ngap - 1, 1060, FAINT, animated, t0 + 0.3))

    # guard rail bar
    out += [box(LEFT, 1184, INNER, 50, 8, PANEL, FAINT)]
    guards_bar = [
        ("PermissionManager", "unlisted tool → deny"),
        ("ConstraintEnforcer", "10 LLM calls · retries"),
        ("SQL policy", "1 statement · AST checked"),
        ("execute_sql", "write branch → blocked"),
        ("caps", "1,000 rows · 30 s statement"),
    ]
    seg_w, seg_gap = 258.0, 11.5
    for index, (name, note) in enumerate(guards_bar):
        x = LEFT + 8 + index * (seg_w + seg_gap)
        out += [
            txt(x, 1204, name, 9.0, AMBER, 700, family=MONO),
            txt(x, 1220, note, 8.4, MUT),
        ]
        if index < 4:
            out.append(ln(f"M {x + seg_w - 4:.0f} 1194 V 1224", FAINT, 1))
    out.append(glow(LEFT, 1184, INNER, 50, AMBER, 8.6, 9.4, animated))

    # state ribbon
    out += [box(LEFT, 1246, INNER, 54, 8, PANEL, FAINT)]
    out += [
        txt(LEFT + 14, 1266, "STATE / 状态", 9.2, PURPLE, 700, spacing=1.1),
        txt(LEFT + 14, 1281, "one terminal state · every step traced", 8.4, DIM),
        f'<circle cx="{LEFT + 236}" cy="1272" r="4" fill="none" stroke="{DIM}" stroke-width="1.4"/>',
        txt(LEFT + 248, 1276, "running", 9.2, DIM, 600, family=MONO),
        ln(f"M {LEFT + 292} 1272 H {LEFT + 322}", FAINT, 1.1),
        head(LEFT + 328, 1272, "right", FAINT, 3.6),
        f'<circle cx="{LEFT + 336}" cy="1272" r="4" fill="none" stroke="{GREEN}" stroke-width="1.4"/>',
        txt(LEFT + 348, 1276, "completed", 9.2, GREEN, 600, family=MONO),
        ln(f"M {LEFT + 412} 1266 V 1278", FAINT, 1.1),
        f'<circle cx="{LEFT + 426}" cy="1272" r="4" fill="none" stroke="{RED}" stroke-width="1.4"/>',
        txt(LEFT + 438, 1276, "failed", 9.2, RED, 600, family=MONO),
        ln(f"M {LEFT + 488} 1266 V 1278", FAINT, 1.1),
        f'<circle cx="{LEFT + 502}" cy="1272" r="4" fill="none" stroke="{AMBER}" stroke-width="1.4"/>',
        txt(LEFT + 514, 1276, "aborted", 9.2, AMBER, 600, family=MONO),
        txt(LEFT + 612, 1266, "node: started → passed | failed | skipped", 8.8, MUT, 400, family=MONO),
        txt(LEFT + 612, 1281, "skipped is an outcome, not a silent pass", 8.4, DIM),
    ]

    # ── 04 BRANCHES ───────────────────────────────────────────────────────
    out.append(section(1322, "04", "BRANCHES & FAILURES / 分支与失败", "every exit names its reason", RED))
    bw = 322.0
    bgap = 16.0
    for index, (title, note, outcome, color, kicker) in enumerate(BRANCHES):
        col, row = index % 4, index // 4
        x = LEFT + col * (bw + bgap)
        y = 1340 + row * 88
        out += [
            box(x, y, bw, 78, 8, PANEL, FAINT),
            box(x, y, 2.6, 78, 1.3, color, "none", 0),
            txt(x + 12, y + 19, kicker, 8.0, color, 700, family=MONO, spacing=1.0),
            txt(x + 40, y + 19, title, 10.2, INK, 600),
            block(x + 12, y + 38, [note], 8.9, MUT, 12, limit=bw - 24),
            block(x + 12, y + 55, [outcome], 8.5, MUT, 12, limit=bw - 24),
            glow(x, y, bw, 78, color, 9.7 + index * 0.16, 10.25 + index * 0.16, animated),
        ]
    # ── 05 END STATE ──────────────────────────────────────────────────────
    out.append(section(1546, "05", "END STATE / 结束状态", "one status, one contract, one trace", GREEN))
    out.append(
        panel(
            LEFT,
            1564,
            400,
            148,
            "aggregate the target loop",
            [
                "completed  iff every target status",
                "           == completed",
                "           and errors == []",
                "",
                "failed     otherwise; the first error",
                "           text travels in errors[]",
                "HTTP 200   in both cases; status carries",
                "           the verdict",
            ],
            GREEN,
            animated=animated,
            t0=12.3,
            t1=12.9,
            leading=13.2,
        )
    )
    out.append(
        panel(
            LEFT + 420,
            1564,
            520,
            148,
            "QueryResponse — JSON / SSE",
            [
                "query_id · status · plan_id · workflow_ids",
                "llm_calls · llm_usage · llm_audit",
                "candidates[] · primary_sql · validation",
                "execution · linked_tables · linked_columns",
                "warnings · errors · source · source_note",
                "confidence is 0 by design; evidence is the answer",
                "MCP projects the same result fields",
            ],
            TEAL,
            animated=animated,
            t0=12.5,
            t1=13.1,
            mono=True,
            leading=13.8,
        )
    )
    out.append(
        panel(
            LEFT + 960,
            1564,
            440,
            148,
            "persist before reply",
            [
                "session turn         last 50 turns",
                "MySQL query history  best effort",
                "data/traces/<day>/<workflow_id>.jsonl",
                "usage audit          optional, no SQL",
                "in-process trace     plan · status · ms",
                "audit failure        warning, not error",
            ],
            PURPLE,
            animated=animated,
            t0=12.7,
            t1=13.3,
            mono=True,
            leading=14.4,
        )
    )
    out += [
        harrow(LEFT + 400, LEFT + 418, 1638, FAINT, animated, 12.9),
        harrow(LEFT + 940, LEFT + 958, 1638, FAINT, animated, 13.1),
    ]

    # terminal pills
    pills = [
        ("completed → HTTP 200", "primary SQL + validation + rows", GREEN, LEFT, 322),
        ("failed → HTTP 200", "status=failed · errors[] explains", RED, LEFT + 338, 322),
        ("rejected → 4xx / 5xx", "code + Retry-After when transient", AMBER, LEFT + 676, 340),
    ]
    for label, note, color, x, w in pills:
        out += [
            box(x, 1738, w, 44, 7, PANEL_HI, color, 1.0),
            txt(x + 14, 1756, label, 9.6, color, 600, family=MONO),
            txt(x + 14, 1771, note, 8.4, MUT),
            varrow(x + w / 2, 1712, 1736, FAINT, animated, 13.3),
        ]
    out += [
        box(LEFT + 1036, 1738, 364, 44, 7, PANEL, FAINT, 1, 1, "4 4"),
        txt(LEFT + 1050, 1756, "always: lease released · process serving", 9.0, INK, 600, family=MONO),
        txt(LEFT + 1050, 1771, "ready for the next request; shutdown rail on SIGTERM", 8.2, DIM),
        txt(
            LEFT,
            1810,
            "Generated by scripts/render_runtime_flow.py — not telemetry. Omits SSE heartbeat framing, "
            "per-driver leases and per-node timings.",
            8.4,
            DIM,
        ),
        txt(
            LEFT,
            1826,
            "Legacy initialize() in main.py is not on the server path; create_app + lifespan own startup. "
            "docs/runtime-flow.md records what each band claims.",
            8.4,
            DIM,
        ),
        txt(
            RIGHT,
            1810,
            "pre-1.0 MVP · single administrator group · read-only SQL",
            8.4,
            DIM,
            400,
            anchor="end",
            family=MONO,
        ),
    ]

    out.append("</svg>")
    return "\n".join(out)


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    animated = build(True)
    static = build(False)
    (ASSETS / "runtime-flow.svg").write_text(animated + "\n", encoding="utf-8", newline="\n")
    (ASSETS / "runtime-flow-static.svg").write_text(static + "\n", encoding="utf-8", newline="\n")
    if _overflow:
        print("Text overflow detected:")
        for item in _overflow:
            print("  -", item)
        raise SystemExit(1)
    print(f"wrote assets/runtime-flow.svg ({len(animated):,} chars)")
    print(f"wrote assets/runtime-flow-static.svg ({len(static):,} chars)")


if __name__ == "__main__":
    main()
