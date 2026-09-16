"""Workspace and dataset discovery for GeMMA Fusion Server."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import requests

from .storage import StorageServices


class GFClient:
    """Connect using a server root URL, e.g. https://host/gf-test.

    Returned discovery records retain the server's JSON fields, including
    dataset attributes and datasource details. HTTP and decoding errors raise.
    """

    def __init__(self, base_url: str, timeout: float | None = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._api_key: str | None = None

    def set_api_key(self, key: str) -> None:
        """Set the key for discovery and subsequently created storage clients."""
        self._api_key = key

    def _get(self, path: str) -> Any:
        headers = {"Accept": "application/json"}
        if self._api_key:
            headers["X-API-Key"] = self._api_key
        response = requests.get(
            f"{self.base_url}/api/{path}", headers=headers, timeout=self.timeout
        )
        response.raise_for_status()
        return response.json()

    def workspaces(self) -> list[dict[str, Any]]:
        """List the workspaces returned by the server for the current credentials."""
        result: list[dict[str, Any]] = self._get("ws")
        return result

    def workspace(self, workspace_id: str) -> dict[str, Any]:
        """Get a workspace's name, description and available services."""
        result: dict[str, Any] = self._get(f"ws/{quote(workspace_id, safe='')}")
        return result

    def datasets(self, workspace_id: str) -> list[dict[str, Any]]:
        """List workspace datasets, including the attributes exposed by GF."""
        result: list[dict[str, Any]] = self._get(
            f"ws/{quote(workspace_id, safe='')}/datasets"
        )
        return result

    def datasources(self, workspace_id: str) -> list[dict[str, Any]]:
        """List workspace datasource descriptions and properties."""
        result: list[dict[str, Any]] = self._get(
            f"ws/{quote(workspace_id, safe='')}/datasources"
        )
        return result

    def storage(self, workspace_id: str) -> StorageServices:
        """Create a storage client using the current key and timeout.

        Subsequent key changes on this GFClient do not change existing clients.
        """
        client = StorageServices(
            f"{self.base_url}/api/ws/{quote(workspace_id, safe='')}/services/storage",
            timeout=self.timeout,
        )
        if self._api_key:
            client.set_api_key(self._api_key)
        return client
