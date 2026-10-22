"""Authenticated clients for existing functional tests; security tests use raw clients."""

from fastapi.testclient import TestClient

from app.auth.security import create_access_token


def authenticated_client(app):
    return TestClient(app, headers={"Authorization": f"Bearer {create_access_token(1, 'test')}"})
