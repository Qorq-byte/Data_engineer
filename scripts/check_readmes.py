"""Check the four README language views stay in step.

Run from the repository root::

    python scripts/check_readmes.py

``README.md`` is the English entry; ``README.zh-CN.md``, ``README.zh-TW.md`` and
``README.ja.md`` are the Simplified Chinese, Traditional Chinese and Japanese mirrors.
The checker compares their section counts and relative link targets, resolves local
links and anchors, rejects a duplicated English page or a concatenated bilingual
page, and verifies that the static diagram is motion-free and stays one page tall.
Standard library only.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
BODIES = {
    "README.md": "## Overview",
    "README.zh-CN.md": "## 项目简介",
    "README.zh-TW.md": "## 專案簡介",
    "README.ja.md": "## 概要",
}
FILES = list(BODIES)

LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
FENCE = re.compile(r"^```")


def strip_code(text: str) -> str:
    lines: list[str] = []
    inside = False
    for line in text.splitlines():
        if FENCE.match(line.strip()):
            inside = not inside
            lines.append("")
            continue
        lines.append("" if inside else line)
    return "\n".join(lines)


def headings(text: str) -> list[tuple[int, str]]:
    result: list[tuple[int, str]] = []
    for line in strip_code(text).splitlines():
        match = HEADING.match(line)
        if match:
            result.append((len(match.group(1)), match.group(2).strip()))
    return result


def slug(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"`", "", value)
    value = re.sub(r"[^\w\s\u4e00-\u9fff\u3040-\u30ff-]", "", value, flags=re.UNICODE)
    return re.sub(r"\s+", "-", value)


def anchors(text: str) -> set[str]:
    seen: dict[str, int] = {}
    result: set[str] = set()
    for _, title in headings(text):
        base = slug(title)
        count = seen.get(base, 0)
        result.add(base if count == 0 else f"{base}-{count}")
        seen[base] = count + 1
    return result


def h2(text: str) -> list[str]:
    return [title for level, title in headings(text) if level == 2]


def body_targets(text: str, start: str) -> list[str]:
    """Relative link targets inside one language body, ignoring navigation and anchors."""
    body = text[text.index(start) :]
    targets: list[str] = []
    for raw in LINK.findall(strip_code(body)):
        if raw.startswith(("http://", "https://", "mailto:", "#")):
            continue
        targets.append(urlparse(unquote(raw)).path)
    return sorted(targets)


def check_links(path: Path, problems: list[str]) -> None:
    text = strip_code(path.read_text(encoding="utf-8"))
    local_anchors = anchors(text)
    for raw in LINK.findall(text):
        target = raw.strip()
        if target.startswith(("<", "http://", "https://", "mailto:")):
            continue
        decoded = unquote(target)
        if decoded.startswith("#"):
            name = decoded[1:]
            if name and name not in local_anchors:
                problems.append(f"{path.name}: missing anchor {target}")
            continue
        parsed = urlparse(decoded)
        candidate = (ROOT / parsed.path).resolve()
        if not candidate.exists():
            problems.append(f"{path.name}: missing file {parsed.path}")
            continue
        if parsed.fragment and candidate.suffix == ".md":
            other = strip_code(candidate.read_text(encoding="utf-8"))
            if parsed.fragment not in anchors(other):
                problems.append(f"{path.name}: missing anchor {target} in {parsed.path}")


def main() -> None:
    problems: list[str] = []
    if (ROOT / "README.en.md").exists():
        problems.append("README.en.md is a duplicate of the English README; remove it")
    texts = {name: (ROOT / name).read_text(encoding="utf-8") for name in FILES}

    for name, text in texts.items():
        levels = [level for level, _ in headings(text)]
        if levels.count(1) != 1:
            problems.append(f"{name}: expected exactly one level-1 title, found {levels.count(1)}")
    if re.search(r"^# 简体中文\s*$", texts["README.md"], re.MULTILINE):
        problems.append(
            "README.md: Chinese section found; keep the languages in separate pages"
        )

    counts = {name: len(h2(text)) for name, text in texts.items()}
    if len(set(counts.values())) != 1:
        detail = ", ".join(f"{name}={count}" for name, count in counts.items())
        problems.append(f"section counts differ: {detail}")

    en_targets = body_targets(texts["README.md"], BODIES["README.md"])
    for name in FILES[1:]:
        targets = body_targets(texts[name], BODIES[name])
        if targets != en_targets:
            only_en = [item for item in en_targets if item not in targets]
            only_other = [item for item in targets if item not in en_targets]
            problems.append(
                f"body links differ for {name}: only English {only_en}; only this view {only_other}"
            )

    for name in FILES:
        check_links(ROOT / name, problems)

    animated = (ROOT / "assets/runtime-flow.svg").read_text(encoding="utf-8")
    static = (ROOT / "assets/runtime-flow-static.svg").read_text(encoding="utf-8")
    if "<animate" not in animated:
        problems.append("assets/runtime-flow.svg: no animation found")
    if "<animate" in static:
        problems.append("assets/runtime-flow-static.svg: still contains animation")
    box = re.search(r'viewBox="0 0 (\d+) (\d+)"', static)
    if not box or int(box.group(2)) > 500:
        problems.append(
            "assets/runtime-flow-static.svg: taller than 500 px; keep the one-page layout"
        )

    if problems:
        print("README checks failed:")
        for item in problems:
            print("  -", item)
        raise SystemExit(1)
    sections = next(iter(counts.values()))
    print(
        f"README checks passed: {len(FILES)} language views, {sections} sections each, "
        "all local links resolve, diagram is one page and motion-free when static."
    )


if __name__ == "__main__":
    main()
