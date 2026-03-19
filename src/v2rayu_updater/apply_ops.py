from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile
from typing import Any

from .config_plan import ConfigPlan


@dataclass(frozen=True)
class ApplySummary:
    planned: int
    created: int
    updated: int
    removed_stale: int
    server_list_count: int
    current_server: str


def is_v2rayu_running() -> bool:
    try:
        result = subprocess.run(
            ["pgrep", "-x", "V2rayU"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if result.returncode == 0:
            return True
        if result.returncode == 1:
            return False
    except Exception:
        pass

    try:
        result = subprocess.run(
            ["ps", "-A", "-o", "comm="],
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception:
        return False
    if result.returncode != 0:
        return False
    for line in result.stdout.splitlines():
        command = line.strip()
        if command.endswith("/V2rayU") or command == "V2rayU":
            return True
    return False


def is_binary_plist_file(plist_path: Path) -> bool:
    try:
        with plist_path.open("rb") as handle:
            header = handle.read(8)
    except OSError:
        return True
    return header.startswith(b"bplist00")


def apply_config_plan_to_plist(
    plist_data: dict[str, Any],
    config_plan: ConfigPlan,
) -> tuple[dict[str, Any], ApplySummary]:
    updated = dict(plist_data)

    for entry in config_plan.entries:
        updated[entry.key] = entry.blob

    stale_set = set(config_plan.stale_config_keys)
    for stale_key in stale_set:
        updated.pop(stale_key, None)

    server_list = _rebuild_server_list(updated, config_plan)
    updated["v2rayServerList"] = server_list

    current = updated.get("v2rayCurrentServerName")
    if not isinstance(current, str) or current not in server_list:
        current = server_list[0] if server_list else ""
        updated["v2rayCurrentServerName"] = current

    created = sum(1 for entry in config_plan.entries if entry.action == "create")
    updated_count = sum(1 for entry in config_plan.entries if entry.action == "update")
    summary = ApplySummary(
        planned=len(config_plan.entries),
        created=created,
        updated=updated_count,
        removed_stale=len(stale_set),
        server_list_count=len(server_list),
        current_server=current,
    )
    return updated, summary


def create_plist_backup(plist_path: Path, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"{plist_path.name}.bak"

    tmp_backup = backup_dir / f"{plist_path.name}.bak.tmp"
    shutil.copy2(plist_path, tmp_backup)
    os.replace(tmp_backup, backup_path)
    return backup_path


def write_plist_atomic(plist_path: Path, plist_data: dict[str, Any], binary: bool) -> None:
    fmt = plistlib.FMT_BINARY if binary else plistlib.FMT_XML
    tmp_path: Path | None = None

    with tempfile.NamedTemporaryFile(
        mode="wb",
        delete=False,
        dir=str(plist_path.parent),
        prefix=f"{plist_path.name}.",
        suffix=".tmp",
    ) as tmp_handle:
        tmp_path = Path(tmp_handle.name)
        plistlib.dump(plist_data, tmp_handle, fmt=fmt, sort_keys=False)

    os.replace(tmp_path, plist_path)


def _rebuild_server_list(plist_data: dict[str, Any], config_plan: ConfigPlan) -> list[str]:
    existing_list = plist_data.get("v2rayServerList")
    existing_keys: list[str] = []
    if isinstance(existing_list, list):
        for item in existing_list:
            if isinstance(item, str):
                existing_keys.append(item)

    planned_keys = [entry.key for entry in config_plan.entries]
    planned_key_set = set(planned_keys)
    stale_set = set(config_plan.stale_config_keys)
    rebuilt: list[str] = []
    seen: set[str] = set()

    for key in existing_keys:
        if key in stale_set:
            continue
        if key in planned_key_set:
            if key not in seen:
                rebuilt.append(key)
                seen.add(key)
            continue
        if isinstance(plist_data.get(key), (bytes, bytearray)) and key.startswith("config."):
            if key not in seen:
                rebuilt.append(key)
                seen.add(key)

    for key in planned_keys:
        if key not in seen:
            rebuilt.append(key)
            seen.add(key)

    for key in sorted(plist_data.keys()):
        if key.startswith("config.") and isinstance(plist_data.get(key), (bytes, bytearray)):
            if key not in seen:
                rebuilt.append(key)
                seen.add(key)

    return rebuilt
