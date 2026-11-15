"""Connector capability catalogue, derived from the shared platform registry."""

from __future__ import annotations

from prospector.platforms import PLATFORMS, ROLE_LABELS, platform_catalog


def connector_status() -> list[dict]:
    """Return safe capability metadata; never return credential values."""
    return [
        {
            "id": item["id"],
            "platform": item["name"],
            "status": item["access"],
            "role": item["role"],
            "roleLabel": ROLE_LABELS.get(item["role"], item["role"]),
            "region": item["region"],
            "capabilities": item["capabilities"],
            "access": item["accessText"],
            "note": item["note"],
            "authorizationChannel": item["authorizationChannel"],
            "connected": False,
        }
        for item in PLATFORMS.values()
    ]


def platform_metadata() -> list[dict]:
    """Platform table used by the profile form and the data-source screen."""
    return platform_catalog()
