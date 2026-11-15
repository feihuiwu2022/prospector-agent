"""Thread-safe JSON persistence for the local, single-user prototype."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path


DEFAULT_PROFILE = {
    "market": "日本",
    "platform": "Amazon.co.jp",
    "sourcingCountry": "中国",
    "sourcingPlatforms": ["1688", "taobao", "jd", "pdd"],
    "fulfillment": "日本 FBA",
    "budgetCny": 100000,
    "inventoryTolerance": "允许少量囤货",
    "riskTolerance": "中等风险",
    "sourcingCapabilities": "",
    # When enabled, the operator's rejection reasons are fed back to the
    # agent as "do not propose this again" hints.
    "avoidRejected": False,
    # Cost assumptions used to turn a visible price gap into a contribution
    # estimate.  Empty values mean "not decided yet", never zero.
    # ``byPlatform`` holds per-platform overrides (platform id -> fees).
    "priceGapAssumptions": {
        "referralRate": "",
        "adCost": "",
        "fulfillmentCost": "",
        "returnReserveRate": "",
        "byPlatform": {},
    },
}

DEFAULT_STATE = {
    "candidates": [],
    "profile": DEFAULT_PROFILE,
    "discoveryRuns": [],
    "conversations": [],
    "priceObservations": [],
    "suppliers": [],
}


class JsonStore:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write(dict(DEFAULT_STATE))

    def _write(self, data: dict) -> None:
        temporary = self.path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(self.path)

    def read(self) -> dict:
        with self.lock:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                data = dict(DEFAULT_STATE)
            for key, default in DEFAULT_STATE.items():
                data.setdefault(key, default.copy() if isinstance(default, (dict, list)) else default)
            # Merge new profile keys into a stored profile instead of letting an
            # older file hide them; operators keep their saved values.
            profile = data.get("profile")
            if not isinstance(profile, dict):
                profile = dict(DEFAULT_PROFILE)
            else:
                for key, default in DEFAULT_PROFILE.items():
                    profile.setdefault(key, default.copy() if isinstance(default, (dict, list)) else default)
            data["profile"] = profile
            return data

    def update(self, callback):
        with self.lock:
            data = self.read()
            result = callback(data)
            self._write(data)
            return result

