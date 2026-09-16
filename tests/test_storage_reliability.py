import hashlib
import io
import json
from pathlib import Path
from typing import Any

import pytest
import requests

from gfslib.storage import StorageServices


def response(payload: Any = None, status: int = 200) -> requests.Response:
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(payload).encode()
    return result


@pytest.mark.parametrize("as_string", [False, True])
def test_upload_local_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, as_string: bool
) -> None:
    local = tmp_path / "sample.txt"
    local.write_bytes(b"content")
    seen: list[Any] = []

    def put(url: str, **kwargs: Any) -> requests.Response:
        assert url == "https://example.test/storage/files/a%20b/%C4%8D.txt"
        assert kwargs["data"].read() == b"content"
        assert kwargs["headers"]["X-API-Key"] == "test-key"
        assert kwargs["timeout"] == 9
        seen.append(kwargs["data"])
        return response()

    monkeypatch.setattr(requests, "put", put)
    svc = StorageServices("https://example.test/storage/", timeout=9)
    svc.set_api_key("test-key")
    svc.upload("/a b/č.txt", str(local) if as_string else local)
    assert seen[0].closed


def test_explicit_upload_methods(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local = tmp_path / "file"
    local.write_text("content")
    bodies: list[bytes] = []

    def put(url: str, **kwargs: Any) -> requests.Response:
        bodies.append(kwargs["data"])
        return response()

    monkeypatch.setattr(requests, "put", put)
    svc = StorageServices("https://example.test")
    svc.upload_text("text", str(local))
    svc.upload_text("text", "č")
    assert bodies == [str(local).encode(), "č".encode()]
    with pytest.raises(FileNotFoundError):
        svc.upload_file("missing", tmp_path / "missing")
    with pytest.raises(FileNotFoundError):
        svc.upload("missing", tmp_path / "missing")


@pytest.mark.parametrize("method", ["ls", "ls_long"])
@pytest.mark.parametrize("path", [None, "folder"])
def test_list_requests(
    monkeypatch: pytest.MonkeyPatch, method: str, path: str | None
) -> None:
    def get(url: str, **kwargs: Any) -> requests.Response:
        assert url.endswith("/ls/long" if method == "ls_long" else "/ls")
        expected = {"recursive": "true", "includeDirectories": "true"}
        if path is not None:
            expected["path"] = path
        assert kwargs["params"] == expected
        return response(["file"])

    monkeypatch.setattr(requests, "get", get)
    svc = StorageServices("https://example.test")
    assert getattr(svc, method)(path, True, True).json() == ["file"]


@pytest.mark.parametrize("paths", ["č.txt", ["a", "b"]])
def test_delete_request(monkeypatch: pytest.MonkeyPatch, paths: Any) -> None:
    def post(url: str, **kwargs: Any) -> requests.Response:
        assert url.endswith("/remove")
        assert json.loads(kwargs["data"]) == (
            [paths] if isinstance(paths, str) else paths
        )
        assert int(kwargs["headers"]["Content-Length"]) == len(kwargs["data"])
        return response([])

    monkeypatch.setattr(requests, "post", post)
    assert StorageServices("https://example.test").delete(paths).json() == []


@pytest.mark.parametrize("ignore_sha", [False, True])
@pytest.mark.parametrize("status", [200, 401, 500])
def test_metadata_errors_and_options(
    monkeypatch: pytest.MonkeyPatch, ignore_sha: bool, status: int
) -> None:
    def post(url: str, **kwargs: Any) -> requests.Response:
        assert url.endswith("/metadata?ignoreSha=true" if ignore_sha else "/metadata")
        assert json.loads(kwargs["data"]) == ["a"]
        return response([{"Path": "a"}], status)

    monkeypatch.setattr(requests, "post", post)
    svc = StorageServices("https://example.test")
    if status == 200:
        assert svc.metadata(iter(["a"]), ignore_sha) == [{"Path": "a"}]
    else:
        with pytest.raises(requests.HTTPError):
            svc.metadata(["a"], ignore_sha)


@pytest.mark.parametrize("prefix", ["", "nested"])
@pytest.mark.parametrize(
    "kind", ["same", "changed", "no_hash", "bad_path", "lowercase"]
)
def test_sync_comparison(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, prefix: str, kind: str
) -> None:
    (tmp_path / "file").write_bytes(b"abc")
    remote = f"{prefix}/file" if prefix else "file"
    digest = hashlib.sha256(b"abc").hexdigest().upper()
    item: dict[str, Any] = {"Path": remote, "Sha2": digest}
    if kind == "changed":
        item["Sha2"] = "different"
    elif kind == "no_hash":
        del item["Sha2"]
    elif kind == "bad_path":
        item["Path"] = None
    elif kind == "lowercase":
        item = {"path": remote, "sha2": digest}
    svc = StorageServices("https://example.test")
    monkeypatch.setattr(svc, "metadata", lambda *a, **kw: [item])
    uploads: list[str] = []

    def upload(remote_path: str, local_path: str) -> requests.Response:
        uploads.append(remote_path)
        assert Path(local_path).read_bytes() == b"abc"
        return response()

    monkeypatch.setattr(svc, "upload_file", upload)
    skip = kind in ("same", "lowercase")
    assert svc.sync_local_to_remote(tmp_path, prefix) == {
        "file": "skipped" if skip else "uploaded"
    }
    assert uploads == ([] if skip else [remote])


def test_sync_upload_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "file").write_text("data")
    svc = StorageServices("https://example.test")
    monkeypatch.setattr(svc, "metadata", lambda *a, **kw: [])
    monkeypatch.setattr(svc, "upload_file", lambda *a, **kw: response(status=500))
    with pytest.raises(requests.HTTPError):
        svc.sync_local_to_remote(tmp_path)


def test_multiple_records_in_one_buffer(tmp_path: Path) -> None:
    stream = io.BytesIO(b'{"Path":"a","Size":1}a{"Path":"b","Size":0}')
    svc = StorageServices("https://example.test")
    assert len(svc._deserialize_stream_to_files(stream, tmp_path, 3, True)) == 2
    assert (tmp_path / "a").read_bytes() == b"a"
    assert (tmp_path / "b").read_bytes() == b""
