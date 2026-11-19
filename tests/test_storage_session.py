"""Caller-owned Session policy applies to every storage request without network I/O."""

import hashlib
import io
import json
from pathlib import Path
from typing import Any, Iterable, cast
from urllib.parse import urlsplit

import pytest
import requests

from gfslib.storage import StorageServices


class RecordingAdapter(requests.adapters.BaseAdapter):
    def __init__(self, redirect: bool = False) -> None:
        super().__init__()
        self.redirect = redirect
        self.calls: list[tuple[requests.PreparedRequest, dict[str, Any]]] = []
        self.closed = False

    def send(
        self,
        request: requests.PreparedRequest,
        stream: bool = False,
        timeout: Any = None,
        verify: bool | str = True,
        cert: Any = None,
        proxies: Any = None,
    ) -> requests.Response:
        self.calls.append(
            (
                request,
                {
                    "stream": stream,
                    "timeout": timeout,
                    "verify": verify,
                    "cert": cert,
                    "proxies": proxies,
                },
            )
        )
        path = urlsplit(request.url or "").path
        body = b""
        payload = b"downloaded"
        if path.endswith("/path/info"):
            body = b'{"exists": false}'
        elif path.endswith("/metadata"):
            names = json.loads(request.body or b"[]")
            body = json.dumps(
                [
                    {
                        "Path": name,
                        "Size": len(payload),
                        "Sha2": hashlib.sha256(payload).hexdigest(),
                    }
                    for name in names
                ]
            ).encode()
        elif path.endswith("/download"):
            body = (
                json.dumps({"Path": "nested/file.txt", "Size": len(payload)}).encode()
                + payload
            )
        elif "/files/" in path and request.method == "GET":
            body = payload
        elif path.endswith(
            ("/ls", "/ls/long", "/directories/create", "/directories/remove")
        ):
            body = b"[]"
        elif path.endswith("/upload"):
            # Consume the streaming body while the caller still owns its source.
            assert b"uploaded" in b"".join(cast(Iterable[bytes], request.body))
        response = requests.Response()
        response.status_code = 302 if self.redirect else 200
        response.url = request.url or ""
        response.request = request
        response.headers["Location"] = "https://different.example/redirected"
        response._content = body
        response.raw = io.BytesIO(body)
        return response

    def close(self) -> None:
        self.closed = True


def test_session_is_used_by_all_storage_operations_and_remains_caller_owned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def reject_global_requests(*args: Any, **kwargs: Any) -> None:
        pytest.fail("The injected Session was bypassed")

    for method in ("get", "post", "put"):
        monkeypatch.setattr(requests, method, reject_global_requests)

    session = requests.Session()
    session.trust_env = False
    session.verify = "/operator/ca.pem"
    session.headers["X-Operator-Policy"] = "configured"
    adapter = RecordingAdapter()
    session.mount("https://", adapter)
    source = tmp_path / "source"
    source.write_bytes(b"uploaded")
    service = StorageServices("https://storage.example/api", 9, session=session)
    service.set_api_key("sdk-test-key")

    service.path_info("file")
    service.rename("before", "after")
    service.mkdir("folder")
    service.rmdir("folder")
    service.ls()
    service.ls_long()
    service.upload("raw", b"uploaded")
    service.upload_text("text", "uploaded")
    service.upload("literal", "uploaded")
    service.upload_file("file", source)
    service.upload_many({"file": source}, chunk_size=3)
    assert service.download("file", verify_sha=True) == b"downloaded"
    batch = tmp_path / "batch"
    batch.mkdir()
    service.download(["nested/file.txt"], dest=batch)
    service.download_directory("nested", tmp_path / "directory")
    service.delete("file")
    service.metadata(["file"])

    assert len(adapter.calls) == 17
    for request, options in adapter.calls:
        assert request.headers["X-Operator-Policy"] == "configured"
        assert request.headers["X-API-Key"] == "sdk-test-key"
        assert options["verify"] == "/operator/ca.pem"
        assert options["timeout"] == 9
    assert session.headers.get("X-API-Key") is None
    assert not adapter.closed
    assert session.get("https://storage.example/api/path/info").json() == {
        "exists": False
    }
    assert adapter.calls[-1][0].headers.get("X-API-Key") is None
    session.close()
    assert adapter.closed


class NoRedirectSession(requests.Session):
    def request(self, *args: Any, **kwargs: Any) -> requests.Response:
        kwargs["allow_redirects"] = False
        return super().request(*args, **kwargs)


def test_session_can_enforce_redirect_policy_without_forwarding_api_key() -> None:
    adapter = RecordingAdapter(redirect=True)
    with NoRedirectSession() as session:
        session.trust_env = False
        session.mount("https://", adapter)
        service = StorageServices("https://storage.example/api", session=session)
        service.set_api_key("sdk-test-key")
        response = service.ls()
        assert response.status_code == 302
        assert len(adapter.calls) == 1
        assert (
            adapter.calls[0][0].url
            == "https://storage.example/api/ls?recursive=false&includeDirectories=false"
        )
