import uuid
from pathlib import Path
from typing import Iterator

import pytest

from gfslib.storage import StorageServices
from test_storage_integration import _skip_unless_env

pytestmark = pytest.mark.integration


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
