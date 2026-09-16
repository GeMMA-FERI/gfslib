# Storage Guide

This guide covers the most common storage workflows.

## Initialize client

```python
from gfslib.storage.client import StorageServices

svc = StorageServices("https://.../services/storage")
svc.set_api_key("your_api_key")
```

## List files

```python
short = svc.ls()
long = svc.ls_long()

# List inside a folder
folder = svc.ls(path="sync-tests", recursive=False, include_directories=True)

# Recursive listing including subfolders
tree = svc.ls_long(path="sync-tests", recursive=True, include_directories=True)
```

`ls()` and `ls_long()` both accept these optional parameters:

- `path`: list a specific remote folder or prefix
- `recursive`: include nested files and folders
- `include_directories`: include directory entries in the response when supported by the server

## Inspect a remote path

```python
svc.exists("folder/sample.txt")
svc.is_file("folder/sample.txt")
svc.is_dir("folder")
info = svc.path_info("folder/sample.txt")
```

Missing paths return `False` from the boolean helpers. Authentication and server
errors raise exceptions. `path_info()` returns the server's `exists` flag and,
when present, the `entry` containing path, type, size, and modification time.

## Upload a file

```python
svc.upload("folder/sample.txt", "path/to/local/sample.txt")
```

Prefer `upload_file(remote_path, local_path)` when uploading a file and
`upload_text(remote_path, text)` when uploading text. `upload_file` accepts
`pathlib.Path` and raises `FileNotFoundError` for missing files. The existing
`upload` method still treats strings that are not existing paths as text.

`metadata()` raises on HTTP errors and invalid JSON. Sync raises on failed
uploads instead of reporting success. Low-level upload methods continue to
return a response; call `raise_for_status()` when using them directly.

## Download a file

```python
# bytes in memory
content = svc.download("folder/sample.txt")

# write directly to disk
svc.download("folder/sample.txt", "downloaded/sample.txt")

# download multiple files into a folder
paths = [
    "folder/sample.txt",
    "folder/sub/other.txt",
]
svc.download(paths, dest="./downloads")

# skip paths that do not exist instead of failing the whole batch
svc.download(paths, dest="./downloads", ignore_missing=True)

# Preserve workspace-relative paths and verify SHA-256 before publishing files
svc.download(paths, dest="./downloads", preserve_paths=True,
             verify_sha=True, overwrite=False)

# Download folder contents, relative to the selected remote directory
svc.download_directory("folder", "./folder-copy", verify_sha=True)
```

Batch downloads retain their existing flat layout by default, but reject
duplicate destination names. `download_directory()` preserves the tree and
creates its destination folder. It defaults to `overwrite=False`; existing
files raise `FileExistsError`. Single/batch downloads retain `overwrite=True`.
Empty remote directories are not represented by the server's download stream.

Files are written to temporary siblings and published only after a complete
transfer and successful checksum verification when requested. A failed file
leaves an existing destination unchanged. Earlier completed files remain if a
later file in a batch fails. `verify_sha=True` requires server hash metadata and
cannot be combined with `ignore_sha=True` or partial (`byte_range`) downloads.

## Metadata

```python
meta = svc.metadata(["folder/sample.txt"], ignore_sha=False)
print(meta)
```

## Create and remove directories

```python
svc.mkdir("empty-folder")
svc.mkdir(["folder-one", "folder-two"])
results = svc.rmdir("empty-folder")
results = svc.rmdir(["folder-one", "folder-two"], recursive=True)
```

Both methods accept a string or an iterable of paths and return the server's
per-path `path`, `status`, and `error` records. Inspect these records for partial
failures even when the HTTP request succeeds. `rmdir()` defaults to
`recursive=False`, so it does not remove a directory's contents.

## Rename or move

```python
svc.rename("uploads/old.txt", "archive/new.txt")
svc.rename("uploads/latest.txt", "archive/latest.txt", overwrite=True)
```

Renaming happens on the server. The default is `overwrite=False`; HTTP failures
raise exceptions. The returned value is the server response.

## Delete a single/multiple files/folders

```python
svc.delete("folder/sample.txt")

svc.delete([
    "folder_one",
    "folder_two/sample.txt",
    "folder_two/sub/other.txt",
])
```

## Sync local folder to remote

```python
result = svc.sync_local_to_remote(
    local_dir="./data",
    remote_prefix="uploads",
    ignore_sha=False,
    dry_run=False,
)
print(result)
```
