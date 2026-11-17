from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jssh.config import Config
from jssh.models.asset import Asset, RecentEntry, asset_from_cache, asset_to_dict

ASSETS_FILE = "assets.json"
RECENT_FILE = "recent.json"


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def write_json(path: Path, payload: Any) -> None:
    ensure_dir(path.parent)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def save_assets(cfg: Config, assets: list[Asset], *, stale: bool = False) -> None:
    write_json(
        cfg.cache_dir / ASSETS_FILE,
        {
            "fetched_at": _now(),
            "stale": stale,
            "assets": [asset_to_dict(item) for item in assets],
        },
    )


def load_assets(cfg: Config) -> tuple[list[Asset], dict[str, Any]] | None:
    path = cfg.cache_dir / ASSETS_FILE
    if not path.is_file():
        return None
    try:
        payload = read_json(path)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("assets"), list):
        return None
    assets: list[Asset] = []
    for row in payload["assets"]:
        if isinstance(row, dict):
            asset = asset_from_cache(row)
            if asset:
                assets.append(asset)
    return assets, payload


def cache_age_seconds(payload: dict[str, Any]) -> float | None:
    stamp = payload.get("fetched_at")
    if not isinstance(stamp, str):
        return None
    try:
        fetched = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - fetched).total_seconds())


def is_fresh(cfg: Config, payload: dict[str, Any]) -> bool:
    age = cache_age_seconds(payload)
    if age is None:
        return False
    return age < cfg.cache_ttl


def save_recent(cfg: Config, entry: RecentEntry, *, limit: int = 20) -> None:
    items = load_recent(cfg)
    key = (entry.asset_id, entry.node_key, entry.account_id, entry.action)
    items = [item for item in items if (item.asset_id, item.node_key, item.account_id, item.action) != key]
    items.insert(0, entry)
    write_json(
        cfg.cache_dir / RECENT_FILE,
        [
            {
                "asset_id": item.asset_id,
                "node_key": item.node_key,
                "account_id": item.account_id,
                "hostname": item.hostname,
                "account_name": item.account_name,
                "ts": item.ts,
                "action": item.action,
            }
            for item in items[:limit]
        ],
    )


def load_recent(cfg: Config) -> list[RecentEntry]:
    path = cfg.cache_dir / RECENT_FILE
    if not path.is_file():
        return []
    try:
        payload = read_json(path)
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, list):
        return []
    items: list[RecentEntry] = []
    for row in payload:
        if not isinstance(row, dict):
            continue
        if not row.get("asset_id") or not row.get("hostname"):
            continue
        items.append(
            RecentEntry(
                asset_id=str(row["asset_id"]),
                node_key=str(row.get("node_key") or ""),
                account_id=str(row.get("account_id") or ""),
                hostname=str(row["hostname"]),
                account_name=str(row.get("account_name") or ""),
                ts=str(row.get("ts") or ""),
                action=str(row.get("action") or "sftp"),
            )
        )
    return items


def clear_cache(cfg: Config) -> None:
    for name in (ASSETS_FILE, RECENT_FILE):
        path = cfg.cache_dir / name
        if path.is_file():
            path.unlink()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
