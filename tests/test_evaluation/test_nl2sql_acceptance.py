"""A separate 30-case profile must never loosen the legacy four-call gate."""

import copy

import pytest

from app.evaluation.nl2sql_batch import manifest_digest, prepare, validate


def test_new_profile_and_legacy_limits_are_independent():
    from app.evaluation.model_batch import LIMITS

    value = prepare("offline-control")
    assert len(value["cases"]) == 30
    assert value["limits"]["max_calls"] == 30
    assert LIMITS["max_calls"] == 4
    assert validate(value, manifest_digest(value)) == value
    assert all(set(row) == {"id", "question", "split", "tags"} for row in value["cases"])


@pytest.mark.parametrize("key,new", [("max_calls", 31), ("max_calls", True),
                                     ("max_retries", 1), ("max_output_tokens", 1025)])
def test_resigning_manifest_cannot_expand_budget(key, new):
    value = copy.deepcopy(prepare("offline-control"))
    value["limits"][key] = new
    with pytest.raises(ValueError):
        validate(value, manifest_digest(value))
