import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
import requests

from gfslib.storage import StorageServices, SyncEntry


def response(payload: Any, status: int = 200) -> requests.Response:
    resp = requests.Response()
    resp.status_code = status
    resp._content = json.dumps(payload).encode()
    return resp


@pytest.mark.parametrize("prefix", ["", "folder"])
@pytest.mark.parametrize(
    "case",
    ["missing", "file", "empty", "valid", "bad_response", "outside", "duplicate"],
)
def test_remote_inventory(
    monkeypatch: pytest.MonkeyPatch, prefix: str, case: str
) -> None:
    svc = StorageServices("https://example.test")
    monkeypatch.setattr(
        svc,
        "path_info",
        lambda *a: {
            "exists": case != "missing",
            "entry": {"type": 0 if case == "file" else 1},
        },
    )
    remote = f"{prefix}/file" if prefix else "file"

    def ls(**kwargs: Any) -> requests.Response:
        assert kwargs == {"path": prefix or "/", "recursive": True}
        return response([] if case == "empty" else [remote])

    def metadata(paths: list[str], **kwargs: Any) -> Any:
        assert paths == [remote]
        assert kwargs == {"ignore_sha": False}
        if case == "bad_response":
            return {}
        item = {"path": "other/file" if case == "outside" else remote, "sha2": "hash"}
        return [item, item] if case == "duplicate" else [item]

    monkeypatch.setattr(svc, "ls", ls)
    monkeypatch.setattr(svc, "metadata", metadata)
    if case in ("file", "bad_response", "duplicate") or (case == "outside" and prefix):
        with pytest.raises((ValueError, NotADirectoryError)):
            svc._remote_metadata(prefix, False)
    else:
        result = svc._remote_metadata(prefix, False)
        assert len(result) == (0 if case in ("missing", "empty") else 1)


@pytest.mark.parametrize(
    "include,exclude,expected",
    [
        (None, (), ["a.txt", "b.bin", "sub/c.txt"]),
        (["*.txt"], (), ["a.txt", "sub/c.txt"]),
        (["*.txt"], ["sub/*"], ["a.txt"]),
        ([], (), []),
        (["*.TXT"], (), []),
        (None, ["*"], []),
    ],
)
def test_sync_patterns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    include: Any,
    exclude: Any,
    expected: list[str],
) -> None:
    for path in ["a.txt", "b.bin", "sub/c.txt"]:
        local = tmp_path / path
        local.parent.mkdir(exist_ok=True)
        local.write_text(path)
    svc = StorageServices("https://example.test")
    monkeypatch.setattr(svc, "_remote_metadata", lambda *a: {})
    plan = svc.plan_sync(tmp_path, include=include, exclude=exclude)
    assert plan == [SyncEntry(path, "upload", "missing") for path in expected]
    result = svc.sync_local_to_remote(
        tmp_path, dry_run=True, include=include, exclude=exclude
    )
    assert result == {path: "uploaded (dry-run)" for path in expected}


@pytest.mark.parametrize("ignore_sha", [False, True])
def test_download_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ignore_sha: bool
) -> None:
    (tmp_path / "same").write_bytes(b"same")
    (tmp_path / "changed").write_bytes(b"old")
    (tmp_path / "unknown").write_bytes(b"old")
    (tmp_path / "local-only").write_text("keep")
    remote = {
        "same": {"Sha2": hashlib.sha256(b"same").hexdigest()},
        "changed": {"Sha2": hashlib.sha256(b"new").hexdigest()},
        "unknown": {},
        "nested/missing": {"Sha2": "abc"},
    }
    svc = StorageServices("https://example.test")
    monkeypatch.setattr(svc, "_remote_metadata", lambda *a: remote)
    plan = svc.plan_sync(
        tmp_path, "prefix", direction="download", ignore_sha=ignore_sha
    )
    assert [entry.path for entry in plan] == sorted(remote)
    same = next(entry for entry in plan if entry.path == "same")
    assert same.action == ("download" if ignore_sha else "skip")
    assert same.reason == (
        "hash comparison disabled" if ignore_sha else "matching hash"
    )
    assert "local-only" not in {entry.path for entry in plan}


@pytest.mark.parametrize("prefix", ["", "folder"])
@pytest.mark.parametrize("dry_run", [False, True])
def test_download_sync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, prefix: str, dry_run: bool
) -> None:
    svc = StorageServices("https://example.test")
    plan = [
        SyncEntry("same", "skip", "matching hash"),
        SyncEntry("sub/new", "download", "missing"),
    ]
    monkeypatch.setattr(svc, "plan_sync", lambda *a: plan)
    calls: list[str] = []

    def download(remote: str, local: Path, **kwargs: Any) -> bytes:
        calls.append(remote)
        assert local == tmp_path / "out/sub/new"
        assert kwargs == {"verify_sha": True, "overwrite": False}
        return b""

    monkeypatch.setattr(svc, "download", download)
    result = svc.sync_remote_to_local(
        tmp_path / "out", prefix, dry_run=dry_run, verify_sha=True, overwrite=False
    )
    assert result == {
        "same": "skipped",
        "sub/new": "downloaded (dry-run)" if dry_run else "downloaded",
    }
    assert calls == ([] if dry_run else [f"{prefix}/sub/new" if prefix else "sub/new"])
    assert not (tmp_path / "out").exists()


def test_dry_download_to_missing_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = StorageServices("https://example.test")
    monkeypatch.setattr(svc, "_remote_metadata", lambda *a: {"a": {}})
    result = svc.sync_remote_to_local(tmp_path / "missing", dry_run=True)
    assert result == {"a": "downloaded (dry-run)"}
    assert not (tmp_path / "missing").exists()


def test_invalid_sync_inputs(tmp_path: Path) -> None:
    svc = StorageServices("https://example.test")
    with pytest.raises(ValueError, match="direction"):
        svc.plan_sync(tmp_path, direction="invalid")  # type: ignore[arg-type]
    with pytest.raises(NotADirectoryError):
        svc.plan_sync(tmp_path / "missing")
    local = tmp_path / "file"
    local.touch()
    with pytest.raises(NotADirectoryError):
        svc.plan_sync(local, direction="download")
    with pytest.raises(ValueError, match="illegal"):
        svc.plan_sync(tmp_path, remote_prefix="../outside")


def test_download_filters(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    svc = StorageServices("https://example.test")
    monkeypatch.setattr(
        svc, "_remote_metadata", lambda *a: {"yes.txt": {}, "no.bin": {}}
    )
    assert svc.plan_sync(tmp_path, direction="download", include=["*.txt"]) == [
        SyncEntry("yes.txt", "download", "missing")
    ]


def test_empty_download_plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    svc = StorageServices("https://example.test")
    monkeypatch.setattr(svc, "_remote_metadata", lambda *a: {})
    assert svc.sync_remote_to_local(tmp_path) == {}
