"""Render the README's animated workflow and static fallback (requires Pillow).

Run from the repository root: python scripts/render_readme_pipeline.py
This is an explanatory illustration, not a recording or a performance measurement.
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
WIDTH, HEIGHT = 1440, 1000
BG, PANEL, INK, MUTED, ACCENT = "#0b1220", "#152235", "#e9f1fa", "#a9bace", "#5eead4"


def font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in (
        "C:/Windows/Fonts/seguiemj.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


STEPS = [
    (
        "01 / RECEIVE",
        "JSON / SSE / MCP",
        "Validate input + authenticate",
        "Resolve session and database target",
    ),
    (
        "02 / PLAN",
        "Fixed YAML plan",
        "Load request_query.node_order",
        "Runner owns every transition",
    ),
    (
        "03 / PARSE",
        "parse_nl",
        "Extract intent and requirements",
        "Keep the question in request context",
    ),
    (
        "04 / LINK",
        "schema_linking",
        "Tables, columns, keys + glossary",
        "Scoped retrieval / schema context",
    ),
    (
        "05 / GENERATE",
        "generate_sql",
        "LLM produces SQL candidates",
        "Budget + completion evidence + usage",
    ),
    (
        "06 / VALIDATE",
        "validate_sql",
        "Read-only AST + schema checks",
        "Types, plans and business rules",
    ),
    (
        "07 / EXECUTE",
        "execute_sql",
        "Only eligible, authorized SQL",
        "Read-only target + rows + deadline",
    ),
    (
        "08 / RESPOND",
        "Shared query contract",
        "Candidates, checks, rows, errors",
        "JSON result / SSE events / MCP tool",
    ),
]


def render(active: int | None) -> Image.Image:
    canvas = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(canvas)
    draw.text((48, 30), "FROM QUESTION TO EVIDENCE", font=font(32), fill=INK)
    draw.text(
        (48, 79),
        "Deterministic orchestration  /  request-scoped state  /  read-only execution",
        font=font(20),
        fill=MUTED,
    )
    for index, (label, title, line1, line2) in enumerate(STEPS):
        row, col = divmod(index, 4)
        x, y = 48 + col * 342, 160 + row * 245
        color = ACCENT if index == active else "#34485e"
        draw.rounded_rectangle(
            (x, y, x + 316, y + 194), radius=16, fill=PANEL, outline=color, width=3
        )
        draw.text((x + 16, y + 17), label, font=font(16), fill=ACCENT)
        draw.text((x + 16, y + 54), title, font=font(23), fill=INK)
        draw.text((x + 16, y + 105), line1, font=font(16), fill=MUTED)
        draw.text((x + 16, y + 136), line2, font=font(15), fill=MUTED)
        if col < 3:
            draw.line((x + 318, y + 95, x + 338, y + 95), fill=color, width=3)
            draw.polygon([(x + 338, y + 95), (x + 330, y + 89), (x + 330, y + 101)], fill=color)
    draw.line([(1390, 354), (1390, 380), (35, 380), (35, 500), (45, 500)], fill=ACCENT, width=2)
    draw.text((590, 360), "CONTINUE IN FIXED ORDER", font=font(14), fill=MUTED)
    rails = [
        (
            "CONTROL PLANE",
            "YAML node_order + WorkflowRunner  |"
            "  setup_input > execute > update_context > evaluate",
        ),
        (
            "SAFETY GATES",
            "Default-deny tool permissions  |"
            "  read-only SQL  |  physical LLM call budget  |  row and time limits",
        ),
        (
            "BRANCHES",
            "No target / execute=false: execution skipped  |"
            "  failed checks: blocked  |  failure: explicit error",
        ),
        (
            "REQUEST LIFETIME",
            "Cancellation / timeout > stop work > close streams > release leases and query slots",
        ),
        (
            "OBSERVABILITY",
            "Per-attempt tokens, latency and missing reasons  |"
            "  trace  |  optional local usage audit",
        ),
    ]
    for i, (label, description) in enumerate(rails):
        y = 650 + i * 59
        draw.rounded_rectangle((48, y, 1390, y + 48), radius=8, fill=PANEL)
        draw.text((62, y + 13), label, font=font(15), fill=ACCENT)
        draw.text((245, y + 13), description, font=font(16), fill=INK)
    caption = (
        "ARCHITECTURE ILLUSTRATION / NOT LIVE TELEMETRY"
        if active is None
        else f"ILLUSTRATED STEP {active + 1}/8  /  {STEPS[active][1]}"
    )
    draw.text((48, 958), caption, font=font(17), fill=MUTED)
    return canvas


if __name__ == "__main__":
    assets = ROOT / "assets"
    assets.mkdir(exist_ok=True)
    render(None).save(assets / "runtime-flow.png", optimize=True)
    frames = [render(i) for i in range(8)]
    frames[0].save(
        assets / "runtime-flow.gif",
        save_all=True,
        append_images=frames[1:],
        duration=1500,
        loop=0,
        optimize=True,
    )
