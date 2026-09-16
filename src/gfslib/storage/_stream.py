"""Internal GF multifile stream and local destination helpers."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Iterable, Generator, Sequence


def encode_files(
    files: Sequence[tuple[str, Path]], chunk_size: int
) -> Generator[bytes, None, None]:
    """Produce GF metadata/payload records without buffering whole files."""
    for remote, local in files:
        with local.open("rb") as source:
            before = os.fstat(source.fileno())
            digest = hashlib.sha256()
            for chunk in iter(lambda: source.read(chunk_size), b""):
                digest.update(chunk)
            source.seek(0)
            metadata = {
                "Name": remote.rsplit("/", 1)[-1],
                "Path": remote,
                "Size": before.st_size,
                "Sha2": digest.hexdigest(),
                "TimeModified": datetime.fromtimestamp(before.st_mtime, timezone.utc)
                .isoformat(timespec="microseconds")
                .replace("+00:00", "Z"),
            }
            yield json.dumps(metadata, ensure_ascii=False).encode("utf-8")
            remaining = before.st_size
            while remaining:
                chunk = source.read(min(chunk_size, remaining))
                if not chunk:
                    raise RuntimeError(f"Local file changed during upload: {local}")
                remaining -= len(chunk)
                yield chunk
            after = os.fstat(source.fileno())
            if (before.st_size, before.st_mtime_ns) != (
                after.st_size,
                after.st_mtime_ns,
            ):
                raise RuntimeError(f"Local file changed during upload: {local}")


def relative_path(path: str) -> str:
    """Normalize a workspace path and reject ambiguous or unsafe components."""
    normalized = path.replace("\\", "/").lstrip("/")
    parts = normalized.split("/")
    if any(part in ("", ".", "..") or ":" in part or "\x00" in part for part in parts):
        raise ValueError("File path contains illegal characters")
    return normalized


def destination(root: Path, path: str) -> Path:
    """Resolve a path within root, also rejecting symlinks that escape it."""
    target = root / relative_path(path)
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError("File path escapes the destination folder")
    return target


def write_file(
    target: Path, chunks: Iterable[bytes], overwrite: bool, sha: str | None = None
) -> None:
    """Stage a file and publish only after successful transfer and verification."""
    if not overwrite and target.exists():
        raise FileExistsError(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as output:
        temporary = Path(output.name)
        try:
            for chunk in chunks:
                output.write(chunk)
                digest.update(chunk)
            if sha is not None and digest.hexdigest().lower() != sha.lower():
                raise ValueError(f"SHA-256 mismatch for {target.name}")
        except BaseException:
            output.close()
            temporary.unlink()
            raise
    try:
        if overwrite:
            os.replace(temporary, target)
        else:
            # Both names are on the same filesystem; link fails if target exists.
            os.link(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


class Reader:
    """Read exact boundaries from a stream that may return short reads."""

    def __init__(self, stream: BinaryIO, chunk_size: int) -> None:
        self.stream = stream
        self.chunk_size = chunk_size
        self.buffer = b""
        self.offset = 0

    def read(self, size: int) -> bytes:
        if self.offset == len(self.buffer):
            self.buffer = self.stream.read(self.chunk_size)
            self.offset = 0
        result = self.buffer[self.offset : self.offset + size]
        self.offset += len(result)
        return result

    def metadata(self) -> dict[str, Any] | None:
        first = self.read(1)
        if not first:
            return None
        if first != b"{":
            raise RuntimeError("Metadata start not found")
        data = bytearray(first)
        depth, quoted, escaped = 1, False, False
        while depth:
            char = self.read(1)
            if not char:
                raise RuntimeError("Metadata start not found: unexpected EOF")
            data.extend(char)
            if len(data) > 1024 * 1024:
                raise RuntimeError("Metadata too large")
            if quoted:
                if escaped:
                    escaped = False
                elif char == b"\\":
                    escaped = True
                elif char == b'"':
                    quoted = False
            elif char == b'"':
                quoted = True
            elif char == b"{":
                depth += 1
            elif char == b"}":
                depth -= 1
        result: dict[str, Any] = json.loads(data)
        return result

    def payload(self, size: int) -> Iterable[bytes]:
        remaining = size
        while remaining:
            chunk = self.read(min(self.chunk_size, remaining))
            if not chunk:
                raise RuntimeError("Unexpected EOF while reading file payload")
            remaining -= len(chunk)
            yield chunk


def extract(
    stream: BinaryIO,
    dest_dir: os.PathLike[str] | str,
    chunk_size: int,
    validate_paths: bool,
    preserve_paths: bool = False,
    overwrite: bool = True,
    verify_sha: bool = False,
    remote_prefix: str = "",
) -> list[dict[str, Any]]:
    root = Path(dest_dir)
    if not root.is_dir():
        raise ValueError("Output folder does not exist")
    if chunk_size <= 0:
        raise ValueError("write_chunk_size must be positive")
    reader = Reader(stream, chunk_size)
    prefix = remote_prefix.replace("\\", "/").strip("/")
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    while (meta := reader.metadata()) is not None:
        path = str(meta.get("Path", meta.get("path", "")))
        if validate_paths or preserve_paths:
            try:
                path = relative_path(path)
            except ValueError as exc:
                raise RuntimeError(str(exc)) from exc
        if prefix:
            if not path.startswith(prefix + "/"):
                raise ValueError("Remote file is outside the requested directory")
            path = path[len(prefix) + 1 :]
        if not preserve_paths:
            path = path.replace("\\", "/").rsplit("/", 1)[-1]
        target = destination(root, path)
        key = os.path.normcase(str(target.resolve()))
        if key in seen:
            raise ValueError(f"Multiple remote files map to {path}")
        seen.add(key)
        size = meta.get("Size", meta.get("size"))
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise ValueError("Invalid file size in metadata")
        sha = meta.get("Sha2", meta.get("sha2")) if verify_sha else None
        if verify_sha and not isinstance(sha, str):
            raise ValueError("SHA-256 is missing from metadata")
        write_file(target, reader.payload(size), overwrite, sha)
        results.append({"metadata": meta, "path": str(target)})
    return results
