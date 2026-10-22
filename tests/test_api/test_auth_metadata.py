"""Malformed DB metadata must not turn into a partial authentication record."""

import pytest
from fastapi import HTTPException

from app.api.auth import _auth_column_names


@pytest.mark.parametrize("description", [None, [], [()], [(123,)]])
def test_invalid_auth_metadata_is_explicitly_unavailable(description):
    with pytest.raises(HTTPException) as error:
        _auth_column_names(description)
    assert error.value.status_code == 503


def test_valid_auth_metadata_preserves_column_order():
    assert _auth_column_names([("id", 3), ("password_hash", 253)]) == ["id", "password_hash"]
