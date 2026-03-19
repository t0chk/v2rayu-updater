from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import plistlib
from plistlib import UID
from typing import Any


class DecodeError(Exception):
    """Raised when NSKeyedArchive blob cannot be decoded."""


@dataclass(frozen=True)
class SubscriptionRecord:
    key: str
    name: str | None
    url: str | None
    remark: str | None
    is_valid: bool | None
    decode_method: str
    error: str | None = None


@dataclass(frozen=True)
class ConfigRecord:
    key: str
    name: str | None
    url: str | None
    remark: str | None
    subscribe: str | None
    json: str | None
    speed: str | None
    is_valid: bool | None
    decode_method: str
    error: str | None = None


def resolve_plist_path(plist_path: str) -> Path:
    return Path(plist_path).expanduser().resolve()


def load_plist(plist_path: Path) -> dict[str, Any]:
    if not plist_path.exists():
        raise FileNotFoundError(f"Plist not found: {plist_path}")
    with plist_path.open("rb") as handle:
        loaded = plistlib.load(handle)
    if not isinstance(loaded, dict):
        raise ValueError(f"Unexpected plist root type: {type(loaded).__name__}")
    return loaded


def parse_subscriptions(plist_data: dict[str, Any]) -> list[SubscriptionRecord]:
    records: list[SubscriptionRecord] = []
    for key, blob in _iter_blob_entries(plist_data, "subscribe."):
        try:
            decoded, method = decode_nskeyed_blob(blob)
            if not isinstance(decoded, dict):
                raise DecodeError(f"Decoded root is not dict: {type(decoded).__name__}")
            records.append(
                SubscriptionRecord(
                    key=key,
                    name=_as_str(decoded.get("Name")),
                    url=_as_str(decoded.get("Url")),
                    remark=_as_str(decoded.get("Remark")),
                    is_valid=_as_bool(decoded.get("IsValid")),
                    decode_method=method,
                )
            )
        except Exception as exc:  # noqa: BLE001
            records.append(
                SubscriptionRecord(
                    key=key,
                    name=None,
                    url=None,
                    remark=None,
                    is_valid=None,
                    decode_method="failed",
                    error=str(exc),
                )
            )
    return records


def parse_configs(plist_data: dict[str, Any]) -> list[ConfigRecord]:
    records: list[ConfigRecord] = []
    for key, blob in _iter_blob_entries(plist_data, "config."):
        try:
            decoded, method = decode_nskeyed_blob(blob)
            if not isinstance(decoded, dict):
                raise DecodeError(f"Decoded root is not dict: {type(decoded).__name__}")
            records.append(
                ConfigRecord(
                    key=key,
                    name=_as_str(decoded.get("Name")),
                    url=_as_str(decoded.get("Url")),
                    remark=_as_str(decoded.get("Remark")),
                    subscribe=_as_str(decoded.get("Subscribe")),
                    json=_as_str(decoded.get("Json")),
                    speed=_as_str(decoded.get("Speed")),
                    is_valid=_as_bool(decoded.get("IsValid")),
                    decode_method=method,
                )
            )
        except Exception as exc:  # noqa: BLE001
            records.append(
                ConfigRecord(
                    key=key,
                    name=None,
                    url=None,
                    remark=None,
                    subscribe=None,
                    json=None,
                    speed=None,
                    is_valid=None,
                    decode_method="failed",
                    error=str(exc),
                )
            )
    return records


def decode_nskeyed_blob(blob: bytes) -> tuple[Any, str]:
    if not isinstance(blob, (bytes, bytearray)):
        raise DecodeError(f"Blob must be bytes, got: {type(blob).__name__}")

    try:
        return _decode_with_bpylist2(bytes(blob)), "bpylist2"
    except Exception:  # noqa: BLE001
        # Fallback keeps Phase 1 usable even before dependencies are installed.
        return _decode_with_plistlib(bytes(blob)), "plistlib"


def _iter_blob_entries(
    plist_data: dict[str, Any], prefix: str
) -> list[tuple[str, bytes]]:
    pairs: list[tuple[str, bytes]] = []
    for key in sorted(plist_data.keys()):
        if not key.startswith(prefix):
            continue
        value = plist_data[key]
        if not isinstance(value, (bytes, bytearray)):
            continue
        pairs.append((key, bytes(value)))
    return pairs


def _decode_with_bpylist2(blob: bytes) -> Any:
    try:
        import importlib

        bpylist2 = importlib.import_module("bpylist2")
    except Exception as exc:  # noqa: BLE001
        raise DecodeError(f"bpylist2 unavailable: {exc}") from exc

    candidates: list[tuple[Any, str]] = []

    archiver_attr = getattr(bpylist2, "archiver", None)
    if archiver_attr is not None:
        for name in ("unarchive", "loads", "load"):
            fn = getattr(archiver_attr, name, None)
            if callable(fn):
                candidates.append((fn, name))

    try:
        import importlib

        archiver_module = importlib.import_module("bpylist2.archiver")
        for name in ("unarchive", "loads", "load"):
            fn = getattr(archiver_module, name, None)
            if callable(fn):
                candidates.append((fn, name))
    except Exception:
        pass

    for fn, name in candidates:
        try:
            if name == "load":
                return fn(BytesIO(blob))
            return fn(blob)
        except Exception:
            continue

    raise DecodeError("bpylist2 decode failed for all known call patterns")


def _decode_with_plistlib(blob: bytes) -> Any:
    try:
        archive = plistlib.loads(blob)
    except Exception as exc:  # noqa: BLE001
        raise DecodeError(f"plistlib cannot decode blob: {exc}") from exc

    if not isinstance(archive, dict):
        raise DecodeError("Decoded archive root is not dict")

    if "$objects" not in archive or "$top" not in archive:
        raise DecodeError("Not an NSKeyedArchive structure")

    objects = archive["$objects"]
    top = archive["$top"]
    if not isinstance(objects, list) or not isinstance(top, dict):
        raise DecodeError("Invalid NSKeyedArchive fields")
    root_uid = top.get("root")
    if not isinstance(root_uid, UID):
        raise DecodeError("NSKeyedArchive root UID missing")
    return _resolve_ns_value(root_uid, objects)


def _resolve_ns_value(value: Any, objects: list[Any], depth: int = 0) -> Any:
    if depth > 100:
        raise DecodeError("Archive nesting too deep")

    if isinstance(value, UID):
        index = value.data
        if index >= len(objects):
            raise DecodeError(f"UID out of range: {index}")
        return _resolve_ns_value(objects[index], objects, depth + 1)

    if isinstance(value, list):
        return [_resolve_ns_value(item, objects, depth + 1) for item in value]

    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if key == "$class":
                continue
            out[str(key)] = _resolve_ns_value(item, objects, depth + 1)
        return out

    return value


def _as_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _as_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None
