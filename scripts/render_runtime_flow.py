# ruff: noqa: E501
"""Render the compact README runtime diagram as animated and static SVG.

Run from the repository root::

    python scripts/render_runtime_flow.py

Writes ``assets/runtime-flow.svg`` (animated) and ``assets/runtime-flow-static.svg`` (motion-free).
Standard library only: no network, no fonts, no model or database access.

The drawing fits one page: a boot line, an entry/admission row, the fixed plan bar, the five
``request_query`` nodes, the response card, and two footnotes for guards and branches.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"

W, H = 1000, 398
DUR = 9.5

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

LEFT, RIGHT = 24.0, 976.0
INNER = RIGHT - LEFT

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
    size: float = 9.0,
    fill: str = INK,
    weight: int = 400,
    anchor: str = "start",
    family: str = SANS,
    spacing: float = 0.0,
) -> str:
    extra = f' letter-spacing="{spacing}"' if spacing else ""
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{fill}" '
        f'font-weight="{weight}" text-anchor="{anchor}" font-family="{family}"{extra}>'
        f"{esc(value)}</text>"
    )


def block(
    x: float,
    y: float,
    rows: list[str],
    size: float = 8.4,
    fill: str = DIM,
    leading: float = 11.0,
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
    rx: float = 7.0,
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


def head(x: float, y: float, direction: str = "right", color: str = FAINT, size: float = 4.4) -> str:
    if direction == "down":
        points = f"{x - size},{y - size * 1.4} {x + size},{y - size * 1.4} {x},{y}"
    elif direction == "right":
        points = f"{x - size * 1.4},{y - size} {x - size * 1.4},{y + size} {x},{y}"
    else:
        points = f"{x + size * 1.4},{y - size} {x + size * 1.4},{y + size} {x},{y}"
    return f'<polygon points="{points}" fill="{color}"/>'


def pulse(t0: float, t1: float, peak: float = 1.0, animated: bool = True) -> str:
    if not animated:
        return ""
    fade_in, fade_out = 0.12, 0.2
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
        f'{box(x, y, w, h, 8, color, "none", 0, 0.05)}'
        f'{box(x, y, w, h, 8, "none", color, 1.4, 0.8)}'
        f"</g>"
    )


def flow(d: str, color: str = TEAL, animated: bool = True, t0: float = 0.0) -> str:
    if not animated:
        return ""
    return (
        f'<g opacity="0.5">{pulse(t0, t0 + 1.2, 0.85, animated)}'
        f'<path d="{d}" fill="none" stroke="{color}" stroke-width="1.4" '
        f'stroke-dasharray="4 14" stroke-linecap="round">'
        f'<animate attributeName="stroke-dashoffset" from="0" to="-36" dur="1.2s" '
        f'repeatCount="indefinite"/></path></g>'
    )


def arrow(x1: float, x2: float, y: float, color: str = FAINT, animated: bool = False, t0: float = 0.0) -> str:
    d = f"M {x1:.1f} {y:.1f} H {x2 - 5:.1f}"
    return "".join(
        [
            ln(d, color, 1.1),
            head(x2, y, "right", color),
            flow(d, color if color != FAINT else TEAL, animated, t0),
        ]
    )


def down_arrow(x: float, y1: float, y2: float, color: str = FAINT, animated: bool = False, t0: float = 0.0) -> str:
    d = f"M {x:.1f} {y1:.1f} V {y2 - 5:.1f}"
    return "".join(
        [
            ln(d, color, 1.1),
            head(x, y2, "down", color),
            flow(d, color if color != FAINT else TEAL, animated, t0),
        ]
    )


def card(
    x: float,
    y: float,
    w: float,
    h: float,
    kicker: str,
    title: str,
    notes: list[str],
    accent: str,
    *,
    animated: bool,
    t0: float,
    t1: float,
    note_y: float,
) -> str:
    return "".join(
        [
            box(x, y, w, h, 7, PANEL_HI, FAINT),
            box(x, y, 2.6, h, 1.3, accent, "none", 0),
            txt(x + 11, y + 15, kicker, 7.8, accent, 700, family=MONO, spacing=1.0),
            txt(x + 11, y + 32, title, 10.2, INK, 600, family=MONO),
            block(x + 11, y + note_y, notes, 8.8, DIM, 11.0, limit=w - 22),
            glow(x, y, w, h, accent, t0, t1, animated),
        ]
    )


def build(animated: bool) -> str:
    out: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
        f'role="img" aria-labelledby="flowTitle flowDesc">',
        '<title id="flowTitle">Compact runtime flow: boot, admit, five nodes, response</title>',
        '<desc id="flowDesc">One-page illustration of the shipped NL2SQL path. A boot line starts '
        "main.py, builds the FastAPI app and runs the lifespan probes. A request passes the operator "
        "gate, validation, admission and session binding. WorkflowRunner walks the fixed node order "
        "parse_nl, schema_linking, generate_sql, validate_sql, execute_sql. JSON and SSE return "
        "QueryResponse; MCP projects it. Guards and failure branches are summarised in two footnotes."
        "</desc>",
        f'<rect width="{W}" height="{H}" fill="#0A1220"/>',
    ]

    # header
    out += [
        txt(LEFT, 36, "Runtime flow", 21, INK, 600),
        txt(
            LEFT,
            55,
            "Boot → request → five nodes → response. A compact illustration of the shipped path, not telemetry.",
            9.2,
            MUT,
        ),
        txt(RIGHT, 36, "request_query.yml · 5 nodes", 8.2, TEAL, 500, anchor="end", family=MONO),
        ln(f"M {LEFT} 66 H {RIGHT}", FAINT, 1),
    ]

    # boot line
    out += [
        txt(LEFT, 84, "BOOT", 7.8, BLUE, 700, family=MONO, spacing=1.2),
        txt(
            LEFT + 38,
            84,
            "python main.py → create_app() → lifespan: catalogs + 10 s probes → :8001 ready",
            8.6,
            MUT,
            family=MONO,
        ),
        txt(
            RIGHT,
            84,
            "probe failure → degrade · SIGTERM → ordered close",
            7.6,
            DIM,
            anchor="end",
            family=MONO,
        ),
        ln(f"M {LEFT} 94 H {RIGHT}", FAINT, 1),
    ]

    # entry row
    out += [
        card(
            LEFT,
            112,
            210,
            56,
            "IN",
            "web · JSON · SSE · MCP",
            ["one request service"],
            TEAL,
            animated=animated,
            t0=0.2,
            t1=0.8,
            note_y=47,
        ),
        card(
            LEFT + 228,
            112,
            320,
            56,
            "GATE",
            "require_operator · QueryRequest",
            ["JWT + allow-list → 401/403 · validation → 422"],
            TEAL,
            animated=animated,
            t0=0.8,
            t1=1.4,
            note_y=47,
        ),
        card(
            LEFT + 566,
            112,
            386,
            56,
            "ADMIT",
            "admission · session · audit",
            ["8 slots → 503 · conflict → 409 · 120 s → 504"],
            TEAL,
            animated=animated,
            t0=1.4,
            t1=2.0,
            note_y=47,
        ),
        arrow(LEFT + 210, LEFT + 228, 140, FAINT, animated, 0.7),
        arrow(LEFT + 548, LEFT + 566, 140, FAINT, animated, 1.3),
    ]

    # plan bar
    out += [
        box(LEFT, 184, INNER, 32, 7, PANEL, PURPLE, 1.0),
        box(LEFT, 184, 2.6, 32, 1.3, PURPLE, "none", 0),
        txt(LEFT + 12, 204, "request_query.yml · WorkflowRunner", 8.8, PURPLE, 700, family=MONO),
        txt(LEFT + 232, 204, "node_order: parse → link → gen → validate → exec", 8.2, MUT, family=MONO),
        txt(RIGHT - 12, 204, "max_retries 0 · abort", 7.8, DIM, anchor="end", family=MONO),
        glow(LEFT, 184, INNER, 32, PURPLE, 2.0, 2.6, animated),
        down_arrow(94, 216, 232, FAINT, animated, 2.3),
    ]

    # node row
    nodes = [
        ("01", "parse_nl", ["nl_text → sqr"], TEAL),
        ("02", "schema_linking", ["sqr → linked tables"], BLUE),
        ("03", "generate_sql", ["schema → candidates"], TEAL),
        ("04", "validate_sql", ["candidates → report"], BLUE),
        ("05", "execute_sql", ["sql → rows"], TEAL),
    ]
    for index, (kicker, title, notes, accent) in enumerate(nodes):
        x = LEFT + index * 152
        t0 = 2.8 + index * 0.5
        out.append(
            card(
                x,
                234,
                140,
                80,
                kicker,
                title,
                notes,
                accent,
                animated=animated,
                t0=t0,
                t1=t0 + 0.5,
                note_y=50,
            )
        )
        if index < 4:
            out.append(arrow(x + 140, x + 152, 274, FAINT, animated, t0 + 0.2))

    out.append(arrow(LEFT + 748, LEFT + 766, 274, FAINT, animated, 5.4))
    out.append(
        card(
            LEFT + 766,
            234,
            186,
            80,
            "OUT",
            "QueryResponse",
            ["status · SQL · evidence", "trace persisted"],
            GREEN,
            animated=animated,
            t0=5.6,
            t1=6.2,
            note_y=50,
        )
    )

    # guards and branches
    out += [
        txt(LEFT, 338, "GUARDS", 7.6, AMBER, 700, family=MONO, spacing=1.1),
        txt(
            LEFT + 54,
            338,
            "PermissionManager (default deny) · ConstraintEnforcer (10 LLM calls · rows · timeout) · SQL policy (1 statement, AST)",
            8.4,
            MUT,
        ),
        glow(LEFT - 4, 326, INNER + 8, 16, AMBER, 6.6, 7.2, animated),
        txt(LEFT, 356, "BRANCHES", 7.6, RED, 700, family=MONO, spacing=1.1),
        txt(
            LEFT + 66,
            356,
            "execute=false → skipped · validation fail → blocked · node error → abort · RAG/LLM unavailable → warning · 401/403/422/503/409/504",
            8.4,
            MUT,
        ),
        glow(LEFT - 4, 344, INNER + 8, 16, RED, 7.2, 7.8, animated),
    ]

    # footer
    out += [
        ln(f"M {LEFT} 370 H {RIGHT}", FAINT, 0.8),
        txt(
            LEFT,
            386,
            "Generated by scripts/render_runtime_flow.py · not telemetry · docs/runtime-flow.md",
            7.2,
            DIM,
            family=MONO,
        ),
        txt(
            RIGHT,
            386,
            "pre-1.0 MVP · one administrator group",
            7.2,
            DIM,
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
