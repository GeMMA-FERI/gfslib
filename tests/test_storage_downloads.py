import hashlib
import io
import json
from pathlib import Path
from typing import Any

import pytest
import requests

from gfslib.storage import StorageServices
from gfslib.storage._stream import destination, extract, write_file


def record(path: str, data: bytes, **extra: Any) -> bytes:
    return json.dumps({"Path": path, "Size": len(data), **extra}).encode() + data


class Fragmented(io.BytesIO):
    def read(self, size: int | None = -1) -> bytes:
        return super().read(3 if size is None or size < 0 else min(size, 3))


class Response(requests.Response):
    def __init__(self, content: bytes, status: int = 200) -> None:
        super().__init__()
        self.status_code = status
        self.raw = Fragmented(content)
        self._content = content
        self.closed = False

    def close(self) -> None:
        self.closed = True
        super().close()


@pytest.mark.parametrize("chunk_size", [1, 7, 1024])
@pytest.mark.parametrize("preserve", [False, True])
def test_extract_boundaries(tmp_path: Path, chunk_size: int, preserve: bool) -> None:
    data = b"\x00}{\xff" * 7
    wire = record("a/č.bin", data, note='quotes " and \\ and }{', nested={"x": 2})
    wire += record("b/empty", b"") + record("c/last", b"last")
    result = extract(Fragmented(wire), tmp_path, chunk_size, True, preserve)
    assert len(result) == 3
    assert (tmp_path / ("a/č.bin" if preserve else "č.bin")).read_bytes() == data
    assert (tmp_path / ("b/empty" if preserve else "empty")).read_bytes() == b""
    assert (tmp_path / ("c/last" if preserve else "last")).read_bytes() == b"last"


@pytest.mark.parametrize(
    "path", ["../evil", "x/../evil", "x\\..\\evil", "C:/evil", "", ".", "x//a", "x\x00"]
)
def test_reject_paths(tmp_path: Path, path: str) -> None:
    with pytest.raises(RuntimeError, match="illegal"):
        extract(io.BytesIO(record(path, b"bad")), tmp_path, 10, True, True)
    assert list(tmp_path.iterdir()) == []


def test_reject_escaping_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = Path.resolve

    def resolve(path: Path, *args: Any, **kwargs: Any) -> Path:
        if path.name == "link":
            return tmp_path.parent / "outside"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    with pytest.raises(ValueError, match="escapes"):
        destination(tmp_path, "link")


@pytest.mark.parametrize("size", [-1, True, "5", None])
def test_bad_sizes(tmp_path: Path, size: Any) -> None:
    wire = json.dumps({"Path": "a", "Size": size}).encode()
    with pytest.raises(ValueError, match="size"):
        extract(io.BytesIO(wire), tmp_path, 2, True)
    assert list(tmp_path.iterdir()) == []


def test_duplicate_basename_and_overwrite(tmp_path: Path) -> None:
    wire = record("a/file", b"first") + record("b/file", b"second")
    with pytest.raises(ValueError, match="Multiple"):
        extract(io.BytesIO(wire), tmp_path, 100, True)
    assert (tmp_path / "file").read_bytes() == b"first"
    with pytest.raises(FileExistsError):
        extract(
            io.BytesIO(record("file", b"new")), tmp_path, 100, True, overwrite=False
        )
    assert (tmp_path / "file").read_bytes() == b"first"


@pytest.mark.parametrize("failure", ["hash", "truncated", "missing_hash"])
def test_failed_transfer_preserves_existing_file(tmp_path: Path, failure: str) -> None:
    target = tmp_path / "file"
    target.write_bytes(b"original")
    wire = record("file", b"new", Sha2="wrong")
    if failure == "truncated":
        wire = record("file", b"new", Sha2=hashlib.sha256(b"new").hexdigest())[:-1]
    elif failure == "missing_hash":
        wire = record("file", b"new")
    with pytest.raises((ValueError, RuntimeError)):
        extract(io.BytesIO(wire), tmp_path, 2, True, verify_sha=True)
    assert target.read_bytes() == b"original"
    assert list(tmp_path.iterdir()) == [target]


def test_lowercase_metadata_and_checksum(tmp_path: Path) -> None:
    wire = (
        json.dumps(
            {
                "path": "/folder/file",
                "size": 3,
                "sha2": hashlib.sha256(b"abc").hexdigest().upper(),
            }
        ).encode()
        + b"abc"
    )
    extract(io.BytesIO(wire), tmp_path, 3, True, True, False, True, "/folder/")
    assert (tmp_path / "file").read_bytes() == b"abc"


def test_prefix_boundary_and_invalid_chunk_size(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="outside"):
        extract(
            io.BytesIO(record("folder2/file", b"x")),
            tmp_path,
            3,
            True,
            remote_prefix="folder",
        )
    with pytest.raises(ValueError, match="positive"):
        extract(io.BytesIO(), tmp_path, 0, True)


