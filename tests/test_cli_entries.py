from __future__ import annotations

import unittest

from v2rayu_updater.cli import build_entries_dump
from v2rayu_updater.cli import build_parser
from v2rayu_updater.plist_store import ConfigRecord


class CliEntriesTests(unittest.TestCase):
    def test_parser_accepts_entries_flag(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["-e"])
        self.assertTrue(args.entries)
        self.assertFalse(args.dry_run)
        self.assertFalse(args.apply)
        self.assertFalse(args.list_subscriptions)

    def test_build_entries_dump_parses_json_and_reports_invalid_json(self) -> None:
        valid = ConfigRecord(
            key="config.VALID",
            name="config.VALID",
            url="vless://valid",
            remark="valid",
            subscribe="subscribe.ONE",
            json='{"a":1}',
            speed="",
            is_valid=True,
            decode_method="plistlib",
            error=None,
        )
        invalid = ConfigRecord(
            key="config.INVALID",
            name="config.INVALID",
            url="vless://invalid",
            remark="invalid",
            subscribe="subscribe.ONE",
            json="{bad json}",
            speed="",
            is_valid=True,
            decode_method="plistlib",
            error=None,
        )

        dumped = build_entries_dump([valid, invalid])
        self.assertEqual(len(dumped), 2)

        self.assertEqual(dumped[0]["key"], "config.VALID")
        self.assertEqual(dumped[0]["json_raw"], '{"a":1}')
        self.assertEqual(dumped[0]["json"], {"a": 1})
        self.assertIsNone(dumped[0]["json_parse_error"])

        self.assertEqual(dumped[1]["key"], "config.INVALID")
        self.assertEqual(dumped[1]["json_raw"], "{bad json}")
        self.assertIsNone(dumped[1]["json"])
        self.assertIsInstance(dumped[1]["json_parse_error"], str)
        self.assertTrue(dumped[1]["json_parse_error"])


if __name__ == "__main__":
    unittest.main()
