from __future__ import annotations

import base64
from dataclasses import dataclass, field
from time import monotonic
from typing import Iterable
from urllib.parse import unquote, urlsplit

from .plist_store import SubscriptionRecord

SUPPORTED_URI_SCHEMES = ("vmess://", "vless://", "trojan://", "ss://")


@dataclass(frozen=True)
class ParsedNode:
    scheme: str
    uri: str
    name: str | None


@dataclass(frozen=True)
class ParsedSubscription:
    format: str
    nodes: list[ParsedNode] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SubscriptionFetchResult:
    key: str
    url: str | None
    remark: str | None
    status_code: int | None
    content_type: str | None
    elapsed_ms: int | None
    parsed: ParsedSubscription | None
    error: str | None = None


def build_request_headers(
    headers: Iterable[str] | None,
    x_hwid: str | None,
) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for item in headers or ():
        name, value = _split_header(item)
        parsed[name] = value
    if x_hwid:
        parsed["x-hwid"] = x_hwid
    return parsed


def fetch_subscription_payloads(
    subscriptions: Iterable[SubscriptionRecord],
    timeout: float,
    headers: dict[str, str] | None = None,
    verify_tls: bool = True,
) -> list[SubscriptionFetchResult]:
    try:
        import requests
    except ModuleNotFoundError:
        message = (
            "requests is not installed. Install dependencies first: "
            "pip install -r requirements.txt"
        )
        return [
            SubscriptionFetchResult(
                key=subscription.key,
                url=subscription.url,
                remark=subscription.remark,
                status_code=None,
                content_type=None,
                elapsed_ms=None,
                parsed=None,
                error=message,
            )
            for subscription in subscriptions
        ]

    results: list[SubscriptionFetchResult] = []
    session = requests.Session()
    session.trust_env = False

    for subscription in subscriptions:
        if not subscription.url:
            results.append(
                SubscriptionFetchResult(
                    key=subscription.key,
                    url=None,
                    remark=subscription.remark,
                    status_code=None,
                    content_type=None,
                    elapsed_ms=None,
                    parsed=None,
                    error="Subscription URL is missing",
                )
            )
            continue

        started = monotonic()
        try:
            response = session.get(
                subscription.url,
                timeout=timeout,
                headers=headers,
                verify=verify_tls,
            )
        except requests.RequestException as exc:
            elapsed_ms = int((monotonic() - started) * 1000)
            results.append(
                SubscriptionFetchResult(
                    key=subscription.key,
                    url=subscription.url,
                    remark=subscription.remark,
                    status_code=None,
                    content_type=None,
                    elapsed_ms=elapsed_ms,
                    parsed=None,
                    error=f"request failed: {exc}",
                )
            )
            continue

        elapsed_ms = int((monotonic() - started) * 1000)
        parsed = parse_subscription_content(
            content=response.content,
            content_type=response.headers.get("content-type"),
        )

        error = None
        if response.status_code >= 400:
            error = f"HTTP {response.status_code}"

        results.append(
            SubscriptionFetchResult(
                key=subscription.key,
                url=subscription.url,
                remark=subscription.remark,
                status_code=response.status_code,
                content_type=response.headers.get("content-type"),
                elapsed_ms=elapsed_ms,
                parsed=parsed,
                error=error,
            )
        )

    return results


def parse_subscription_content(content: bytes, content_type: str | None) -> ParsedSubscription:
    text = _decode_text(content)
    if not text.strip():
        return ParsedSubscription(format="empty")

    direct_nodes = _extract_nodes_from_text(text)
    if direct_nodes:
        return ParsedSubscription(format="plain-uri", nodes=direct_nodes)

    decoded_text = _try_decode_base64_block(text)
    if decoded_text:
        decoded_nodes = _extract_nodes_from_text(decoded_text)
        if decoded_nodes:
            return ParsedSubscription(format="base64-uri", nodes=decoded_nodes)

    if _looks_like_yaml(text, content_type):
        return ParsedSubscription(
            format="yaml-unsupported",
            warnings=[
                "YAML-like subscription detected. Structured YAML proxy conversion is not implemented yet."
            ],
        )

    return ParsedSubscription(
        format="unknown",
        warnings=["Could not detect URI list format (plain/base64)."],
    )


def _extract_nodes_from_text(text: str) -> list[ParsedNode]:
    nodes: list[ParsedNode] = []
    seen: set[str] = set()

    for raw_line in text.splitlines():
        candidate = _normalize_candidate(raw_line)
        if not candidate:
            continue
        scheme = _detect_scheme(candidate)
        if not scheme:
            continue
        if candidate in seen:
            continue
        seen.add(candidate)
        nodes.append(
            ParsedNode(
                scheme=scheme,
                uri=candidate,
                name=_extract_node_name(candidate),
            )
        )
    return nodes


def _normalize_candidate(raw_line: str) -> str | None:
    line = raw_line.strip()
    if not line:
        return None
    if line.startswith("#"):
        return None

    # YAML list item like "- vmess://..."
    if line.startswith("- "):
        line = line[2:].strip()

    # key: value style where value might be URI
    if ":" in line and "://" in line and not _starts_with_supported_scheme(line):
        _, value = line.split(":", 1)
        line = value.strip()

    line = line.strip("'\"")
    return line or None


def _starts_with_supported_scheme(value: str) -> bool:
    lowered = value.lower()
    return any(lowered.startswith(prefix) for prefix in SUPPORTED_URI_SCHEMES)


def _detect_scheme(uri: str) -> str | None:
    lowered = uri.lower()
    for prefix in SUPPORTED_URI_SCHEMES:
        if lowered.startswith(prefix):
            return prefix.removesuffix("://")
    return None


def _extract_node_name(uri: str) -> str | None:
    try:
        fragment = urlsplit(uri).fragment
    except Exception:
        return None
    if not fragment:
        return None
    return unquote(fragment) or None


def _try_decode_base64_block(text: str) -> str | None:
    compact = "".join(text.split())
    if not compact:
        return None
    if len(compact) < 16:
        return None

    allowed = set(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=_-"
    )
    if not set(compact).issubset(allowed):
        return None

    padded = compact + ("=" * ((4 - len(compact) % 4) % 4))
    decoders = (base64.b64decode, base64.urlsafe_b64decode)
    for decoder in decoders:
        try:
            decoded = decoder(padded.encode("ascii"))
            text = decoded.decode("utf-8")
        except Exception:
            continue
        if any(text.lower().find(prefix) >= 0 for prefix in SUPPORTED_URI_SCHEMES):
            return text
    return None


def _decode_text(content: bytes) -> str:
    for encoding in ("utf-8", "utf-8-sig"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def _looks_like_yaml(text: str, content_type: str | None) -> bool:
    if content_type and "yaml" in content_type.lower():
        return True
    lowered = text.lower()
    if "\nproxies:" in lowered or lowered.startswith("proxies:"):
        return True
    if "\nproxy-providers:" in lowered or lowered.startswith("proxy-providers:"):
        return True
    return False


def _split_header(header: str) -> tuple[str, str]:
    if ":" not in header:
        raise ValueError(f"Invalid header format (expected 'Name: Value'): {header}")
    name, value = header.split(":", 1)
    name = name.strip()
    value = value.strip()
    if not name:
        raise ValueError(f"Header name is empty: {header}")
    return name, value
