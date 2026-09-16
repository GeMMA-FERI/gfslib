import json
from typing import Any

import pytest
import requests

from gfslib import GFClient


@pytest.mark.parametrize(
    "method,suffix",
    [
        ("workspaces", "ws"),
        ("workspace", "ws/a%20b%2F%C4%8D"),
        ("datasets", "ws/a%20b%2F%C4%8D/datasets"),
        ("datasources", "ws/a%20b%2F%C4%8D/datasources"),
    ],
)
@pytest.mark.parametrize("api_key", [None, "test-key"])
@pytest.mark.parametrize("status", [200, 401, 404, 500])
def test_discovery_requests(
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    suffix: str,
    api_key: str | None,
    status: int,
) -> None:
    payload: Any = {"uuid": "id", "name": "example", "attributes": [{"name": "value"}]}
    if method != "workspace":
        payload = [payload]

    def get(url: str, **kwargs: Any) -> requests.Response:
        assert url == f"https://example.test/gf/api/{suffix}"
        assert kwargs["timeout"] is None
        headers = {"Accept": "application/json"}
        if api_key:
            headers["X-API-Key"] = api_key
        assert kwargs["headers"] == headers
        resp = requests.Response()
        resp.status_code = status
        resp._content = json.dumps(payload).encode()
        return resp

    monkeypatch.setattr(requests, "get", get)
    client = GFClient("https://example.test/gf/", timeout=None)
    if api_key:
        client.set_api_key(api_key)
    args = [] if method == "workspaces" else ["a b/č"]
    if status == 200:
        assert getattr(client, method)(*args) == payload
    else:
        with pytest.raises(requests.HTTPError):
            getattr(client, method)(*args)


def test_discovery_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    resp = requests.Response()
    resp.status_code = 200
    resp._content = b"not json"
    monkeypatch.setattr(requests, "get", lambda *a, **kw: resp)
    with pytest.raises(ValueError):
        GFClient("https://example.test").workspaces()


@pytest.mark.parametrize("authenticated", [False, True])
def test_storage_factory(authenticated: bool) -> None:
    client = GFClient("https://example.test/gf/", timeout=9)
    if authenticated:
        client.set_api_key("first")
    storage = client.storage("a/b")
    assert storage.base_url == "https://example.test/gf/api/ws/a%2Fb/services/storage"
    assert storage.timeout == 9
    assert storage._headers().get("X-API-Key") == ("first" if authenticated else None)
    client.set_api_key("second")
    assert storage._headers().get("X-API-Key") == ("first" if authenticated else None)
    assert client.storage("other")._headers()["X-API-Key"] == "second"
