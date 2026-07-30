"""Tests for SkillManager + SkillFuncTool.

Real YAML files are written to tmp_path — no mocks.
"""

import pytest

from app.harness.skill_manager import SkillDef, SkillFuncTool, SkillManager

# ── Fixtures / helpers ──────────────────────────────────────────────────


def write_skill(path, name=None, description="", tools=None, instructions=""):
    """Write a skill YAML file with the given fields."""
    lines = []
    if name is not None:
        lines.append(f"name: {name}")
    if description:
        lines.append(f"description: {description}")
    if tools:
        lines.append("tools:")
        lines.extend(f"  - {t}" for t in tools)
    if instructions:
        lines.append("instructions: |")
        lines.extend(f"  {line}" for line in instructions.splitlines())
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture
def skills_dir(tmp_path):
    """A skills directory with two top-level skills and one subdir skill."""
    d = tmp_path / "skills"
    d.mkdir()
    write_skill(
        d / "sql_optimizer.yaml",
        name="sql_optimizer",
        description="Optimize slow SQL",
        tools=["explain_plan", "execute_read_query"],
        instructions="Analyze the EXPLAIN output.\nRewrite the query.",
    )
    write_skill(
        d / "term_lookup.yml",
        name="term_lookup",
        description="Look up business terms",
        tools=["lookup_term"],
        instructions="Use the glossary.",
    )
    sub = d / "report_builder"
    sub.mkdir()
    write_skill(
        sub / "skill.yaml",
        name="report_builder",
        description="Build reports",
        tools=["execute_read_query"],
        instructions="Assemble a report.",
    )
    return d


# ── Discovery ───────────────────────────────────────────────────────────


def test_discover_missing_dir_returns_empty(tmp_path):
    manager = SkillManager(skills_dir=str(tmp_path / "does_not_exist"))
    assert manager.discover() == []
    assert manager.list_all() == []


def test_discover_finds_all_skills(skills_dir):
    manager = SkillManager(skills_dir=str(skills_dir))
    skills = manager.discover()
    names = {s.name for s in skills}
    assert names == {"sql_optimizer", "term_lookup", "report_builder"}


def test_discover_yml_extension(skills_dir):
    manager = SkillManager(skills_dir=str(skills_dir))
    manager.discover()
    assert manager.get("term_lookup") is not None


def test_discover_subdir_skill_yaml(skills_dir):
    manager = SkillManager(skills_dir=str(skills_dir))
    manager.discover()
    skill = manager.get("report_builder")
    assert skill is not None
    assert skill.source_path.endswith("skill.yaml")


def test_discover_subdir_skill_yml(tmp_path):
    sub = tmp_path / "skills" / "digest"
    sub.mkdir(parents=True)
    write_skill(sub / "skill.yml", name="digest", instructions="Summarize.")
    manager = SkillManager(skills_dir=str(tmp_path / "skills"))
    skills = manager.discover()
    assert [s.name for s in skills] == ["digest"]


def test_discover_ignores_non_yaml_files(tmp_path):
    d = tmp_path / "skills"
    d.mkdir()
    (d / "readme.txt").write_text("not a skill", encoding="utf-8")
    (d / "notes.md").write_text("# notes", encoding="utf-8")
    write_skill(d / "real.yaml", name="real", instructions="x")
    manager = SkillManager(skills_dir=str(d))
    assert [s.name for s in manager.discover()] == ["real"]


def test_discover_skips_malformed_yaml(tmp_path):
    d = tmp_path / "skills"
    d.mkdir()
    (d / "broken.yaml").write_text("name: [unclosed", encoding="utf-8")
    write_skill(d / "ok.yaml", name="ok", instructions="fine")
    manager = SkillManager(skills_dir=str(d))
    assert [s.name for s in manager.discover()] == ["ok"]


def test_discover_skips_non_mapping_yaml(tmp_path):
    d = tmp_path / "skills"
    d.mkdir()
    (d / "list.yaml").write_text("- a\n- b\n", encoding="utf-8")
    (d / "scalar.yaml").write_text("just a string\n", encoding="utf-8")
    manager = SkillManager(skills_dir=str(d))
    assert manager.discover() == []


def test_discover_registers_skills(skills_dir):
    manager = SkillManager(skills_dir=str(skills_dir))
    manager.discover()
    assert manager.get("sql_optimizer") is not None
    assert len(manager.list_all()) == 3


def test_parsed_fields(skills_dir):
    manager = SkillManager(skills_dir=str(skills_dir))
    manager.discover()
    skill = manager.get("sql_optimizer")
    assert skill.description == "Optimize slow SQL"
    assert skill.tools == ["explain_plan", "execute_read_query"]
    assert "EXPLAIN" in skill.instructions
    assert skill.source_path.endswith("sql_optimizer.yaml")


