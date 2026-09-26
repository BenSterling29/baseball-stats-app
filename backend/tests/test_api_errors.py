"""The API's error handlers: upstream network failures are 502s, everything
else (including bugs in the app's own math) is a 500, and both return JSON
the frontend can display."""
import pytest
import requests
from fastapi.testclient import TestClient

from app import pybaseball_client as pbc
from app.main import app


@pytest.fixture
def client():
    # raise_server_exceptions=False so the 500 path returns the handler's
    # response instead of re-raising into the test.
    return TestClient(app, raise_server_exceptions=False)


def test_upstream_network_failure_is_502(client, monkeypatch):
    def boom(season):
        raise requests.ConnectionError("baseball-reference.com unreachable")

    monkeypatch.setattr(pbc, "get_pitcher_cwar", boom)
    res = client.get("/api/stats/pitching/cwar?season=2025")
    assert res.status_code == 502
    assert "Upstream data fetch failed" in res.json()["detail"]


def test_internal_bug_is_500_not_upstream(client, monkeypatch):
    def boom(season):
        raise KeyError("GB/FB")

    monkeypatch.setattr(pbc, "get_pitcher_cwar", boom)
    res = client.get("/api/stats/pitching/cwar?season=2025")
    assert res.status_code == 500
    detail = res.json()["detail"]
    assert "KeyError" in detail
    assert "Upstream" not in detail


def test_invalid_season_is_rejected_before_any_fetch(client, monkeypatch):
    called = []
    monkeypatch.setattr(pbc, "get_pitcher_cwar", lambda season: called.append(season))
    res = client.get("/api/stats/pitching/cwar?season=20")
    assert res.status_code == 422
    assert called == []
