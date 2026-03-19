from __future__ import annotations

import base64
from copy import deepcopy
from dataclasses import dataclass, field
import json
import plistlib
from plistlib import UID
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit
import uuid

from .plist_store import ConfigRecord
from .plist_store import decode_nskeyed_blob
from .subscriptions import ParsedNode
from .subscriptions import SubscriptionFetchResult

CLASS_NAME = "V2rayU.V2rayItem"
CLASS_HIERARCHY = [CLASS_NAME, "NSObject"]


@dataclass(frozen=True)
class PlannedConfigEntry:
    key: str
    action: str
    subscribe: str
    url: str
    remark: str
    name: str
    json_text: str
    speed: str
    is_valid: bool
    scheme: str
    blob: bytes
    notes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ConfigPlan:
    entries: list[PlannedConfigEntry]
    stale_config_keys: list[str]
    errors: list[str]


def build_config_plan(
    existing_configs: list[ConfigRecord],
    fetch_results: list[SubscriptionFetchResult],
) -> ConfigPlan:
    existing_by_pair: dict[tuple[str, str], ConfigRecord] = {}
    managed_subscriptions: set[str] = set()
    template_json_obj = _select_template_json(existing_configs) or _default_template_json()

    for item in existing_configs:
        if item.subscribe and item.url:
            existing_by_pair[(item.subscribe, item.url)] = item

    entries: list[PlannedConfigEntry] = []
    plan_errors: list[str] = []
    desired_pairs: set[tuple[str, str]] = set()

    for fetch in fetch_results:
        if fetch.error or not fetch.parsed:
            plan_errors.append(f"{fetch.key}: payload unavailable ({fetch.error or 'no parsed payload'})")
            continue
        managed_subscriptions.add(fetch.key)

        for node in fetch.parsed.nodes:
            pair = (fetch.key, node.uri)
            if pair in desired_pairs:
                continue
            desired_pairs.add(pair)
            existing = existing_by_pair.get(pair)
            key = existing.key if existing else _new_config_key()
            action = "update" if existing else "create"
            remark = node.name or (existing.remark if existing and existing.remark else key)
            speed = existing.speed if existing and existing.speed else ""

            json_text, notes, errors = _build_json_for_node(
                node=node,
                template_json_obj=template_json_obj,
            )
            if errors:
                plan_errors.extend(f"{key}: {error}" for error in errors)
                continue

            payload = {
                "Name": key,
                "Remark": remark,
                "Json": json_text,
                "Url": node.uri,
                "Subscribe": fetch.key,
                "Speed": speed,
                "IsValid": True,
            }
            blob = encode_config_archive(payload)

            decoded, method = decode_nskeyed_blob(blob)
            if not isinstance(decoded, dict):
                plan_errors.append(f"{key}: round-trip decode failed (root type)")
                continue
            for field_name in ("Name", "Remark", "Url", "Subscribe", "Json", "IsValid"):
                if decoded.get(field_name) != payload[field_name]:
                    plan_errors.append(f"{key}: round-trip mismatch in field {field_name}")
                    break
            else:
                notes.append(f"archive_roundtrip={method}")
                entries.append(
                    PlannedConfigEntry(
                        key=key,
                        action=action,
                        subscribe=fetch.key,
                        url=node.uri,
                        remark=remark,
                        name=key,
                        json_text=json_text,
                        speed=speed,
                        is_valid=True,
                        scheme=node.scheme,
                        blob=blob,
                        notes=notes,
                    )
                )

    stale = [
        item.key
        for item in existing_configs
        if item.subscribe in managed_subscriptions
        and item.subscribe
        and item.url
        and (item.subscribe, item.url) not in desired_pairs
    ]
    return ConfigPlan(entries=entries, stale_config_keys=sorted(set(stale)), errors=plan_errors)


def encode_config_archive(payload: dict[str, Any]) -> bytes:
    objects: list[Any] = ["$null"]

    def add(value: Any) -> UID:
        objects.append(value)
        return UID(len(objects) - 1)

    root: dict[str, Any] = {}
    root_uid = add(root)

    for key in ("Name", "Remark", "Json", "Url", "Subscribe", "Speed"):
        root[key] = add(str(payload.get(key, "")))
    root["IsValid"] = bool(payload.get("IsValid", True))
    root["$class"] = add({"$classname": CLASS_NAME, "$classes": CLASS_HIERARCHY})

    archive = {
        "$version": 100000,
        "$archiver": "NSKeyedArchiver",
        "$top": {"root": root_uid},
        "$objects": objects,
    }
    return plistlib.dumps(archive, fmt=plistlib.FMT_BINARY, sort_keys=False)


