"""High-level StorageServices for GeMMA Fusion Server.

Provides methods for listing, uploading, downloading, deleting,
fetching metadata and syncing local files with the remote storage service.

Usage:
    client = StorageServices("https://fusion.gemma.feri.um.si/gf-test/api/ws/<workspace-id>/services/storage")
    client.set_api_key("...")
    client.ls()
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import (
    Dict,
    Iterable,
    List,
    Optional,
    Tuple,
    Any,
    Literal,
    Sequence,
    Mapping,
)
from urllib.parse import quote

import requests

from ._stream import destination, encode_files, extract, relative_path, write_file
from ._sync import SyncEntry, selected


class StorageServices:
    """Client for the server storage API.

    Initialize with the base storage service URL. Examples of base URL:
    - https://.../api/ws/<workspace-id>/services/storage
    """

    def __init__(self, base_url: str, timeout: Optional[float] = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._api_key: Optional[str] = None
        self.timeout = timeout

    def set_api_key(self, key: str) -> None:
        """Set the X-API-Key to use for requests."""
        self._api_key = key

    def _headers(self, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        headers = {
            "Accept": "*/*",
        }
        if self._api_key:
            headers["X-API-Key"] = self._api_key
        if extra:
            headers.update(extra)
        return headers

    def _file_url(self, remote_path: str) -> str:
        # Ensure we don't double the slashes; keep path parts encoded except '/'
        rp = remote_path.lstrip("/")
        return f"{self.base_url}/files/{quote(rp, safe='/') }"

    def path_info(self, path: str) -> Dict[str, Any]:
        """Return whether a path exists and its server-provided entry details."""
        resp = requests.get(
            f"{self.base_url}/path/info",
            headers=self._headers(),
            params={"path": path},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        result: Dict[str, Any] = resp.json()
        return result

    def exists(self, path: str) -> bool:
        """Return whether a remote file or directory exists."""
        return bool(self.path_info(path)["exists"])

    def is_file(self, path: str) -> bool:
        """Return whether a remote path exists and is a file."""
        info = self.path_info(path)
        return bool(info["exists"] and info["entry"]["type"] == 0)

    def is_dir(self, path: str) -> bool:
        """Return whether a remote path exists and is a directory."""
        info = self.path_info(path)
        return bool(info["exists"] and info["entry"]["type"] == 1)

    def rename(
        self, source: str, destination: str, overwrite: bool = False
    ) -> requests.Response:
        """Rename or move a remote path. Overwriting must be explicitly enabled."""
        resp = requests.post(
            f"{self.base_url}/rename",
            headers=self._headers(),
            json={
                "sourcePath": source,
                "destinationPath": destination,
                "overwrite": overwrite,
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp

    def mkdir(self, paths: str | Iterable[str]) -> List[Dict[str, Any]]:
        """Create directories and return per-path status/error records."""
        return self._directories("create", paths, {})

    def rmdir(
        self, paths: str | Iterable[str], recursive: bool = False
    ) -> List[Dict[str, Any]]:
        """Remove directories; only empty directories are removed by default.

        Inspect returned records for individual failures, including nonempty
        directories. HTTP failures raise requests.HTTPError.
        """
        return self._directories("remove", paths, {"recursive": str(recursive).lower()})

    def _directories(
        self, operation: str, paths: str | Iterable[str], params: Dict[str, str]
    ) -> List[Dict[str, Any]]:
        resp = requests.post(
            f"{self.base_url}/directories/{operation}",
            headers=self._headers(),
            json=[paths] if isinstance(paths, str) else list(paths),
            params=params,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        results: List[Dict[str, Any]] = resp.json()
        return results

    def ls(
        self,
        path: Optional[str] = None,
        recursive: bool = False,
        include_directories: bool = False,
    ) -> requests.Response:
        """List remote files (short). Returns the requests.Response object."""
        url = f"{self.base_url}/ls"
        params: Dict[str, str] = {}
        if path is not None:
            params["path"] = path
        params["recursive"] = str(recursive).lower()
        params["includeDirectories"] = str(include_directories).lower()
        return requests.get(
            url, headers=self._headers(), params=params, timeout=self.timeout
        )

    def ls_long(
        self,
        path: Optional[str] = None,
        recursive: bool = False,
        include_directories: bool = False,
    ) -> requests.Response:
        """List remote files (long). Returns the requests.Response object."""
        url = f"{self.base_url}/ls/long"
        params: Dict[str, str] = {}
        if path is not None:
            params["path"] = path
        params["recursive"] = str(recursive).lower()
        params["includeDirectories"] = str(include_directories).lower()
        return requests.get(
            url, headers=self._headers(), params=params, timeout=self.timeout
        )

    def upload(
        self, remote_path: str, data: bytes | str | os.PathLike[str]
    ) -> requests.Response:
        """Upload data to `remote_path` using PUT.

        `data` can be raw bytes, a string (will be encoded as utf-8), or a path
        to a local file to stream.
        """
        url = self._file_url(remote_path)
        headers = self._headers({"Content-Type": "application/octet-stream"})

        if isinstance(data, (bytes, bytearray)):
            body = data
            resp = requests.put(url, headers=headers, data=body, timeout=self.timeout)
            return resp

        if isinstance(data, os.PathLike) or (
            isinstance(data, str) and os.path.exists(data)
        ):
            return self.upload_file(remote_path, data)

        # otherwise treat as string content
        if isinstance(data, str):
            body = data.encode("utf-8")
            return requests.put(url, headers=headers, data=body, timeout=self.timeout)

        raise TypeError("data must be bytes, string, or path to file")

    def upload_file(
        self, remote_path: str, local_path: os.PathLike[str] | str
    ) -> requests.Response:
        """Stream a local file. Missing files raise FileNotFoundError."""
        with open(local_path, "rb") as fh:
            return requests.put(
                self._file_url(remote_path),
                headers=self._headers({"Content-Type": "application/octet-stream"}),
                data=fh,
                timeout=self.timeout,
            )

    def upload_text(self, remote_path: str, text: str) -> requests.Response:
        """Upload UTF-8 text, even when the text names an existing local file."""
        return self.upload(remote_path, text.encode("utf-8"))

    def upload_many(
        self,
        files: Mapping[str, os.PathLike[str] | str],
        file_comparison: str = "TimeModified",
        chunk_size: int = 1024 * 1024,
    ) -> requests.Response:
        """Stream remote-path -> local-file mappings in one GF upload request.

        Each file is hashed and then streamed in bounded chunks. The server's
        fileComparison policy controls replacement (default TimeModified).
        A failed request can leave earlier files uploaded; this is not atomic
        across files and is never retried automatically. HTTP errors raise.
        """
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        if not files:
            raise ValueError("files must not be empty")
        prepared = []
        seen = set()
        for remote, local in files.items():
            path = relative_path(remote)
            if path in seen:
                raise ValueError("Duplicate remote upload path")
            seen.add(path)
            source = Path(local)
            if not source.is_file():
                raise FileNotFoundError(source)
            prepared.append((path, source))
        body = encode_files(prepared, chunk_size)
        try:
            resp = requests.post(
                f"{self.base_url}/upload",
                headers=self._headers({"Content-Type": "application/octet-stream"}),
                params={"fileComparison": file_comparison},
                data=body,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            return resp
        finally:
            body.close()

    def download(
        self,
        remote_path: str | List[str],
        dest: Optional[os.PathLike[str] | str] = None,
        byte_range: Optional[Tuple[int, Optional[int]]] = None,
        ignore_sha: bool = False,
        write_chunk_size: int = 10 * 1024 * 1024,
        validate_paths: bool = True,
        ignore_missing: bool = False,
        preserve_paths: bool = False,
        overwrite: bool = True,
        verify_sha: bool = False,
    ) -> bytes | List[Dict[str, Any]]:
        """Download a file or multiple files.

        If the remote path is a single string, a single file will be downloaded
        and returned as bytes (or written to `dest` if provided).

        For multiple files, pass a list of strings in `remote_path` and provide
        `dest` as a destination folder. The server returns a stream in the form
        <METADATA_JSON><FILE><METADATA_JSON><FILE> and the files are written
        into `dest`. Set `ignore_missing` to skip requested files that do not
        exist instead of failing the entire multi-file download.

        Batch downloads flatten paths by default for compatibility. Set
        preserve_paths=True to retain workspace-relative paths. Colliding names
        in a batch raise ValueError. overwrite=False protects existing files.
        verify_sha=True checks server SHA-256 metadata; it cannot be combined
        with ignore_sha or a byte range. Files are staged until complete.
        """
        if write_chunk_size <= 0:
            raise ValueError("write_chunk_size must be positive")
        if verify_sha and (ignore_sha or byte_range is not None):
            raise ValueError("Checksum verification requires complete files and hashes")
        if isinstance(remote_path, list):
            if dest is None:
                raise ValueError(
                    "dest must be provided when downloading multiple files"
                )
            return self._download_many(
                remote_path,
                dest_dir=dest,
                ignore_sha=ignore_sha,
                ignore_missing=ignore_missing,
                write_chunk_size=write_chunk_size,
                validate_paths=validate_paths,
                preserve_paths=preserve_paths,
                overwrite=overwrite,
                verify_sha=verify_sha,
            )

        url = self._file_url(remote_path)
        headers = self._headers()
        if byte_range is not None:
            start, end = byte_range
            if end is None:
                headers["Range"] = f"bytes={start}-"
            else:
                headers["Range"] = f"bytes={start}-{end}"

        sha = None
        if verify_sha:
            meta = self.metadata([remote_path])
            if not isinstance(meta, list) or len(meta) != 1:
                raise ValueError("Expected metadata for one file")
            sha = meta[0].get("Sha2", meta[0].get("sha2"))
            if not isinstance(sha, str):
                raise ValueError("SHA-256 is missing from metadata")
        resp = requests.get(url, headers=headers, stream=True, timeout=self.timeout)
        try:
            resp.raise_for_status()
            if dest is not None:
                write_file(
                    Path(dest),
                    resp.iter_content(chunk_size=write_chunk_size),
                    overwrite,
                    sha,
                )
                return b""
            content = resp.content
            if (
                sha is not None
                and hashlib.sha256(content).hexdigest().lower() != sha.lower()
            ):
                raise ValueError("SHA-256 mismatch")
            return content
        finally:
            resp.close()

    def _download_many(
        self,
        remote_paths: List[str],
        dest_dir: os.PathLike[str] | str,
        ignore_sha: bool,
        ignore_missing: bool,
        write_chunk_size: int,
        validate_paths: bool,
        preserve_paths: bool,
        overwrite: bool,
        verify_sha: bool,
        remote_prefix: str = "",
    ) -> List[Dict[str, Any]]:
        url = f"{self.base_url}/download"
        query_params = []
        if ignore_sha:
            query_params.append("ignoreSha=True")
        if ignore_missing:
            query_params.append("ignoreMissingFiles=True")
        if query_params:
            url = f"{url}?{'&'.join(query_params)}"

        body = json.dumps(list(remote_paths))
        headers = self._headers(
            {
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
                "Accept-Encoding": "gzip, deflate, br",
            }
        )
        resp = requests.post(
            url,
            headers=headers,
            data=body.encode("utf-8"),
            stream=True,
            timeout=self.timeout,
        )
        try:
            resp.raise_for_status()
            resp.raw.decode_content = True
            return self._deserialize_stream_to_files(
                resp.raw,
                dest_dir=dest_dir,
                write_chunk_size=write_chunk_size,
                validate_paths=validate_paths,
                preserve_paths=preserve_paths,
                overwrite=overwrite,
                verify_sha=verify_sha,
                remote_prefix=remote_prefix,
            )
        finally:
            resp.close()

    def download_directory(
        self,
        remote_path: str,
        dest: os.PathLike[str] | str,
        overwrite: bool = False,
        verify_sha: bool = False,
        write_chunk_size: int = 10 * 1024 * 1024,
    ) -> List[Dict[str, Any]]:
        """Download directory contents, preserving paths relative to remote_path.

        Existing files are rejected unless overwrite=True. Completed files remain
        if a later file fails; each individual file is published atomically.
        Empty directories are not represented in the GF multifile stream.
        """
        if write_chunk_size <= 0:
            raise ValueError("write_chunk_size must be positive")
        Path(dest).mkdir(parents=True, exist_ok=True)
        if verify_sha:
            # GET /download omits hashes. POST /download includes them.
            listing = self.ls(path=remote_path, recursive=True)
            listing.raise_for_status()
            paths = listing.json()
            if not paths:
                return []
            return self._download_many(
                paths,
                dest,
                False,
                False,
                write_chunk_size,
                True,
                True,
                overwrite,
                True,
                remote_prefix=remote_path,
            )
        resp = requests.get(
            f"{self.base_url}/download",
            headers=self._headers(),
            params={"path": remote_path, "includeWorkspaceDatabase": "false"},
            stream=True,
            timeout=self.timeout,
        )
        try:
            resp.raise_for_status()
            resp.raw.decode_content = True
            return self._deserialize_stream_to_files(
                resp.raw,
                dest,
                write_chunk_size,
                True,
                preserve_paths=True,
                overwrite=overwrite,
                verify_sha=verify_sha,
                remote_prefix=remote_path,
            )
        finally:
            resp.close()

    def _deserialize_stream_to_files(
        self,
        stream: Any,
        dest_dir: os.PathLike[str] | str,
        write_chunk_size: int,
        validate_paths: bool,
        preserve_paths: bool = False,
        overwrite: bool = True,
        verify_sha: bool = False,
        remote_prefix: str = "",
    ) -> List[Dict[str, Any]]:
        return extract(
            stream,
            dest_dir,
            write_chunk_size,
            validate_paths,
            preserve_paths,
            overwrite,
            verify_sha,
            remote_prefix,
        )

    def delete(self, remote_path: str | List[str]) -> requests.Response:
        """Remove one or more remote files or directories recursively."""
        paths = [remote_path] if isinstance(remote_path, str) else list(remote_path)
        url = f"{self.base_url}/remove"
        body = json.dumps(paths)
        headers = self._headers(
            {"Content-Type": "application/json", "Content-Length": str(len(body))}
        )
        return requests.post(
            url, headers=headers, data=body.encode("utf-8"), timeout=self.timeout
        )

    def metadata(self, filepaths: Iterable[str], ignore_sha: bool = False) -> Any:
        """Get metadata for the provided filepaths."""
        url = f"{self.base_url}/metadata"
        if ignore_sha:
            url = f"{url}?ignoreSha=true"

        body = json.dumps(list(filepaths))
        headers = self._headers(
            {"Content-Type": "application/json", "Content-Length": str(len(body))}
        )
        resp = requests.post(
            url, headers=headers, data=body.encode("utf-8"), timeout=self.timeout
        )
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def compute_sha256(path: os.PathLike[str] | str) -> str:
        """Compute SHA-256 hex digest of a file."""
        h = hashlib.sha256()
        with open(str(path), "rb") as fh:
            for chunk in iter(lambda: fh.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()

    def _remote_metadata(self, prefix: str, ignore_sha: bool) -> Dict[str, Any]:
        info = self.path_info(prefix or "/")
        if not info["exists"]:
            return {}
        if info["entry"]["type"] != 1:
            raise NotADirectoryError(prefix)
        listing = self.ls(path=prefix or "/", recursive=True)
        listing.raise_for_status()
        paths = listing.json()
        if not paths:
            return {}
        metadata = self.metadata(paths, ignore_sha=ignore_sha)
        if not isinstance(metadata, list):
            raise ValueError("Expected a list of remote file metadata")
        result = {}
        for item in metadata:
            path = relative_path(item.get("Path", item.get("path", "")))
            if prefix:
                if not path.startswith(prefix + "/"):
                    raise ValueError("Remote file is outside the requested directory")
                path = path[len(prefix) + 1 :]
            if path in result:
                raise ValueError("Duplicate remote file metadata")
            result[path] = item
        return result

    def plan_sync(
        self,
        local_dir: os.PathLike[str] | str,
        remote_prefix: str = "",
        direction: Literal["upload", "download"] = "upload",
        ignore_sha: bool = False,
        include: Sequence[str] | None = None,
        exclude: Sequence[str] = (),
    ) -> List[SyncEntry]:
        """Plan a one-way sync without modifying files.

        Patterns are case-sensitive shell wildcards against relative paths with
        forward slashes. Exclusions win; an empty include list selects nothing.
        No files are deleted. ignore_sha=True plans every selected file for
        transfer, including files already present at the destination.
        """
        if direction not in ("upload", "download"):
            raise ValueError("direction must be upload or download")
        root = Path(local_dir)
        if (direction == "upload" or root.exists()) and not root.is_dir():
            raise NotADirectoryError(root)
        prefix = remote_prefix.replace("\\", "/").strip("/")
        if prefix:
            prefix = relative_path(prefix)
        local_paths = sorted(
            (Path(folder) / name).relative_to(root).as_posix()
            for folder, _, files in os.walk(root)
            for name in files
        )
        candidates = [p for p in local_paths if selected(p, include, exclude)]
        if direction == "upload" and not candidates:
            return []
        remote = self._remote_metadata(prefix, ignore_sha)
        if direction == "download":
            candidates = sorted(p for p in remote if selected(p, include, exclude))
        plan = []
        for path in candidates:
            local = destination(root, path)
            item = remote.get(path)
            reason = "missing"
            action: Literal["upload", "download", "skip"] = direction
            if item is not None and local.is_file():
                sha = item.get("Sha2", item.get("sha2"))
                if ignore_sha:
                    reason = "hash comparison disabled"
                elif not sha:
                    reason = "remote hash unavailable"
                elif self.compute_sha256(local).lower() == sha.lower():
                    action, reason = "skip", "matching hash"
                else:
                    reason = "different hash"
            plan.append(SyncEntry(path, action, reason))
        return plan

    def sync_local_to_remote(
        self,
        local_dir: os.PathLike[str] | str,
        remote_prefix: str = "",
        ignore_sha: bool = False,
        dry_run: bool = False,
        include: Sequence[str] | None = None,
        exclude: Sequence[str] = (),
    ) -> Dict[str, str]:
        """Upload missing/changed selected files, without deleting remote files.

        Returns relative path -> uploaded/skipped (or uploaded (dry-run)).
        Use plan_sync() for structured actions and comparison reasons.
        """
        plan = self.plan_sync(
            local_dir, remote_prefix, "upload", ignore_sha, include, exclude
        )
        prefix = remote_prefix.replace("\\", "/").strip("/")
        result = {}
        for entry in plan:
            if entry.action == "skip":
                result[entry.path] = "skipped"
            else:
                if not dry_run:
                    remote = f"{prefix}/{entry.path}" if prefix else entry.path
                    self.upload_file(
                        remote, str(Path(local_dir) / entry.path)
                    ).raise_for_status()
                result[entry.path] = "uploaded (dry-run)" if dry_run else "uploaded"
        return result

    def sync_remote_to_local(
        self,
        local_dir: os.PathLike[str] | str,
        remote_prefix: str = "",
        ignore_sha: bool = False,
        dry_run: bool = False,
        include: Sequence[str] | None = None,
        exclude: Sequence[str] = (),
        verify_sha: bool = False,
        overwrite: bool = True,
    ) -> Dict[str, str]:
        """Download missing/changed selected files, without deleting local files.

        Dry runs do not create the destination directory. Changed existing files
        are replaced unless overwrite=False. verify_sha checks transferred data
        independently of the ignore_sha option for planning comparisons.
        """
        plan = self.plan_sync(
            local_dir, remote_prefix, "download", ignore_sha, include, exclude
        )
        prefix = remote_prefix.replace("\\", "/").strip("/")
        result = {}
        for entry in plan:
            if entry.action == "skip":
                result[entry.path] = "skipped"
            else:
                if not dry_run:
                    remote = f"{prefix}/{entry.path}" if prefix else entry.path
                    self.download(
                        remote,
                        destination(Path(local_dir), entry.path),
                        verify_sha=verify_sha,
                        overwrite=overwrite,
                    )
                result[entry.path] = "downloaded (dry-run)" if dry_run else "downloaded"
        return result