def test_default_name_from_file_stem(tmp_path):
    d = tmp_path / "skills"
    d.mkdir()
    write_skill(d / "unnamed_skill.yaml", instructions="no name field")
    manager = SkillManager(skills_dir=str(d))
    skills = manager.discover()
    assert skills[0].name == "unnamed_skill"


def test_default_name_from_subdir_name(tmp_path):
    sub = tmp_path / "skills" / "from_dir"
    sub.mkdir(parents=True)
    write_skill(sub / "skill.yaml", instructions="no name field")
    manager = SkillManager(skills_dir=str(tmp_path / "skills"))
    skills = manager.discover()
    assert skills[0].name == "from_dir"


# ── Registry: register / get / load / list_all ──────────────────────────


def test_register_and_get():
    manager = SkillManager(skills_dir="./nonexistent")
    skill = SkillDef(name="manual", description="added by hand", instructions="do it")
    manager.register(skill)
    assert manager.get("manual") is skill


def test_register_overwrites_same_name():
    manager = SkillManager(skills_dir="./nonexistent")
    manager.register(SkillDef(name="dup", instructions="v1"))
    manager.register(SkillDef(name="dup", instructions="v2"))
    assert manager.get("dup").instructions == "v2"
    assert len(manager.list_all()) == 1


def test_get_missing_returns_none():
    manager = SkillManager(skills_dir="./nonexistent")
    assert manager.get("ghost") is None


def test_load_triggers_discovery(skills_dir):
    manager = SkillManager(skills_dir=str(skills_dir))
    # Not discovered yet — load() should scan the directory on demand.
    skill = manager.load("sql_optimizer")
    assert skill.name == "sql_optimizer"


def test_load_missing_raises_keyerror(tmp_path):
    manager = SkillManager(skills_dir=str(tmp_path))
    with pytest.raises(KeyError):
        manager.load("ghost")


def test_list_all_returns_all_registered(skills_dir):
    manager = SkillManager(skills_dir=str(skills_dir))
    manager.discover()
    manager.register(SkillDef(name="extra", instructions="x"))
    assert {s.name for s in manager.list_all()} == {
        "sql_optimizer",
        "term_lookup",
        "report_builder",
        "extra",
    }


# ── load_from_config ────────────────────────────────────────────────────


def test_load_from_config_auto_mode(skills_dir):
    config = {"mode": "auto", "directory": str(skills_dir), "preload": []}
    manager = SkillManager.load_from_config(config)
    assert len(manager.list_all()) == 3


def test_load_from_config_manual_preload(skills_dir):
    config = {
        "mode": "manual",
        "directory": str(skills_dir),
        "preload": ["sql_optimizer"],
    }
    manager = SkillManager.load_from_config(config)
    assert [s.name for s in manager.list_all()] == ["sql_optimizer"]
    assert manager.get("term_lookup") is None


def test_load_from_config_full_agent_yml_shape(skills_dir):
    """Accepts the full agent.yml mapping (agent.skills nesting)."""
    config = {
        "agent": {
            "name": "NL2SQL Agent",
            "skills": {"mode": "auto", "directory": str(skills_dir), "preload": []},
        }
    }
    manager = SkillManager.load_from_config(config)
    assert len(manager.list_all()) == 3


def test_load_from_config_defaults(tmp_path, monkeypatch):
    """Empty config falls back to mode=auto, directory=./skills."""
    monkeypatch.chdir(tmp_path)  # no ./skills here — must not raise
    manager = SkillManager.load_from_config({})
    assert manager.list_all() == []


# ── SkillFuncTool ───────────────────────────────────────────────────────


def test_skill_func_tool_call_returns_instructions():
    skill = SkillDef(
        name="prompted",
        description="a prompt skill",
        instructions="Step 1: read schema.\nStep 2: write SQL.",
    )
    tool = SkillFuncTool(skill)
    assert tool() == "Step 1: read schema.\nStep 2: write SQL."


def test_skill_func_tool_name_and_description():
    skill = SkillDef(name="prompted", description="a prompt skill", instructions="x")
    tool = SkillFuncTool(skill)
    assert tool.name == "prompted"
    assert tool.description == "a prompt skill"


def test_skill_func_tool_ignores_args():
    skill = SkillDef(name="s", instructions="the text")
    tool = SkillFuncTool(skill)
    assert tool("positional", key="value") == "the text"


def test_manager_as_tool(skills_dir):
    manager = SkillManager(skills_dir=str(skills_dir))
    tool = manager.as_tool("report_builder")
    assert isinstance(tool, SkillFuncTool)
    assert tool.name == "report_builder"
    assert tool().strip() == "Assemble a report."
