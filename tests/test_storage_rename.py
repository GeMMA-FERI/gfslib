from typing import Any

import pytest
import requests

from gfslib.storage import StorageServices


@pytest.mark.parametrize("overwrite", [False, True])
@pytest.mark.parametrize("status", [200, 204, 400, 401, 404, 409, 500])
def test_rename_request(
    monkeypatch: pytest.MonkeyPatch, overwrite: bool, status: int
) -> None:
    resp = requests.Response()
    resp.status_code = status

    def post(url: str, **kwargs: Any) -> requests.Response:
        assert url == "https://example.test/storage/rename"
        assert kwargs["json"] == {
            "sourcePath": "a/č.txt",
            "destinationPath": "b/new name.txt",
            "overwrite": overwrite,
        }
        assert kwargs["headers"]["X-API-Key"] == "key"
        assert kwargs["timeout"] == 7
        return resp

    monkeypatch.setattr(requests, "post", post)
    svc = StorageServices("https://example.test/storage", timeout=7)
    svc.set_api_key("key")
    if status >= 400:
        with pytest.raises(requests.HTTPError):
            svc.rename("a/č.txt", "b/new name.txt", overwrite)
    elif overwrite:
        assert svc.rename("a/č.txt", "b/new name.txt", True) is resp
    else:
        assert svc.rename("a/č.txt", "b/new name.txt") is resp
