"""Connector interface. Implementations must use explicitly authorized access."""

from __future__ import annotations

from typing import Protocol


class ConnectorNotConfigured(RuntimeError):
    pass


class MarketplaceConnector(Protocol):
    connector_id: str

    def search_products(self, query: str, market: str) -> list[dict]: ...

    def get_product(self, external_id: str, market: str) -> dict: ...
