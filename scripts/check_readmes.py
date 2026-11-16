"""Check that the three README views agree and every local link resolves.

Run from the repository root::

    python scripts/check_readmes.py

The bilingual ``README.md`` is composed from the English and Simplified Chinese bodies. This checker
compares the section lists, resolves relative links and same-file anchors, and verifies that the
static diagram really is motion-free. Standard library only.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
FILES = ["README.md", "README.en.md", "README.zh-CN.md"]

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
    value = re.sub(r"[^\w\s\u4e00-\u9fff-]", "", value, flags=re.UNICODE)
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


def body_targets(text: str, start: str) -> list[str]:
    """Relative link targets inside one language body, ignoring navigation and anchors."""
    body = text[text.index(start) :]
    targets: list[str] = []
    for raw in LINK.findall(strip_code(body)):
        if raw.startswith(("http://", "https://", "mailto:", "#")):
            continue
        targets.append(urlparse(unquote(raw)).path)
    return sorted(targets)


def main() -> None:
    problems: list[str] = []
    texts = {name: (ROOT / name).read_text(encoding="utf-8") for name in FILES}

    en_sections = h2(texts["README.en.md"])
    zh_sections = h2(texts["README.zh-CN.md"])
    if len(en_sections) != len(zh_sections):
        problems.append(
            f"section count differs: English {len(en_sections)} vs Chinese {len(zh_sections)}"
        )

    en_targets = body_targets(texts["README.en.md"], "## Overview")
    zh_targets = body_targets(texts["README.zh-CN.md"], "## 项目简介")
    if en_targets != zh_targets:
        only_en = [item for item in en_targets if item not in zh_targets]
        only_zh = [item for item in zh_targets if item not in en_targets]
        problems.append(f"body links differ: only English {only_en}; only Chinese {only_zh}")

    combined = h2(texts["README.md"])
    if combined[: len(en_sections)] != en_sections:
        problems.append("README.md: English half does not match README.en.md section order")
    if combined[len(en_sections) :] != zh_sections:
        problems.append("README.md: Chinese half does not match README.zh-CN.md section order")

    for name in FILES:
        if not texts[name].lstrip().startswith("# "):
            problems.append(f"{name}: must start with a level-1 title")
        check_links(ROOT / name, problems)

    animated = (ROOT / "assets/runtime-flow.svg").read_text(encoding="utf-8")
    static = (ROOT / "assets/runtime-flow-static.svg").read_text(encoding="utf-8")
    if "<animate" not in animated:
        problems.append("assets/runtime-flow.svg: no animation found")
    if "<animate" in static:
        problems.append("assets/runtime-flow-static.svg: still contains animation")

    if problems:
        print("README checks failed:")
        for item in problems:
            print("  -", item)
        raise SystemExit(1)
    print(
        f"README checks passed: {len(en_sections)} sections per language, "
        f"{len(combined)} sections in the bilingual view, all local links resolve."
    )


if __name__ == "__main__":
    main()
