"""Sync plans and file selection."""

from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Literal, Sequence


@dataclass(frozen=True)
class SyncEntry:
    """One planned transfer, using a path relative to the selected directory."""

    path: str
    action: Literal["upload", "download", "skip"]
    reason: str


def selected(path: str, include: Sequence[str] | None, exclude: Sequence[str]) -> bool:
    """Match case-sensitive shell patterns against forward-slash relative paths."""
    return (include is None or any(fnmatchcase(path, p) for p in include)) and not any(
        fnmatchcase(path, p) for p in exclude
    )
