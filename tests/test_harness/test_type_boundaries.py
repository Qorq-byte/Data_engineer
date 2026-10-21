"""Runtime boundary failures must stay explicit while core types are tightened."""

import os

import pytest
import yaml

from app.harness.config_loader import ConfigLoader
from app.harness.plan_loader import PlanLoader


@pytest.mark.parametrize("raw", [None, [], 42, {"agent": []}, {"agent": None}])
def test_agent_loader_rejects_nonmapping_input(tmp_path, raw):
    path = tmp_path / "agent.yml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="must be a mapping"):
        ConfigLoader(str(path)).load()


@pytest.mark.parametrize("raw", [None, [], 42, {"plan": []}, {"plan": None}])
def test_plan_loader_rejects_nonmapping_input_without_caching_it(tmp_path, raw):
    path = tmp_path / "request.yml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    loader = PlanLoader(str(tmp_path))
    with pytest.raises(ValueError, match="must be a mapping"):
        loader.load("request")
    path.write_text("id: request\nname: Recovered\n", encoding="utf-8")
    assert loader.load("request").name == "Recovered"


def test_bad_hot_reload_raises_and_valid_config_can_recover(tmp_path):
    path = tmp_path / "agent.yml"
    path.write_text("agent: {name: first}\n", encoding="utf-8")
    loader = ConfigLoader(str(path))
    assert loader.load().name == "first"

    def rewrite(text):
        previous = path.stat()
        path.write_text(text, encoding="utf-8")
        os.utime(path, ns=(previous.st_atime_ns, previous.st_mtime_ns + 1_000_000_000))

    rewrite("agent: []\n")
    with pytest.raises(ValueError, match="must be a mapping"):
        loader.load()
    rewrite("agent: {name: recovered}\n")
    assert loader.load().name == "recovered"