def _build_json_for_node(
    node: ParsedNode,
    template_json_obj: dict[str, Any],
) -> tuple[str, list[str], list[str]]:
    notes: list[str] = [f"scheme={node.scheme}"]
    errors: list[str] = []

    if node.scheme == "vless":
        uri, parse_notes, parse_error = _parse_vless_uri(node.uri)
        notes.extend(parse_notes)
        if parse_error or uri is None:
            errors.append(parse_error or "VLESS parse failed")
            return "", notes, errors
        config_obj = _build_vless_config(template_json_obj, uri, notes)
        return json.dumps(config_obj, ensure_ascii=False, indent=2), notes, errors

    if node.scheme == "ss":
        uri, parse_error = _parse_ss_uri(node.uri)
        if parse_error or uri is None:
            errors.append(parse_error or "SS parse failed")
            return "", notes, errors
        config_obj = _build_ss_config(template_json_obj, uri)
        return json.dumps(config_obj, ensure_ascii=False, indent=2), notes, errors

    if node.scheme == "trojan":
        uri, parse_error = _parse_trojan_uri(node.uri)
        if parse_error or uri is None:
            errors.append(parse_error or "Trojan parse failed")
            return "", notes, errors
        config_obj = _build_trojan_config(template_json_obj, uri)
        return json.dumps(config_obj, ensure_ascii=False, indent=2), notes, errors

    if node.scheme == "vmess":
        uri, parse_error = _parse_vmess_uri(node.uri)
        if parse_error or uri is None:
            errors.append(parse_error or "VMess parse failed")
            return "", notes, errors
        config_obj = _build_vmess_config(template_json_obj, uri)
        return json.dumps(config_obj, ensure_ascii=False, indent=2), notes, errors

    errors.append(f"Unsupported scheme: {node.scheme}")
    return "", notes, errors


def _select_template_json(existing_configs: list[ConfigRecord]) -> dict[str, Any] | None:
    for item in existing_configs:
        if not item.json:
            continue
        parsed = _parse_json(item.json)
        if parsed is not None:
            return deepcopy(parsed)
    return None


def _default_template_json() -> dict[str, Any]:
    return {
        "log": {"loglevel": "info", "access": "", "error": ""},
        "inbounds": [
            {
                "port": 1086,
                "listen": "127.0.0.1",
                "protocol": "socks",
                "settings": {"auth": "noauth", "udp": True, "ip": "127.0.0.1"},
            },
            {
                "port": 1087,
                "listen": "127.0.0.1",
                "protocol": "http",
                "settings": {"auth": "noauth", "udp": True, "ip": "127.0.0.1"},
            },
        ],
        "outbounds": [{"protocol": "freedom", "settings": {}, "tag": "proxy"}],
        "routing": {"domainStrategy": "AsIs", "rules": []},
        "dns": {"servers": ["1.1.1.1", "8.8.8.8", "localhost"]},
    }


def _parse_json(text: str) -> dict[str, Any] | None:
    try:
        loaded = json.loads(text)
    except Exception:
        return None
    return loaded if isinstance(loaded, dict) else None


