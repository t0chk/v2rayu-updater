from __future__ import annotations

import tempfile
import unittest

from v2rayu_updater.apply_ops import apply_config_plan_to_plist
from v2rayu_updater.apply_ops import create_plist_backup
from v2rayu_updater.config_plan import ConfigPlan
from v2rayu_updater.config_plan import PlannedConfigEntry


class ApplyOpsTests(unittest.TestCase):
    def test_apply_config_plan_updates_and_rebuilds_server_list(self) -> None:
        plist_data = {
            "config.A": b"old-A",
            "config.B": b"old-B",
            "config.C": b"old-C",
            "v2rayServerList": ["config.B", "config.A", "config.C"],
            "v2rayCurrentServerName": "config.B",
        }
        plan = ConfigPlan(
            entries=[
                PlannedConfigEntry(
                    key="config.A",
                    action="update",
                    subscribe="subscribe.ONE",
                    url="vless://a",
                    remark="A",
                    name="config.A",
                    json_text="{}",
                    speed="",
                    is_valid=True,
                    scheme="vless",
                    blob=b"new-A",
                ),
                PlannedConfigEntry(
                    key="config.D",
                    action="create",
                    subscribe="subscribe.ONE",
                    url="vless://d",
                    remark="D",
                    name="config.D",
                    json_text="{}",
                    speed="",
                    is_valid=True,
                    scheme="vless",
                    blob=b"new-D",
                ),
            ],
            stale_config_keys=["config.B"],
            errors=[],
        )

        updated, summary = apply_config_plan_to_plist(plist_data, plan)

        self.assertEqual(updated["config.A"], b"new-A")
        self.assertEqual(updated["config.D"], b"new-D")
        self.assertNotIn("config.B", updated)
        self.assertEqual(updated["v2rayServerList"], ["config.A", "config.C", "config.D"])
        self.assertEqual(updated["v2rayCurrentServerName"], "config.A")

        self.assertEqual(summary.planned, 2)
        self.assertEqual(summary.created, 1)
        self.assertEqual(summary.updated, 1)
        self.assertEqual(summary.removed_stale, 1)
        self.assertEqual(summary.server_list_count, 3)

    def test_create_plist_backup_uses_single_rolling_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            from pathlib import Path

            root = Path(tmpdir)
            plist_path = root / "net.yanue.V2rayU.plist"
            backup_dir = root / "backups"

            plist_path.write_bytes(b"first")
            first_path = create_plist_backup(plist_path, backup_dir)
            self.assertEqual(first_path.name, "net.yanue.V2rayU.plist.bak")
            self.assertEqual(first_path.read_bytes(), b"first")

            plist_path.write_bytes(b"second")
            second_path = create_plist_backup(plist_path, backup_dir)
            self.assertEqual(second_path, first_path)
            self.assertEqual(second_path.read_bytes(), b"second")


if __name__ == "__main__":
    unittest.main()
