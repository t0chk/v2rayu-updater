from __future__ import annotations

import json
import unittest

from v2rayu_updater.config_plan import build_config_plan
from v2rayu_updater.config_plan import encode_config_archive
from v2rayu_updater.plist_store import ConfigRecord
from v2rayu_updater.plist_store import decode_nskeyed_blob
from v2rayu_updater.subscriptions import ParsedNode
from v2rayu_updater.subscriptions import ParsedSubscription
from v2rayu_updater.subscriptions import SubscriptionFetchResult


class ArchiveEncodingTests(unittest.TestCase):
    def test_encode_config_archive_roundtrip(self) -> None:
        payload = {
            "Name": "config.TEST",
            "Remark": "test",
            "Json": '{"outbounds":[{"protocol":"vless"}]}',
            "Url": "vless://uuid@example.com:443?type=tcp#test",
            "Subscribe": "subscribe.TEST",
            "Speed": "",
            "IsValid": True,
        }
        blob = encode_config_archive(payload)
        decoded, _ = decode_nskeyed_blob(blob)
        self.assertIsInstance(decoded, dict)
        for key in ("Name", "Remark", "Json", "Url", "Subscribe", "IsValid"):
            self.assertEqual(decoded[key], payload[key])


class ConfigPlanTests(unittest.TestCase):
    def test_build_config_plan_patches_xhttp(self) -> None:
        existing_json = json.dumps(
            {
                "outbounds": [
                    {
                        "protocol": "vless",
                        "settings": {"vnext": [{"address": "old", "port": 443, "users": [{}]}]},
                        "streamSettings": {
                            "network": "xhttp",
                            "security": "reality",
                            "xhttpSettings": {"path": "", "mode": ""},
                            "realitySettings": {
                                "serverName": "",
                                "fingerprint": "",
                                "publicKey": "",
                                "shortId": "",
                                "spiderX": "",
                                "show": True,
                            },
                        },
                    }
                ]
            }
        )
        existing = ConfigRecord(
            key="config.EXISTING",
            name="config.EXISTING",
            url="vless://uuid@example.com:443?security=reality&type=xhttp&path=&mode=auto&sni=old&fp=chrome&pbk=pk&sid=sid#old",
            remark="old",
            subscribe="subscribe.ONE",
            json=existing_json,
            speed="",
            is_valid=True,
            decode_method="plistlib",
        )

        node = ParsedNode(
            scheme="vless",
            uri=(
                "vless://uuid@example.com:443?security=reality&type=xhttp&path=%2Fnew-path"
                "&mode=auto&sni=site.test&fp=chrome&pbk=pub&sid=abcd#Node"
            ),
            name="Node",
        )
        fetch = SubscriptionFetchResult(
            key="subscribe.ONE",
            url="https://example.com/sub",
            remark="sub",
            status_code=200,
            content_type="text/plain",
            elapsed_ms=10,
            parsed=ParsedSubscription(format="plain-uri", nodes=[node]),
            error=None,
        )

        plan = build_config_plan(existing_configs=[existing], fetch_results=[fetch])
        self.assertEqual(len(plan.errors), 0)
        self.assertEqual(len(plan.entries), 1)

        entry = plan.entries[0]
        self.assertEqual(entry.action, "create")
        self.assertIn("xhttp_settings_patched", entry.notes)

        js = json.loads(entry.json_text)
        stream = js["outbounds"][0]["streamSettings"]
        self.assertEqual(stream["xhttpSettings"]["path"], "/new-path")
        self.assertEqual(stream["xhttpSettings"]["mode"], "auto")
        self.assertEqual(stream["realitySettings"]["serverName"], "site.test")


if __name__ == "__main__":
    unittest.main()