def test_atomic_publish_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    def fail(*args: Any) -> None:
        raise PermissionError("test")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(PermissionError):
        write_file(tmp_path / "file", [b"a"], True)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("status", [200, 403])
def test_directory_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    data = b"abc"
    resp = Response(
        record("folder/sub/file", data, Sha2=hashlib.sha256(data).hexdigest()), status
    )

    def get(url: str, **kwargs: Any) -> requests.Response:
        assert url == "https://example.test/download"
        assert kwargs["params"] == {
            "path": "folder",
            "includeWorkspaceDatabase": "false",
        }
        assert kwargs["stream"] is True
        assert kwargs["headers"]["X-API-Key"] == "key"
        return resp

    monkeypatch.setattr(requests, "get", get)
    svc = StorageServices("https://example.test")
    svc.set_api_key("key")
    if status == 200:
        results = svc.download_directory("folder", tmp_path / "out", write_chunk_size=2)
        assert (tmp_path / "out/sub/file").read_bytes() == data
        assert len(results) == 1
    else:
        with pytest.raises(requests.HTTPError):
            svc.download_directory("folder", tmp_path)
    assert resp.closed
    with pytest.raises(ValueError, match="positive"):
        svc.download_directory("folder", tmp_path, write_chunk_size=0)


@pytest.mark.parametrize("destination_file", [False, True])
@pytest.mark.parametrize("hash_kind", ["valid", "invalid", "missing", "bad_metadata"])
def test_single_download_checksums(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    destination_file: bool,
    hash_kind: str,
) -> None:
    svc = StorageServices("https://example.test")
    resp = Response(b"abc")
    monkeypatch.setattr(requests, "get", lambda *a, **kw: resp)
    digest: Any = (
        hashlib.sha256(b"abc").hexdigest() if hash_kind == "valid" else "wrong"
    )
    if hash_kind == "missing":
        digest = None
    metadata = [] if hash_kind == "bad_metadata" else [{"sha2": digest}]
    monkeypatch.setattr(svc, "metadata", lambda *a, **kw: metadata)
    dest = tmp_path / "file" if destination_file else None
    if hash_kind == "valid":
        assert svc.download("file", dest, verify_sha=True) == (
            b"" if destination_file else b"abc"
        )
        if dest:
            assert dest.read_bytes() == b"abc"
    else:
        with pytest.raises(ValueError):
            svc.download("file", dest, verify_sha=True)
        assert not (tmp_path / "file").exists()
    if hash_kind in ("valid", "invalid"):
        assert resp.closed


@pytest.mark.parametrize(
    "options",
    [
        {"write_chunk_size": 0},
        {"verify_sha": True, "ignore_sha": True},
        {"verify_sha": True, "byte_range": (0, 1)},
    ],
)
def test_invalid_download_options(options: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        StorageServices("https://example.test").download("file", **options)


def test_batch_preserve_and_verify(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resp = Response(record("a/file", b"abc", Sha2=hashlib.sha256(b"abc").hexdigest()))
    monkeypatch.setattr(requests, "post", lambda *a, **kw: resp)
    svc = StorageServices("https://example.test")
    svc.download(
        ["a/file"], tmp_path, preserve_paths=True, verify_sha=True, overwrite=False
    )
    assert (tmp_path / "a/file").read_bytes() == b"abc"
    assert resp.closed


@pytest.mark.parametrize("empty", [False, True])
def test_verified_directory_uses_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, empty: bool
) -> None:
    listing = Response(json.dumps([] if empty else ["folder/a/file"]).encode())
    batch = Response(
        record("folder/a/file", b"abc", Sha2=hashlib.sha256(b"abc").hexdigest())
    )

    def get(url: str, **kwargs: Any) -> requests.Response:
        assert url.endswith("/ls")
        assert kwargs["params"] == {
            "path": "folder",
            "recursive": "true",
            "includeDirectories": "false",
        }
        return listing

    def post(url: str, **kwargs: Any) -> requests.Response:
        assert not empty
        assert url.endswith("/download")
        assert json.loads(kwargs["data"]) == ["folder/a/file"]
        return batch

    monkeypatch.setattr(requests, "get", get)
    monkeypatch.setattr(requests, "post", post)
    result = StorageServices("https://example.test").download_directory(
        "folder", tmp_path, verify_sha=True
    )
    assert len(result) == (0 if empty else 1)
    if not empty:
        assert (tmp_path / "a/file").read_bytes() == b"abc"


@pytest.mark.parametrize("many", [False, True])
def test_http_error_closes_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, many: bool
) -> None:
    resp = Response(b"error", 500)
    monkeypatch.setattr(requests, "post" if many else "get", lambda *a, **kw: resp)
    with pytest.raises(requests.HTTPError):
        StorageServices("https://example.test").download(
            ["file"] if many else "file", tmp_path
        )
    assert resp.closed
