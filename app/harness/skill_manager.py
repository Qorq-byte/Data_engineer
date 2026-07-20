"""SkillManager + SkillFuncTool — skill discovery, loading, and tool wrapping.

See SPEC §4.12.3 (④ SkillManager + SkillFuncTool) and §3.4.5 (agent.yml skills).

A Skill is a declarative YAML definition (name, description, tools,
instructions) living under a skills directory:

    skills/
    ├── sql_optimizer.yaml          # top-level skill file
    └── report_builder/
        └── skill.yaml              # subdirectory skill file

SkillManager discovers and registers SkillDef entries; SkillFuncTool wraps
a SkillDef as a callable tool whose invocation returns the skill's
instructions text (prompt injection).
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

SKILL_FILE_NAMES = ("skill.yaml", "skill.yml")
SKILL_SUFFIXES = (".yaml", ".yml")


@dataclass
class SkillDef:
    """A declarative skill definition loaded from YAML."""

    name: str
    description: str = ""
    tools: list[str] = field(default_factory=list)
    instructions: str = ""
    source_path: str = ""


class SkillFuncTool:
    """Wraps a SkillDef as a callable tool.

    Calling the tool returns the skill's instructions text so the skill
    content can be injected into an LLM prompt as-is. Exposes ``name``
    and ``description`` attributes like any other func tool.
    """

    def __init__(self, skill: SkillDef):
        self.skill = skill
        self.name = skill.name
        self.description = skill.description

    def __call__(self, *args: object, **kwargs: object) -> str:
        """Return the skill's instructions (prompt text). Args are ignored."""
        return self.skill.instructions

    def __repr__(self) -> str:
        return f"SkillFuncTool(name={self.name!r})"


class SkillManager:
    """Discovers, loads, and registers skills from a skills directory.

    Skills are YAML files directly under ``skills_dir`` (``*.yaml`` /
    ``*.yml``) or ``skill.yaml`` / ``skill.yml`` files inside first-level
    subdirectories. A missing directory is not an error — discovery simply
    returns an empty list.
    """

    def __init__(self, skills_dir: str = "./skills"):
        self.skills_dir = Path(skills_dir)
        self._skills: dict[str, SkillDef] = {}

    # ── Discovery ─────────────────────────────────────────────────────

    def discover(self) -> list[SkillDef]:
        """Scan the skills directory, register every parseable skill.

        Returns the list of discovered SkillDef objects. Unparseable or
        non-mapping YAML files are skipped silently. If the directory
        does not exist, returns an empty list.
        """
        discovered = self._scan()
        for skill in discovered:
            self.register(skill)
        return discovered

    def _scan(self) -> list[SkillDef]:
        """Parse all skill files without registering them."""
        if not self.skills_dir.is_dir():
            return []

        found: list[SkillDef] = []
        for entry in sorted(self.skills_dir.iterdir()):
            if entry.is_file() and entry.suffix in SKILL_SUFFIXES:
                skill = self._parse_file(entry, default_name=entry.stem)
                if skill is not None:
                    found.append(skill)
            elif entry.is_dir():
                for file_name in SKILL_FILE_NAMES:
                    candidate = entry / file_name
                    if candidate.is_file():
                        skill = self._parse_file(candidate, default_name=entry.name)
                        if skill is not None:
                            found.append(skill)
                        break
        return found

    @staticmethod
    def _parse_file(path: Path, default_name: str) -> SkillDef | None:
        """Parse a single YAML skill file into a SkillDef.

        Returns None for unreadable, malformed, or non-mapping YAML.
        """
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            return None
        if not isinstance(raw, dict):
            return None

        tools_raw = raw.get("tools") or []
        if isinstance(tools_raw, str):
            tools = [tools_raw]
        elif isinstance(tools_raw, list):
            tools = [str(t) for t in tools_raw]
        else:
            tools = []

        return SkillDef(
            name=str(raw.get("name") or default_name),
            description=str(raw.get("description") or ""),
            tools=tools,
            instructions=str(raw.get("instructions") or ""),
            source_path=str(path),
        )

    # ── Registry access ───────────────────────────────────────────────

    def register(self, skill: SkillDef) -> None:
        """Register (or replace) a skill by name."""
        self._skills[skill.name] = skill

    def get(self, name: str) -> SkillDef | None:
        """Return the registered skill or None if unknown."""
        return self._skills.get(name)

    def load(self, name: str) -> SkillDef:
        """Return the skill by name, discovering from disk if needed.

        Raises:
            KeyError: If the skill is not registered and not found on disk.
        """
        if name not in self._skills:
            self.discover()
        if name not in self._skills:
            raise KeyError(f"Skill '{name}' not found in {self.skills_dir}")
        return self._skills[name]

    def list_all(self) -> list[SkillDef]:
        """Return all registered skills."""
        return list(self._skills.values())

    def as_tool(self, name: str) -> SkillFuncTool:
        """Wrap a registered/discoverable skill as a SkillFuncTool."""
        return SkillFuncTool(self.load(name))

    # ── Config-driven construction ────────────────────────────────────

    @classmethod
    def load_from_config(cls, config: dict[str, Any]) -> "SkillManager":
        """Build a SkillManager from an agent.yml-style config dict.

        Accepts either the full agent.yml mapping, the ``agent`` section,
        or the ``skills`` section directly::

            skills:
              mode: auto        # auto | manual
              directory: "./skills"
              preload: []

        - ``mode: auto``   — discover and register every skill found.
        - ``mode: manual`` — register only the skills named in ``preload``.
        """
        section = config or {}
        if isinstance(section.get("agent"), dict):
            section = section["agent"]
        if isinstance(section.get("skills"), dict):
            section = section["skills"]

        directory = section.get("directory", "./skills")
        mode = section.get("mode", "auto")
        preload = section.get("preload") or []

        manager = cls(skills_dir=directory)
        if mode == "manual":
            wanted = set(preload)
            for skill in manager._scan():
                if skill.name in wanted:
                    manager.register(skill)
        else:
            manager.discover()
        return manager
