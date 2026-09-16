import hashlib
import io
import json
import os
from pathlib import Path
from typing import Any

import pytest
import requests

from gfslib.storage import StorageServices
from gfslib.storage._stream import Reader, encode_files


@pytest.mark.parametrize("chunk_size", [1, 7, 1024])
def test_upload_many_wire_format(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, chunk_size: int
) -> None:
    first, empty = tmp_path / "one", tmp_path / "empty"
    first.write_bytes(b"abc\x00\xff" * 3)
    empty.touch()
    os.utime(first, (1600000000, 1600000000))
    resp = requests.Response()
    resp.status_code = 200

    def post(url: str, **kwargs: Any) -> requests.Response:
        assert url == "https://example.test/upload"
        assert kwargs["params"] == {"fileComparison": "TimeModified"}
        assert kwargs["headers"]["Content-Type"] == "application/octet-stream"
        assert kwargs["headers"]["X-API-Key"] == "key"
        assert kwargs["timeout"] == 8
        chunks = list(kwargs["data"])
        reader = Reader(io.BytesIO(b"".join(chunks)), 2)
        meta = reader.metadata()
        assert meta == {
            "Name": "č.bin",
            "Path": "nested/č.bin",
            "Size": 15,
            "Sha2": hashlib.sha256(first.read_bytes()).hexdigest(),
            "TimeModified": "2020-09-13T12:26:40.000000Z",
        }
        assert b"".join(reader.payload(15)) == first.read_bytes()
        meta = reader.metadata()
        assert meta is not None
        assert meta["Path"] == "empty" and meta["Size"] == 0
        assert meta["Sha2"] == hashlib.sha256(b"").hexdigest()
        assert reader.metadata() is None
        # Payload chunks are bounded; metadata is one small JSON record.
        assert all(len(chunk) <= chunk_size for chunk in chunks[1:-1])
        return resp

    monkeypatch.setattr(requests, "post", post)
    svc = StorageServices("https://example.test", timeout=8)
    svc.set_api_key("key")
    assert (
        svc.upload_many(
            {"/nested\\č.bin": first, "empty": str(empty)}, chunk_size=chunk_size
        )
        is resp
    )


@pytest.mark.parametrize(
    "case", ["empty", "missing", "directory", "duplicate", "unsafe", "chunk_size"]
)
def test_upload_many_validation(
    tmp_path: Path, case: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    local = tmp_path / "file"
    local.write_bytes(b"abc")
    files: Any = {"file": local}
    options: Any = {}
    if case == "empty":
        files = {}
    elif case == "missing":
        files = {"file": tmp_path / "missing"}
    elif case == "directory":
        files = {"file": tmp_path}
    elif case == "duplicate":
        files = {"a/b": local, "a\\b": local}
    elif case == "unsafe":
        files = {"../file": local}
    else:
        options = {"chunk_size": 0}
    monkeypatch.setattr(
        requests,
        "post",
        lambda *a, **kw: pytest.fail("invalid inputs must not send requests"),
    )
    with pytest.raises((ValueError, FileNotFoundError)):
        StorageServices("https://example.test").upload_many(files, **options)


@pytest.mark.parametrize("failure", ["http", "network"])
def test_upload_failure_closes_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    local = tmp_path / "file"
    local.write_bytes(b"abc")
    opened: list[Any] = []
    original = Path.open

    def open_file(path: Path, *args: Any, **kwargs: Any) -> Any:
        result = original(path, *args, **kwargs)
        opened.append(result)
        return result

    def post(url: str, **kwargs: Any) -> requests.Response:
        assert kwargs["params"] == {"fileComparison": "Sha2"}
        next(kwargs["data"])
        if failure == "network":
            raise requests.ConnectionError("disconnected")
        resp = requests.Response()
        resp.status_code = 500
        return resp

    monkeypatch.setattr(Path, "open", open_file)
    monkeypatch.setattr(requests, "post", post)
    with pytest.raises(requests.RequestException):
        StorageServices("https://example.test").upload_many(
            {"file": local}, file_comparison="Sha2"
        )
    assert opened and all(source.closed for source in opened)


@pytest.mark.parametrize("change", ["shrink", "grow", "modify"])
def test_changed_file_detected(tmp_path: Path, change: str) -> None:
    local = tmp_path / "file"
    local.write_bytes(b"a" * 10000)
    stream = encode_files([("file", local)], 3)
    assert json.loads(next(stream))["Size"] == 10000
    if change == "shrink":
        local.write_bytes(b"")
    elif change == "grow":
        with local.open("ab") as target:
            target.write(b"extra")
    else:
        os.utime(local, (1600000000, 1600000000))
    with pytest.raises(RuntimeError, match="changed"):
        list(stream)
