import uuid
from pathlib import Path
from typing import Iterator

import pytest
import requests

from gfslib.storage import StorageServices
from gfslib import GFClient
from test_storage_integration import _skip_unless_env

pytestmark = pytest.mark.integration


def test_workspace_discovery() -> None:
    url, key = _skip_unless_env()
    base, service = url.rsplit("/api/ws/", 1)
    workspace_id = service.split("/", 1)[0]
    server = GFClient(base)
    server.set_api_key(key)
    assert isinstance(server.workspaces(), list)
    assert server.workspace(workspace_id)["uuid"] == workspace_id
    assert server.storage(workspace_id).base_url == url.rstrip("/")


@pytest.mark.parametrize("method", ["datasets", "datasources"])
def test_workspace_catalog(method: str) -> None:
    url, key = _skip_unless_env()
    base, service = url.rsplit("/api/ws/", 1)
    server = GFClient(base)
    server.set_api_key(key)
    try:
        result = getattr(server, method)(service.split("/", 1)[0])
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 500:
            pytest.xfail(f"GF test server returns HTTP 500 for {method}")
        raise
    assert isinstance(result, list)


@pytest.fixture
def remote_folder() -> Iterator[tuple[StorageServices, str]]:
    url, key = _skip_unless_env()
    svc = StorageServices(url)
    svc.set_api_key(key)
    root = f"gfslib-tests/{uuid.uuid4().hex}"
    svc.mkdir(root)
    try:
        yield svc, root
    finally:
        svc.delete(root).raise_for_status()


def test_path_and_directory_workflow(
    remote_folder: tuple[StorageServices, str],
) -> None:
    svc, root = remote_folder
    assert svc.exists(root)
    assert svc.is_dir(root)
    assert not svc.is_file(root)
    assert not svc.exists(f"{root}/missing")
    svc.upload_text(f"{root}/file.txt", "text").raise_for_status()
    assert svc.is_file(f"{root}/file.txt")
    assert not svc.is_dir(f"{root}/file.txt")
    svc.rename(f"{root}/file.txt", f"{root}/renamed.txt")
    assert not svc.exists(f"{root}/file.txt")
    assert svc.download(f"{root}/renamed.txt", verify_sha=True) == b"text"
    svc.mkdir([f"{root}/empty", f"{root}/nested/empty"])
    svc.rmdir(f"{root}/empty")
    assert not svc.exists(f"{root}/empty")
    svc.rmdir(f"{root}/nested", recursive=True)
    assert not svc.exists(f"{root}/nested")


def test_directory_download_workflow(
    remote_folder: tuple[StorageServices, str], tmp_path: Path
) -> None:
    svc, root = remote_folder
    for path, text in [
        ("a/same.txt", "first"),
        ("b/same.txt", "second"),
        ("empty.txt", ""),
    ]:
        svc.upload_text(f"{root}/{path}", text).raise_for_status()
    results = svc.download_directory(
        root, tmp_path / "copy", verify_sha=True, write_chunk_size=7
    )
    assert len(results) == 3
    assert (tmp_path / "copy/a/same.txt").read_text() == "first"
    assert (tmp_path / "copy/b/same.txt").read_text() == "second"
    assert (tmp_path / "copy/empty.txt").read_bytes() == b""
    with pytest.raises(FileExistsError):
        svc.download_directory(root, tmp_path / "copy")


def test_sync_workflow(
    remote_folder: tuple[StorageServices, str], tmp_path: Path
) -> None:
    svc, root = remote_folder
    local = tmp_path / "local"
    local.mkdir()
    (local / "file.txt").write_text("first")
    (local / "ignored.bin").write_bytes(b"ignored")
    prefix = f"{root}/new"
    assert svc.sync_local_to_remote(local, prefix, include=["*.txt"]) == {
        "file.txt": "uploaded"
    }
    assert svc.sync_local_to_remote(local, prefix, include=["*.txt"]) == {
        "file.txt": "skipped"
    }
    copy = tmp_path / "copy"
    assert svc.sync_remote_to_local(copy, prefix, verify_sha=True) == {
        "file.txt": "downloaded"
    }
    assert (copy / "file.txt").read_text() == "first"
    assert svc.sync_remote_to_local(copy, prefix) == {"file.txt": "skipped"}
    (local / "file.txt").write_text("changed")
    assert svc.sync_local_to_remote(local, prefix, include=["*.txt"]) == {
        "file.txt": "uploaded"
    }
    svc.sync_remote_to_local(copy, prefix, verify_sha=True)
    assert (copy / "file.txt").read_text() == "changed"


def test_bulk_upload_workflow(
    remote_folder: tuple[StorageServices, str], tmp_path: Path
) -> None:
    svc, root = remote_folder
    first, empty = tmp_path / "first", tmp_path / "empty"
    first.write_bytes(b"\x00\xffabc" * 4096)
    empty.touch()
    svc.upload_many(
        {f"{root}/sub/first.bin": first, f"{root}/empty": empty}, chunk_size=7
    )
    assert svc.download(f"{root}/sub/first.bin", verify_sha=True) == first.read_bytes()
    # The server's single-file GET fails for zero-byte files; its batch endpoint
    # supports them and verifies that the upload created the empty file.
    output = tmp_path / "empty-download"
    output.mkdir()
    svc.download([f"{root}/empty"], output, verify_sha=True)
    assert (output / "empty").read_bytes() == b""
