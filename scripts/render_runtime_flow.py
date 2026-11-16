"""Render the README request-lifecycle diagram as animated and static SVG.

Run from the repository root:

    python scripts/render_runtime_flow.py

Writes ``assets/runtime-flow.svg`` (motion, used by the README) and
``assets/runtime-flow-static.svg`` (motion-free fallback). Standard library only: no fonts,
telemetry, model or database access. The illustration describes the fixed request path shipped
in ``app/workflow/plans/request_query.yml``; it is not a trace recording.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"

W, H = 1280, 1056
DUR = 14.0

INK = "#E9F0FA"
MUT = "#95A6BE"
DIM = "#61728C"
FAINT = "#2B3A52"
PANEL = "#111A2B"
PANEL_HI = "#16233A"
TEAL = "#2DD4BF"
TEAL_SOFT = "#7FF3E4"
BLUE = "#7FB2F0"
AMBER = "#F5C451"
GREEN = "#4ADE80"
RED = "#F87171"

SANS = "'Segoe UI', -apple-system, BlinkMacSystemFont, 'Helvetica Neue', Arial, sans-serif"
MONO = "ui-monospace, SFMono-Regular, Consolas, 'Liberation Mono', Menlo, monospace"

LEFT, RIGHT = 36.0, 1244.0
INNER = RIGHT - LEFT


def esc(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


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
    size: float = 9.5,
    fill: str = MUT,
    leading: float = 14.0,
    family: str = SANS,
) -> str:
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


def ln(d: str, stroke: str = FAINT, sw: float = 1.0, dash: str | None = None) -> str:
    dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
    return (
        f'<path d="{d}" fill="none" stroke="{stroke}" stroke-width="{sw}" '
        f'stroke-linecap="round" stroke-linejoin="round"{dash_attr}/>'
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


def pulse(t0: float, t1: float, peak: float = 1.0) -> str:
    """Opacity envelope across one loop, expressed as a single <animate>."""
    fade_in, fade_out = 0.16, 0.28
    start = t0 / DUR
    rise = min((t0 + fade_in) / DUR, (t1 - fade_out) / DUR)
    fall = max((t1 - fade_out) / DUR, (t0 + fade_in) / DUR)
    key_times = [0.0, start, rise, fall, t1 / DUR, 1.0]
    kts = ";".join(f"{max(0.0, min(1.0, k)):.4f}" for k in key_times)
    values = f"0;0;{peak};{peak};0;0"
    return (
        f'<animate attributeName="opacity" values="{values}" keyTimes="{kts}" '
        f'dur="{DUR}s" repeatCount="indefinite"/>'
    )


def glow(x: float, y: float, w: float, h: float, color: str, t0: float, t1: float) -> str:
    """A card-sized highlight that fades in and out inside the shared loop."""
    return (
        f'<g opacity="0">{pulse(t0, t1)}'
        f'{box(x, y, w, h, 10, color, "none", 0, 0.05)}'
        f'{box(x, y, w, h, 10, "none", color, 1.6, 0.85)}'
        f"</g>"
    )


def section(y: float, index: str, title: str) -> str:
    rule_x = LEFT + 34 + len(title) * 5.9 + 10
    return "".join(
        [
            txt(LEFT, y, index, 9.5, TEAL, 700, family=MONO, spacing=1.2),
            txt(LEFT + 34, y, title.upper(), 9.5, MUT, 600, spacing=1.6),
            ln(f"M {rule_x:.0f} {y - 3} H {RIGHT}", FAINT, 1),
        ]
    )


CHIPS = [
    ("Web workspace", "chat · schema · SQL workbench"),
    ("JSON API", "POST /api/v1/query"),
    ("SSE stream", "POST /api/v1/query/stream"),
    ("MCP server", "4 FastMCP servers · tool calls"),
]

BIND_STEPS = [
    ("JWT + allow-list", "unlisted operator → 401", "auth"),
    ("Request validation", "Pydantic · dialect enum · enabled model", "validate"),
    ("Session + target", "session_id · domain · db_id snapshot", "bind"),
]

CONTROL_CARDS = [
    (124.0, 214.0, "PlanLoader", ["request_query.yml", "validated before use"]),
    (
        352.0,
        414.0,
        "request_query.yml",
        ["node_order:", "parse_nl → schema_linking →", "generate_sql → validate_sql → execute_sql"],
    ),
    (780.0, 214.0, "WorkflowRunner", ["setup · execute · update", "owns every transition"]),
    (
        1008.0,
        222.0,
        "node lifecycle",
        ["setup_input()", "execute()", "update_context()", "evaluate()"],
    ),
]

NODES = [
    (
        "parse_nl",
        "question → structured query",
        [
            "language · intent · entities",
            "time range · target tables",
            "ambiguous asks flagged",
            "clarification blocks execution",
        ],
        "→ SQR",
    ),
    (
        "schema_linking",
        "entities → schema objects",
        [
            "tables · columns · keys · comments",
            "glossary · FK expansion",
            "BM25 ⊕ vectors → RRF",
            "cached vs fresh snapshot labelled",
        ],
        "→ linked schema",
    ),
    (
        "generate_sql",
        "prompt → SQL candidates",
        [
            "1–5 candidates, temperature spread",
            "completion evidence + usage",
            "writes are never requested",
            "mock mode yields placeholders",
        ],
        "→ candidates",
    ),
    (
        "validate_sql",
        "five static stages",
        [
            "syntax · schema refs — blocking",
            "types · plan · rules — warning",
            "each check pass / fail / ?",
            "skipped is never reported as passed",
        ],
        "→ evidence",
    ),
    (
        "execute_sql",
        "read-only run, if eligible",
        [
            "read-only session · row cap",
            "deadline · connection lease",
            "no write path exists",
            "skip is not success",
        ],
        "→ rows | skipped",
    ),
]

GUARDS = [
    ("PermissionManager", "allow · deny · ask · default deny"),
    ("ConstraintEnforcer", "read_only · max_rows · max_llm_calls"),
    ("SQL policy", "single statement · AST · no DML"),
    ("LLM boundary", "budget · finish_reason · usage"),
]

LANES = [
    (LEFT, 584.0, "execution runs", GREEN, 11.7, "rows + truncated flag",
     "read-only session · row cap · deadline · lease"),
    (LEFT + 600, 608.0, "skipped / blocked", AMBER, 12.3, "explicit status, no fake success",
     "no target · execute=false · blocking check · cap hit"),
]


def build(animated: bool) -> str:
    body: list[str] = []
    lights: list[str] = []

    def add(*parts: str) -> None:
        body.extend(parts)

    add(
        '<defs><linearGradient id="bg" x1="0" y1="0" x2="0.35" y2="1">'
        '<stop offset="0" stop-color="#0A1020"/>'
        '<stop offset="0.55" stop-color="#0B1222"/>'
        '<stop offset="1" stop-color="#0E1728"/></linearGradient>'
        '<radialGradient id="halo" cx="0.5" cy="0.5" r="0.5">'
        f'<stop offset="0" stop-color="{TEAL}" stop-opacity="0.55"/>'
        f'<stop offset="1" stop-color="{TEAL}" stop-opacity="0"/></radialGradient></defs>',
        f'<rect width="{W}" height="{H}" fill="url(#bg)"/>',
    )

    # ── header ────────────────────────────────────────────────────────────
    add(
        txt(LEFT, 30, "NL2SQL DATA ENGINEERING AGENT", 9.5, DIM, 600, family=MONO, spacing=2.4),
        txt(
            LEFT,
            58,
            "Request lifecycle: one fixed path, five nodes, four guard rails",
            19,
            INK,
            600,
        ),
        txt(RIGHT, 30, "architecture illustration · not telemetry", 9, DIM, anchor="end"),
        ln(f"M {LEFT} 74 H {RIGHT}", FAINT, 1),
    )

    # ── 01 entry surfaces ─────────────────────────────────────────────────
    add(section(100, "01", "entry surfaces"))
    gap = 14.0
    chip_w = (INNER - gap * 3) / 4
    chip_y, chip_h = 110.0, 50.0
    centres = []
    for i, (title, sub) in enumerate(CHIPS):
        x = LEFT + i * (chip_w + gap)
        centres.append(x + chip_w / 2)
        add(
            box(x, chip_y, chip_w, chip_h, 9),
            box(x, chip_y, 3, chip_h, 1.5, TEAL, TEAL, 0),
            txt(x + 16, chip_y + 21, title, 11, INK, 600),
            txt(x + 16, chip_y + 38, sub, 8.8, MUT, family=MONO),
        )
        if animated:
            lights.append(
                glow(x + 8, chip_y, chip_w, chip_h, TEAL, 0.15 + i * 0.22, 1.0 + i * 0.22)
            )

    bus_y = 172.0
    for cx in centres:
        add(ln(f"M {cx:.0f} {chip_y + chip_h} V {bus_y}"))
    add(
        ln(f"M {centres[0]:.0f} {bus_y} H {centres[-1]:.0f}"),
        ln(f"M 640 {bus_y} V 218"),
        head(640, 220, "down", MUT),
    )

    # ── 02 authenticate, validate, bind ───────────────────────────────────
    add(section(212, "02", "authenticate · validate · bind"))
    bind_y, bind_h = 222.0, 60.0
    add(box(LEFT, bind_y, INNER, bind_h, 9, "#0E1626"))
    step_w = (INNER - 24) / 3
    for i, (title, sub, tag) in enumerate(BIND_STEPS):
        x = LEFT + 12 + i * step_w
        add(
            box(x + 6, bind_y + 10, step_w - 24, bind_h - 20, 7, PANEL_HI),
            txt(x + 18, bind_y + 30, f"{i + 1}  {title}", 10.5, INK, 600),
            txt(x + 18, bind_y + 46, sub, 8.6, MUT, family=MONO),
            txt(x + step_w - 30, bind_y + 30, tag, 8, DIM, family=MONO, anchor="end"),
        )
        if i < 2:
            ax = x + step_w - 16
            add(ln(f"M {ax - 4} {bind_y + 30} H {ax + 6}"), head(ax + 8, bind_y + 30, "right", MUT))
        if animated:
            lights.append(
                glow(
                    x + 6,
                    bind_y + 10,
                    step_w - 24,
                    bind_h - 20,
                    TEAL,
                    1.5 + i * 0.4,
                    2.5 + i * 0.4,
                )
            )

    # ── 03 control plane ──────────────────────────────────────────────────
    add(section(308, "03", "control plane — the model never picks the next step"))
    ctl_x, ctl_y, ctl_h = 110.0, 318.0, 92.0
    card_y, card_h = ctl_y + 26, ctl_h - 38
    add(
        box(ctl_x, ctl_y, RIGHT - ctl_x, ctl_h, 10, "#0D1524"),
        txt(ctl_x + 14, ctl_y + 18, "CONTROL", 8, DIM, 700, family=MONO, spacing=1.6),
    )
    for i, (x, w, title, rows) in enumerate(CONTROL_CARDS):
        add(
            box(x, card_y, w, card_h, 7),
            txt(x + 12, ctl_y + 44, title, 10.5, INK, 600, family=SANS if i == 0 else MONO),
            block(x + 12, ctl_y + 58, rows, 8.4, MUT, 10.5, MONO),
        )
        if animated:
            lights.append(glow(x, card_y, w, card_h, BLUE, 2.9 + i * 0.3, 3.9 + i * 0.3))
    for i in range(3):
        ax = CONTROL_CARDS[i][0] + CONTROL_CARDS[i][1] + 4
        add(ln(f"M {ax} {ctl_y + 53} H {ax + 16}"), head(ax + 18, ctl_y + 53, "right", MUT))

    # ── 04 five nodes ─────────────────────────────────────────────────────
    add(section(438, "04", "five nodes, fixed order — a failed node aborts, it does not reroute"))
    node_gap = 15.0
    node_w = (INNER - node_gap * 4) / 5
    node_y, node_h = 472.0, 190.0
    node_x = [LEFT + i * (node_w + node_gap) for i in range(5)]
    node_mid = [x + node_w / 2 for x in node_x]

    rail_y = 456.0
    add(
        ln(f"M 150 {ctl_y + ctl_h} V {rail_y}", FAINT, 1.2),
        ln(f"M 150 {rail_y} H {node_mid[-1]:.1f}", FAINT, 1.2),
        txt(
            RIGHT,
            rail_y - 8,
            "dispatched by the runner in node_order",
            8,
            DIM,
            family=MONO,
            anchor="end",
        ),
    )

    for i, (name, role, facts, out) in enumerate(NODES):
        x, cx = node_x[i], node_mid[i]
        add(
            box(x, node_y, node_w, node_h, 10),
            box(x, node_y, node_w, 3, 1.5, TEAL, TEAL, 0),
            txt(x + 14, node_y + 30, f"0{i + 1}", 9.5, TEAL, 700, family=MONO),
            txt(x + 40, node_y + 30, name, 12.5, INK, 600, family=MONO),
            txt(x + 14, node_y + 48, role, 8.8, DIM),
            ln(f"M {x + 14} {node_y + 58} H {x + node_w - 14}", FAINT, 1),
            block(x + 14, node_y + 78, facts, 9.2, MUT, 16),
            ln(f"M {x + 14} {node_y + 146} H {x + node_w - 14}", FAINT, 1),
            txt(x + 14, node_y + 160, "emits", 8, DIM, family=MONO),
            txt(x + 52, node_y + 160, out, 8.6, TEAL_SOFT, family=MONO),
            ln(f"M {cx:.1f} {rail_y} V {node_y - 2}", FAINT, 1),
            head(cx, node_y - 1, "down", FAINT),
        )
        if i < 4:
            ax = x + node_w + 2
            add(ln(f"M {ax} {node_y + node_h / 2} H {ax + 9}"))
            add(head(ax + 11, node_y + node_h / 2, "right", MUT))
        if animated:
            lights.append(glow(x, node_y, node_w, node_h, TEAL, 4.95 + i * 1.2, 6.05 + i * 1.2))

    # ── 05 guard rails ────────────────────────────────────────────────────
    add(ln(f"M {LEFT} 678 H {RIGHT}", FAINT, 1, "4 5"))
    for cx in node_mid:
        add(ln(f"M {cx:.1f} {node_y + node_h + 2} V 678", FAINT, 1, "3 4"))
    add(section(702, "05", "guard rails — checked per step, unlisted means denied"))
    guard_w = (INNER - 3 * 12) / 4
    guard_y, guard_h = 712.0, 54.0
    for i, (title, sub) in enumerate(GUARDS):
        x = LEFT + i * (guard_w + 12)
        add(
            box(x, guard_y, guard_w, guard_h, 8, "#111925", "#3A4A63"),
            txt(x + 12, guard_y + 22, title, 9.8, INK, 600, family=MONO),
            txt(x + 12, guard_y + 38, sub, 8.4, MUT),
        )
        if animated:
            lights.append(
                glow(x, guard_y, guard_w, guard_h, AMBER, 10.9 + i * 0.12, 11.9 + i * 0.12)
            )

    # ── 06 conditional execution ──────────────────────────────────────────
    add(section(792, "06", "execution is conditional — the outcome is always explicit"))
    lane_y, lane_h = 802.0, 62.0
    for x, w, title, colour, t0, chip, sub in LANES:
        add(
            box(x, lane_y, w, lane_h, 9, "#101A28", "#3A4A63"),
            box(x, lane_y, 3, lane_h, 1.5, colour, colour, 0),
            txt(x + 16, lane_y + 24, title, 10.8, INK, 600),
            txt(x + 16, lane_y + 42, sub, 8.6, MUT, family=MONO),
            txt(x + w - 14, lane_y + 24, chip, 8.6, colour, 500, family=MONO, anchor="end"),
        )
        if animated:
            lights.append(glow(x, lane_y, w, lane_h, colour, t0, t0 + 0.9))

    # ── 07 one contract ───────────────────────────────────────────────────
    add(section(890, "07", "one contract, three surfaces"))
    out_y, out_h = 900.0, 62.0
    add(
        box(LEFT, out_y, INNER, out_h, 9),
        txt(LEFT + 16, out_y + 24, "QueryResponse", 11, INK, 600, family=MONO),
        txt(
            RIGHT - 16,
            out_y + 25,
            "trace → MySQL · optional SQL-free audit",
            8.4,
            DIM,
            family=MONO,
            anchor="end",
        ),
        ln(f"M {LEFT + 16} {out_y + 40} H {RIGHT - 16}", FAINT, 1),
        txt(RIGHT - 16, out_y + 54, "one status per request · errors carry a code", 8.4, DIM,
            anchor="end"),
    )
    field_x = LEFT + 150
    for field in ["status", "candidates[].sql", "evidence", "validation", "execution", "usage"]:
        width = 12 + len(field) * 6.2
        add(
            box(field_x, out_y + 11, width, 20, 5, "#16233A", "#31425C"),
            txt(field_x + width / 2, out_y + 25, field, 8.6, TEAL_SOFT, family=MONO,
                anchor="middle"),
        )
        field_x += width + 8
    surface_x = LEFT + 16
    for surface in ["JSON", "SSE event per node", "MCP tool result"]:
        add(
            txt(surface_x + 4, out_y + 54, "▸", 8, TEAL),
            txt(surface_x + 14, out_y + 54, surface, 8.6, MUT, family=MONO),
        )
        surface_x += 12 + len(surface) * 6.4 + 22
    if animated:
        lights.append(glow(LEFT, out_y, INNER, out_h, TEAL, 12.9, 13.8))

    # ── status legend ─────────────────────────────────────────────────────
    legend_y = 982.0
    legend = [
        (LEFT, GREEN, "completed", "SQL and evidence returned"),
        (LEFT + 420, RED, "failed", "a node aborted · errors[] carries a code"),
        (LEFT + 860, AMBER, "skipped / blocked", "execution not attempted · said so"),
    ]
    for x, colour, name, gloss in legend:
        add(
            f'<circle cx="{x + 4:.1f}" cy="{legend_y - 3.5}" r="3.4" fill="{colour}" '
            f'opacity="0.9"/>',
            txt(x + 14, legend_y, name, 9.4, INK, 600, family=MONO),
            txt(x + 24 + len(name) * 6.0, legend_y, gloss, 8.6, DIM),
        )

    # ── footer ────────────────────────────────────────────────────────────
    add(
        ln(f"M {LEFT} {H - 26} H {RIGHT}", FAINT, 1),
        txt(
            LEFT,
            H - 10,
            "Rendered from scripts/render_runtime_flow.py · the plan YAML decides order; "
            "the model only writes SQL",
            8.6,
            DIM,
        ),
        txt(RIGHT, H - 10, "read-only · request-scoped · fail explicit", 8.6, DIM,
            family=MONO, anchor="end"),
    )

    # ── motion: travelling request and highlight envelope ─────────────────
    travelling = ""
    if animated:
        span = node_mid[-1] - 150
        steps: list[tuple[float, float]] = [(0.0, 0.0), (3.95, 0.0)]
        for i, cx in enumerate(node_mid):
            position = (cx - 150) / span
            arrival = 5.0 + i * 1.2
            steps.append((arrival, position))
            if i < 4:
                steps.append((arrival + 0.8, position))
        steps.append((DUR, 1.0))
        kp = ";".join(f"{min(1.0, max(0.0, p)):.4f}" for _, p in steps)
        kt = ";".join(f"{min(1.0, max(0.0, t / DUR)):.4f}" for t, _ in steps)
        d_path = f"M 150 {rail_y} H {node_mid[-1]:.1f}"
        travelling = (
            '<g opacity="0"><animate attributeName="opacity" values="0;0;0.4;0.4;0;0" '
            'keyTimes="0;0.26;0.28;0.78;0.80;1" dur="14s" repeatCount="indefinite"/>'
            f'<circle r="11" fill="url(#halo)"><animateMotion dur="14s" begin="-0.08s" '
            f'repeatCount="indefinite" calcMode="linear" keyPoints="{kp}" keyTimes="{kt}" '
            f'path="{d_path}"/></circle></g>'
            '<g opacity="0"><animate attributeName="opacity" values="0;0;1;1;0;0" '
            'keyTimes="0;0.27;0.29;0.78;0.80;1" dur="14s" repeatCount="indefinite"/>'
            f'<circle r="3.6" fill="#0B1222" stroke="{TEAL_SOFT}" stroke-width="1.6">'
            f'<animateMotion dur="14s" repeatCount="indefinite" calcMode="linear" '
            f'keyPoints="{kp}" keyTimes="{kt}" path="{d_path}"/></circle></g>'
        )

    return "\n".join(
        [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
            f'viewBox="0 0 {W} {H}" role="img" aria-labelledby="title desc">',
            '<title id="title">Request lifecycle of the NL2SQL data engineering agent</title>',
            '<desc id="desc">Four entry surfaces converge on one authenticated request. A fixed '
            "YAML plan drives WorkflowRunner through parse_nl, schema_linking, generate_sql, "
            "validate_sql and execute_sql. Permission, constraint, SQL-policy and LLM-budget "
            "guard rails apply per step. Execution is conditional; a skipped or blocked run still "
            "produces an explicit status. Every surface shares one QueryResponse contract and a "
            "persisted trace.</desc>",
            *body,
            *([f'<g class="motion">{"".join(lights)}{travelling}</g>'] if animated else []),
            "</svg>",
        ]
    )


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    for name, animated in (("runtime-flow.svg", True), ("runtime-flow-static.svg", False)):
        # newline="\n" keeps the committed bytes identical on Windows and Linux.
        (ASSETS / name).write_text(build(animated), encoding="utf-8", newline="\n")
    for name in ("runtime-flow.svg", "runtime-flow-static.svg"):
        size = (ASSETS / name).stat().st_size
        print(f"wrote assets/{name} ({size / 1024:.1f} KiB)")


if __name__ == "__main__":
    main()
