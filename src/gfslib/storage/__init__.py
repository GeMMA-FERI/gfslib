"""Storage utilities for gfslib."""

from .client import StorageServices
from ._sync import SyncEntry

__all__ = ["StorageServices", "SyncEntry"]
