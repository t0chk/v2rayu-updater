from __future__ import annotations

import base64
import unittest

from v2rayu_updater.subscriptions import build_request_headers
from v2rayu_updater.subscriptions import parse_subscription_content


class SubscriptionParsingTests(unittest.TestCase):
    def test_plain_uri_list(self) -> None:
        text = (
            "vmess://abc#node1\n"
            "vless://uuid@example.com:443?type=tcp#node2\n"
            "not-a-uri\n"
        )
        parsed = parse_subscription_content(text.encode("utf-8"), None)
        self.assertEqual(parsed.format, "plain-uri")
        self.assertEqual(len(parsed.nodes), 2)
        self.assertEqual({node.scheme for node in parsed.nodes}, {"vmess", "vless"})

    def test_base64_uri_list(self) -> None:
        text = "vmess://abc#node1\ntrojan://pass@example.com:443#node2\n"
        encoded = base64.b64encode(text.encode("utf-8"))
        parsed = parse_subscription_content(encoded, "text/plain")
        self.assertEqual(parsed.format, "base64-uri")
        self.assertEqual(len(parsed.nodes), 2)
        self.assertEqual({node.scheme for node in parsed.nodes}, {"vmess", "trojan"})

    def test_yaml_detected(self) -> None:
        content = b"proxies:\n  - name: test\n    type: vless\n"
        parsed = parse_subscription_content(content, "application/yaml")
        self.assertEqual(parsed.format, "yaml-unsupported")
        self.assertEqual(len(parsed.nodes), 0)
        self.assertTrue(parsed.warnings)


class HeaderParsingTests(unittest.TestCase):
    def test_build_headers(self) -> None:
        headers = build_request_headers(["Authorization: Bearer abc"], "hwid-value")
        self.assertEqual(headers["Authorization"], "Bearer abc")
        self.assertEqual(headers["x-hwid"], "hwid-value")

    def test_invalid_header(self) -> None:
        with self.assertRaises(ValueError):
            build_request_headers(["broken-header"], None)


if __name__ == "__main__":
    unittest.main()
