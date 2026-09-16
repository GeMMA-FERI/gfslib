import json
from typing import Any

import pytest
import requests

from gfslib.storage import StorageServices


@pytest.mark.parametrize("method", ["mkdir", "rmdir"])
@pytest.mark.parametrize("form", ["single", "list", "iterator", "empty"])
@pytest.mark.parametrize("recursive", [False, True])
def test_directory_requests(
    monkeypatch: pytest.MonkeyPatch, method: str, form: str, recursive: bool
) -> None:
    paths: Any = "folder/č"
    expected = [paths]
    if form == "list":
        paths = ["folder/č", "other"]
        expected = paths
    elif form == "iterator":
        paths = iter(["folder/č"])
    elif form == "empty":
        paths, expected = [], []
    payload = [
        {"path": "folder/č", "status": 0, "error": None},
        {"path": "other", "status": 3, "error": "not empty"},
    ]

    def post(url: str, **kwargs: Any) -> requests.Response:
        operation = "create" if method == "mkdir" else "remove"
        assert url == f"https://example.test/directories/{operation}"
        assert kwargs["json"] == expected
        assert kwargs["params"] == (
            {} if method == "mkdir" else {"recursive": str(recursive).lower()}
        )
        assert kwargs["headers"]["X-API-Key"] == "key"
        assert kwargs["timeout"] == 30
        resp = requests.Response()
        resp.status_code = 200
        resp._content = json.dumps(payload).encode()
        return resp

    monkeypatch.setattr(requests, "post", post)
    svc = StorageServices("https://example.test")
    svc.set_api_key("key")
    result = (
        svc.mkdir(paths)
        if method == "mkdir"
        else (svc.rmdir(paths, True) if recursive else svc.rmdir(paths))
    )
    assert result == payload


@pytest.mark.parametrize("method", ["mkdir", "rmdir"])
def test_directory_errors(monkeypatch: pytest.MonkeyPatch, method: str) -> None:
    resp = requests.Response()
    resp.status_code = 403
    monkeypatch.setattr(requests, "post", lambda *a, **kw: resp)
    with pytest.raises(requests.HTTPError):
        getattr(StorageServices("https://example.test"), method)("folder")
