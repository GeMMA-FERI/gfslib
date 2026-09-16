from typing import Any

import pytest
import requests

from gfslib.storage import StorageServices


@pytest.mark.parametrize("kind", [None, 0, 1, 2])
def test_path_information(monkeypatch: pytest.MonkeyPatch, kind: int | None) -> None:
    payload: dict[str, Any] = {"exists": kind is not None}
    if kind is not None:
        payload["entry"] = {"type": kind, "path": "a b/č"}

    def get(url: str, **kwargs: Any) -> requests.Response:
        import json

        assert url == "https://example.test/storage/path/info"
        assert kwargs["params"] == {"path": "a b/č"}
        assert kwargs["timeout"] == 4
        assert kwargs["headers"]["X-API-Key"] == "key"
        resp = requests.Response()
        resp.status_code = 200
        resp._content = json.dumps(payload).encode()
        return resp

    monkeypatch.setattr(requests, "get", get)
    svc = StorageServices("https://example.test/storage/", timeout=4)
    svc.set_api_key("key")
    assert svc.path_info("a b/č") == payload
    assert svc.exists("a b/č") is (kind is not None)
    assert svc.is_file("a b/č") is (kind == 0)
    assert svc.is_dir("a b/č") is (kind == 1)


@pytest.mark.parametrize("method", ["path_info", "exists", "is_file", "is_dir"])
@pytest.mark.parametrize("status", [401, 403, 500])
def test_path_http_failure(
    monkeypatch: pytest.MonkeyPatch, method: str, status: int
) -> None:
    resp = requests.Response()
    resp.status_code = status
    monkeypatch.setattr(requests, "get", lambda *a, **kw: resp)
    with pytest.raises(requests.HTTPError):
        getattr(StorageServices("https://example.test"), method)("file")