def _prepare_outbound(template_json_obj: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    patched = deepcopy(template_json_obj)
    outbounds = patched.setdefault("outbounds", [])
    if not isinstance(outbounds, list):
        outbounds = []
        patched["outbounds"] = outbounds
    if not outbounds:
        outbounds.append({})
    if not isinstance(outbounds[0], dict):
        outbounds[0] = {}
    outbound = outbounds[0]
    outbound["tag"] = "proxy"
    return patched, outbound


@dataclass(frozen=True)
class VlessUri:
    user_id: str
    host: str
    port: int
    network: str
    security: str
    flow: str
    encryption: str
    path: str
    mode: str
    host_param: str
    spx: str
    has_mode: bool
    has_host: bool
    has_spx: bool
    sni: str
    fp: str
    pbk: str
    sid: str
    alpn: str
    allow_insecure: bool | None
    extra: dict[str, Any] | None


def _parse_vless_uri(uri: str) -> tuple[VlessUri | None, list[str], str | None]:
    notes: list[str] = []
    split = urlsplit(uri)
    if split.scheme.lower() != "vless":
        return None, notes, "URI scheme is not vless"
    if not split.hostname:
        return None, notes, "VLESS URI host is missing"
    if split.port is None:
        return None, notes, "VLESS URI port is missing"
    user_id = unquote(split.username or "")
    if not user_id:
        return None, notes, "VLESS URI user id is missing"

    query = parse_qs(split.query, keep_blank_values=True)

    def q(name: str, default: str = "") -> str:
        values = query.get(name)
        if not values:
            return default
        return unquote(values[0])

    extra_obj: dict[str, Any] | None = None
    extra_raw = q("extra")
    if "extra" in query and extra_raw:
        try:
            parsed_extra = json.loads(extra_raw)
        except Exception:
            notes.append("xhttp_extra_invalid_json")
        else:
            if isinstance(parsed_extra, dict):
                extra_obj = parsed_extra
                notes.append("xhttp_extra_loaded")
            else:
                notes.append("xhttp_extra_non_object")

    return (
        VlessUri(
            user_id=user_id,
            host=split.hostname,
            port=split.port,
            network=(q("type", "tcp") or "tcp").lower(),
            security=(q("security", "none") or "none").lower(),
            flow=q("flow"),
            encryption=q("encryption", "none") or "none",
            path=q("path"),
            mode=q("mode"),
            host_param=q("host"),
            spx=q("spx"),
            has_mode="mode" in query,
            has_host="host" in query,
            has_spx="spx" in query,
            sni=q("sni"),
            fp=q("fp"),
            pbk=q("pbk"),
            sid=q("sid"),
            alpn=q("alpn"),
            allow_insecure=_parse_optional_bool(q("allowInsecure"), present="allowInsecure" in query),
            extra=extra_obj,
        ),
        notes,
        None,
    )


def _build_vless_config(template_json_obj: dict[str, Any], uri: VlessUri, notes: list[str]) -> dict[str, Any]:
    patched, outbound = _prepare_outbound(template_json_obj)
    outbound["protocol"] = "vless"
    outbound["settings"] = {
        "vnext": [
            {
                "address": uri.host,
                "port": uri.port,
                "users": [
                    {
                        "id": uri.user_id,
                        "encryption": uri.encryption,
                        "flow": uri.flow,
                        "level": 0,
                    }
                ],
            }
        ]
    }

    stream_settings: dict[str, Any] = {
        "network": uri.network,
        "security": uri.security,
    }

    if uri.network == "xhttp":
        xhttp: dict[str, Any] = {}
        if uri.extra:
            xhttp.update(uri.extra)
        xhttp["path"] = uri.path
        if uri.has_mode:
            xhttp["mode"] = uri.mode
        if uri.has_host:
            xhttp["host"] = uri.host_param
        if uri.has_spx:
            xhttp["spx"] = uri.spx
        stream_settings["xhttpSettings"] = xhttp
        notes.append("xhttp_settings_patched")
        if "path" in xhttp:
            notes.append(f"xhttp_path={xhttp.get('path', '')}")
        if "mode" in xhttp:
            notes.append(f"xhttp_mode={xhttp.get('mode', '')}")

    if uri.security == "reality":
        stream_settings["realitySettings"] = {
            "serverName": uri.sni,
            "fingerprint": uri.fp,
            "publicKey": uri.pbk,
            "shortId": uri.sid,
            "spiderX": "",
            "show": True,
        }
    elif uri.security == "tls":
        tls_settings: dict[str, Any] = {
            "serverName": uri.sni,
            "allowInsecure": True if uri.allow_insecure is None else uri.allow_insecure,
            "alpn": [uri.alpn or ""],
        }
        if uri.fp:
            tls_settings["fingerprint"] = uri.fp
        stream_settings["tlsSettings"] = tls_settings

    outbound["streamSettings"] = stream_settings
    return patched


@dataclass(frozen=True)
class SsUri:
    method: str
    password: str
    host: str
    port: int


def _parse_ss_uri(uri: str) -> tuple[SsUri | None, str | None]:
    split = urlsplit(uri)
    if split.scheme.lower() != "ss":
        return None, "URI scheme is not ss"
    if not split.hostname:
        return None, "SS URI host is missing"
    if split.port is None:
        return None, "SS URI port is missing"

    username = unquote(split.username or "")
    password = unquote(split.password or "")

    if not password:
        decoded = _decode_base64_text(username)
        if not decoded or ":" not in decoded:
            return None, "SS URI credentials are invalid"
        method, secret = decoded.split(":", 1)
    else:
        method = username
        secret = password

    if not method or not secret:
        return None, "SS URI method/password is missing"

    return SsUri(method=method, password=secret, host=split.hostname, port=split.port), None


def _build_ss_config(template_json_obj: dict[str, Any], uri: SsUri) -> dict[str, Any]:
    patched, outbound = _prepare_outbound(template_json_obj)
    outbound["protocol"] = "shadowsocks"
    outbound["settings"] = {
        "servers": [
            {
                "address": uri.host,
                "port": uri.port,
                "method": uri.method,
                "password": uri.password,
                "ota": False,
                "level": 0,
                "email": "",
            }
        ]
    }
    outbound["streamSettings"] = {
        "network": "tcp",
        "security": "none",
        "tcpSettings": {"header": {"type": "none"}},
    }
    return patched


@dataclass(frozen=True)
class TrojanUri:
    password: str
    host: str
    port: int
    network: str
    security: str
    sni: str
    fp: str
    alpn: str
    allow_insecure: bool | None
    ws_host: str
    ws_path: str
    grpc_service_name: str


def _parse_trojan_uri(uri: str) -> tuple[TrojanUri | None, str | None]:
    split = urlsplit(uri)
    if split.scheme.lower() != "trojan":
        return None, "URI scheme is not trojan"
    if not split.hostname:
        return None, "Trojan URI host is missing"
    if split.port is None:
        return None, "Trojan URI port is missing"
    password = unquote(split.username or "")
    if not password:
        return None, "Trojan URI password is missing"

    query = parse_qs(split.query, keep_blank_values=True)

    def q(name: str, default: str = "") -> str:
        values = query.get(name)
        if not values:
            return default
        return unquote(values[0])

    network = (q("type", "tcp") or "tcp").lower()
    security = (q("security", "tls") or "tls").lower()

    return (
        TrojanUri(
            password=password,
            host=split.hostname,
            port=split.port,
            network=network,
            security=security,
            sni=q("sni"),
            fp=q("fp"),
            alpn=q("alpn"),
            allow_insecure=_parse_optional_bool(q("allowInsecure"), present="allowInsecure" in query),
            ws_host=q("host"),
            ws_path=q("path"),
            grpc_service_name=q("serviceName"),
        ),
        None,
    )


def _build_trojan_config(template_json_obj: dict[str, Any], uri: TrojanUri) -> dict[str, Any]:
    patched, outbound = _prepare_outbound(template_json_obj)
    outbound["protocol"] = "trojan"
    outbound["settings"] = {
        "servers": [
            {
                "address": uri.host,
                "port": uri.port,
                "password": uri.password,
                "level": 0,
                "email": "",
            }
        ]
    }

    stream_settings: dict[str, Any] = {
        "network": uri.network,
        "security": uri.security,
    }
    if uri.security == "tls":
        tls_settings: dict[str, Any] = {
            "serverName": uri.sni,
            "allowInsecure": True if uri.allow_insecure is None else uri.allow_insecure,
            "alpn": [uri.alpn or ""],
        }
        if uri.fp:
            tls_settings["fingerprint"] = uri.fp
        stream_settings["tlsSettings"] = tls_settings

    if uri.network == "ws":
        ws_settings: dict[str, Any] = {"path": uri.ws_path or "/"}
        if uri.ws_host:
            ws_settings["headers"] = {"Host": uri.ws_host}
        stream_settings["wsSettings"] = ws_settings
    elif uri.network == "grpc":
        stream_settings["grpcSettings"] = {"serviceName": uri.grpc_service_name}

    outbound["streamSettings"] = stream_settings
    return patched


@dataclass(frozen=True)
class VmessUri:
    host: str
    port: int
    user_id: str
    alter_id: int
    security_user: str
    network: str
    security: str
    sni: str
    fp: str
    alpn: str
    ws_host: str
    ws_path: str
    grpc_service_name: str
    tcp_header_type: str


def _parse_vmess_uri(uri: str) -> tuple[VmessUri | None, str | None]:
    split = urlsplit(uri)
    if split.scheme.lower() != "vmess":
        return None, "URI scheme is not vmess"

    token = uri[len("vmess://") :]
    decoded = _decode_base64_text(token)
    if not decoded:
        return None, "VMess URI payload is not valid base64"

    try:
        payload = json.loads(decoded)
    except Exception:
        return None, "VMess URI payload is not valid JSON"
    if not isinstance(payload, dict):
        return None, "VMess URI payload JSON root is not object"

    host = str(payload.get("add", "")).strip()
    port_raw = str(payload.get("port", "")).strip()
    user_id = str(payload.get("id", "")).strip()
    if not host or not port_raw or not user_id:
        return None, "VMess URI required fields add/port/id are missing"

    try:
        port = int(port_raw)
    except ValueError:
        return None, "VMess URI port is invalid"

    alter_id_raw = str(payload.get("aid", "0")).strip() or "0"
    try:
        alter_id = int(alter_id_raw)
    except ValueError:
        alter_id = 0

    net = (str(payload.get("net", "tcp")) or "tcp").lower()
    tls_flag = str(payload.get("tls", "")).lower()
    security = "tls" if tls_flag in {"tls", "xtls"} else "none"

    return (
        VmessUri(
            host=host,
            port=port,
            user_id=user_id,
            alter_id=alter_id,
            security_user=str(payload.get("scy", "auto") or "auto"),
            network=net,
            security=security,
            sni=str(payload.get("sni", "")),
            fp=str(payload.get("fp", "")),
            alpn=str(payload.get("alpn", "")),
            ws_host=str(payload.get("host", "")),
            ws_path=str(payload.get("path", "")),
            grpc_service_name=str(payload.get("path", "")),
            tcp_header_type=str(payload.get("type", "none") or "none"),
        ),
        None,
    )


def _build_vmess_config(template_json_obj: dict[str, Any], uri: VmessUri) -> dict[str, Any]:
    patched, outbound = _prepare_outbound(template_json_obj)
    outbound["protocol"] = "vmess"
    outbound["settings"] = {
        "vnext": [
            {
                "address": uri.host,
                "port": uri.port,
                "users": [
                    {
                        "id": uri.user_id,
                        "alterId": uri.alter_id,
                        "security": uri.security_user,
                        "level": 0,
                    }
                ],
            }
        ]
    }

    stream_settings: dict[str, Any] = {
        "network": uri.network,
        "security": uri.security,
    }
    if uri.network == "ws":
        ws_settings: dict[str, Any] = {"path": uri.ws_path or "/"}
        if uri.ws_host:
            ws_settings["headers"] = {"Host": uri.ws_host}
        stream_settings["wsSettings"] = ws_settings
    elif uri.network == "grpc":
        stream_settings["grpcSettings"] = {"serviceName": uri.grpc_service_name}
    elif uri.network == "tcp":
        stream_settings["tcpSettings"] = {"header": {"type": uri.tcp_header_type or "none"}}

    if uri.security == "tls":
        tls_settings: dict[str, Any] = {
            "serverName": uri.sni,
            "allowInsecure": True,
            "alpn": [uri.alpn or ""],
        }
        if uri.fp:
            tls_settings["fingerprint"] = uri.fp
        stream_settings["tlsSettings"] = tls_settings

    outbound["streamSettings"] = stream_settings
    return patched


def _parse_optional_bool(value: str, present: bool) -> bool | None:
    if not present:
        return None
    lowered = value.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    return None


def _decode_base64_text(value: str) -> str | None:
    compact = "".join(value.split())
    if not compact:
        return None
    padded = compact + ("=" * ((4 - len(compact) % 4) % 4))
    for decoder in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            decoded = decoder(padded.encode("ascii"))
            return decoded.decode("utf-8")
        except Exception:
            continue
    return None


def _new_config_key() -> str:
    return f"config.{str(uuid.uuid4()).upper()}"
