from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
from typing import Sequence

from .apply_ops import apply_config_plan_to_plist
from .apply_ops import create_plist_backup
from .apply_ops import is_binary_plist_file
from .apply_ops import is_v2rayu_running
from .apply_ops import write_plist_atomic
from .config_plan import build_config_plan
from .plist_store import load_plist
from .plist_store import parse_configs
from .plist_store import parse_subscriptions
from .plist_store import resolve_plist_path
from .subscriptions import build_request_headers
from .subscriptions import fetch_subscription_payloads


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="v2rayu-updater",
        description="External subscription updater for V2RayU plist storage.",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "-l",
        "--list-subscriptions",
        action="store_true",
        help="List subscription entries found in V2RayU plist.",
    )
    mode.add_argument(
        "-d",
        "--dry-run",
        action="store_true",
        help="Preview updates without writing plist.",
    )
    mode.add_argument(
        "-a",
        "--apply",
        action="store_true",
        help="Apply updates and write plist.",
    )
    mode.add_argument(
        "-e",
        "--entries",
        action="store_true",
        help="Dump config.* server entries from plist as JSON.",
    )
    parser.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="Allow --apply even when V2RayU is running.",
    )
    parser.add_argument(
        "--plist-path",
        default="~/Library/Preferences/net.yanue.V2rayU.plist",
        help="Path to V2RayU plist file.",
    )
    parser.add_argument(
        "--backup-dir",
        default="./backups",
        help="Directory for plist backups when --apply is used.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=20.0,
        help="HTTP timeout in seconds for subscription download.",
    )
    parser.add_argument(
        "--header",
        action="append",
        default=[],
        help="Custom request header in format 'Name: Value'. Can be used multiple times.",
    )
    parser.add_argument(
        "--x-hwid",
        default=None,
        help="Convenience option to set x-hwid request header.",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Disable TLS certificate verification for subscription HTTP requests.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    plist_path = resolve_plist_path(args.plist_path)

    try:
        plist_data = load_plist(plist_path)
    except Exception as exc:  # noqa: BLE001
        parser.error(str(exc))

    subscriptions = parse_subscriptions(plist_data)
    configs = parse_configs(plist_data)

    if args.list_subscriptions:
        print_subscription_report(plist_path, subscriptions)
        return 0

    if args.entries:
        print_entries_json(configs)
        return 0

    if args.dry_run or args.apply:
        try:
            request_headers = build_request_headers(args.header, args.x_hwid)
        except ValueError as exc:
            parser.error(str(exc))
        fetch_results = fetch_subscription_payloads(
            subscriptions=subscriptions,
            timeout=args.timeout,
            headers=request_headers,
            verify_tls=not args.insecure,
        )
        config_plan = build_config_plan(
            existing_configs=configs,
            fetch_results=fetch_results,
        )
        if args.dry_run:
            print_dry_run_report(
                plist_path=plist_path,
                subscriptions=subscriptions,
                configs=configs,
                fetch_results=fetch_results,
                config_plan=config_plan,
            )
            return 0

        return apply_changes(
            parser=parser,
            plist_path=plist_path,
            plist_data=plist_data,
            fetch_results=fetch_results,
            config_plan=config_plan,
            backup_dir=Path(args.backup_dir).expanduser().resolve(),
            force=args.force,
        )

    return 0


def apply_changes(
    parser: argparse.ArgumentParser,
    plist_path: Path,
    plist_data: dict,
    fetch_results: list,
    config_plan,
    backup_dir: Path,
    force: bool,
) -> int:
    fetch_errors = [item for item in fetch_results if item.error]
    if fetch_errors:
        parser.error(
            f"Cannot apply: {len(fetch_errors)} subscription fetch error(s). Run -d and resolve issues first."
        )
    if config_plan.errors:
        parser.error(
            f"Cannot apply: {len(config_plan.errors)} config planning error(s). Run -d and resolve issues first."
        )

    running = is_v2rayu_running()
    if running and not force:
        parser.error("V2RayU is running. Close it or use --force to apply anyway.")
    if running and force:
        print("warning: V2RayU is running, proceeding because --force is set.")

    if not config_plan.entries and not config_plan.stale_config_keys:
        print("no changes to apply")
        return 0

    updated_plist, summary = apply_config_plan_to_plist(plist_data, config_plan)
    backup_path = create_plist_backup(plist_path, backup_dir)
    binary_format = is_binary_plist_file(plist_path)
    write_plist_atomic(plist_path, updated_plist, binary=binary_format)

    print(f"plist updated: {plist_path}")
    print(f"backup created: {backup_path}")
    print(
        "apply summary: "
        f"planned={summary.planned} create={summary.created} update={summary.updated} "
        f"removed_stale={summary.removed_stale} server_list={summary.server_list_count}"
    )
    print(f"current server: {summary.current_server}")
    return 0


def print_subscription_report(plist_path: Path, subscriptions: list) -> None:
    print(f"plist: {plist_path}")
    print(f"subscriptions found: {len(subscriptions)}")
    if not subscriptions:
        return

    failures = [item for item in subscriptions if item.error]
    print(f"decoded: {len(subscriptions) - len(failures)} | failed: {len(failures)}")
    for index, item in enumerate(subscriptions, start=1):
        print(f"{index}. {item.key}")
        print(f"   url: {_shorten(item.url)}")
        print(f"   remark: {item.remark or '-'}")
        print(f"   valid: {item.is_valid}")
        print(f"   decode: {item.decode_method}")
        if item.error:
            print(f"   error: {item.error}")


def print_entries_json(configs: list) -> None:
    payload = build_entries_dump(configs)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def build_entries_dump(configs: list) -> list[dict]:
    entries: list[dict] = []
    for item in configs:
        parsed_json = None
        json_parse_error = None
        if item.json is not None:
            try:
                loaded = json.loads(item.json)
            except Exception as exc:  # noqa: BLE001
                json_parse_error = str(exc)
            else:
                parsed_json = loaded

        entries.append(
            {
                "key": item.key,
                "name": item.name,
                "remark": item.remark,
                "subscribe": item.subscribe,
                "url": item.url,
                "is_valid": item.is_valid,
                "speed": item.speed,
                "decode_method": item.decode_method,
                "error": item.error,
                "json_raw": item.json,
                "json": parsed_json,
                "json_parse_error": json_parse_error,
            }
        )
    return entries


def print_dry_run_report(
    plist_path: Path, subscriptions: list, configs: list, fetch_results: list, config_plan
) -> None:
    print(f"plist: {plist_path}")
    print("mode: dry-run (no changes written)")

    sub_failures = [item for item in subscriptions if item.error]
    cfg_failures = [item for item in configs if item.error]

    print(
        "subscriptions: "
        f"total={len(subscriptions)} decoded={len(subscriptions) - len(sub_failures)} "
        f"failed={len(sub_failures)}"
    )
    print(
        "configs: "
        f"total={len(configs)} decoded={len(configs) - len(cfg_failures)} "
        f"failed={len(cfg_failures)}"
    )

    if fetch_results:
        ok_fetches = [item for item in fetch_results if not item.error]
        failed_fetches = [item for item in fetch_results if item.error]
        total_nodes = sum(len(item.parsed.nodes) for item in ok_fetches if item.parsed)
        print(
            "subscription fetch: "
            f"total={len(fetch_results)} ok={len(ok_fetches)} failed={len(failed_fetches)} "
            f"nodes={total_nodes}"
        )

    if config_plan:
        create_count = sum(1 for entry in config_plan.entries if entry.action == "create")
        update_count = sum(1 for entry in config_plan.entries if entry.action == "update")
        xhttp_patch_count = sum(
            1 for entry in config_plan.entries if any(note.startswith("xhttp_settings_patched") for note in entry.notes)
        )
        print(
            "config synthesis: "
            f"planned={len(config_plan.entries)} create={create_count} update={update_count} "
            f"stale={len(config_plan.stale_config_keys)} xhttp_patched={xhttp_patch_count} "
            f"errors={len(config_plan.errors)}"
        )

    sub_lookup = {
        item.key: {
            "remark": item.remark or "-",
            "url": item.url or "-",
        }
        for item in subscriptions
    }

    if subscriptions:
        print("\nsubscriptions:")
        for item in subscriptions:
            print(f"- {item.key}")
            print(f"  -- remark={item.remark or '-'}")
            print(f"  -- url={_shorten(item.url)}")
            if item.error:
                print(f"  -- error={item.error}")
            print("")

    if fetch_results:
        print("\nsubscription payloads:")
        for item in fetch_results:
            sub_info = sub_lookup.get(item.key, {})
            remark = sub_info.get("remark", item.remark or "-")
            url = sub_info.get("url", item.url or "-")
            print(f"- remark={remark}")
            print(f"  -- url={_shorten(url)}")
            print(f"  -- {item.key}")
            if item.error:
                print(
                    f"  -- status={item.status_code if item.status_code is not None else '-'} | "
                    f"elapsed_ms={item.elapsed_ms if item.elapsed_ms is not None else '-'} | error={item.error}"
                )
                print("")
                continue
            parsed = item.parsed
            if not parsed:
                print(f"  -- status={item.status_code} | parsed=-")
                print("")
                continue
            scheme_counts = Counter(node.scheme for node in parsed.nodes)
            scheme_stat = (
                ", ".join(f"{scheme}:{count}" for scheme, count in sorted(scheme_counts.items()))
                if scheme_counts
                else "-"
            )
            print(
                f"  -- status={item.status_code} | format={parsed.format} | "
                f"nodes={len(parsed.nodes)} | schemes={scheme_stat}"
            )
            for warning in parsed.warnings:
                print(f"  -- warning={warning}")
            print("")

    if config_plan and config_plan.entries:
        by_sub = Counter(entry.subscribe for entry in config_plan.entries)
        print("\nplanned config entries by subscription:")
        ordered_sub_keys = [item.key for item in subscriptions if item.key in by_sub]
        ordered_sub_key_set = set(ordered_sub_keys)
        extra_sub_keys = sorted(key for key in by_sub if key not in ordered_sub_key_set)
        for sub_key in ordered_sub_keys + extra_sub_keys:
            remark = sub_lookup.get(sub_key, {}).get("remark", "-")
            print(f"- remark={remark}")
            print(f"  -- {sub_key}: {by_sub[sub_key]}")
            print("")

    if configs:
        linked = Counter(item.subscribe for item in configs if item.subscribe)
        print("\nconfigs per subscription:")
        ordered_linked_keys = [item.key for item in subscriptions if item.key in linked]
        ordered_linked_key_set = set(ordered_linked_keys)
        extra_linked_keys = sorted(key for key in linked if key not in ordered_linked_key_set)
        for sub_key in ordered_linked_keys + extra_linked_keys:
            remark = sub_lookup.get(sub_key, {}).get("remark", "-")
            print(f"- remark={remark}")
            print(f"  -- {sub_key}: {linked[sub_key]}")
            print("")

        orphan = [
            item.key
            for item in configs
            if item.subscribe and item.subscribe not in {s.key for s in subscriptions}
        ]
        if orphan:
            print("\norphan config entries (missing subscribe.*):")
            for key in orphan:
                print(f"- {key}")

    if config_plan and config_plan.stale_config_keys:
        print("\nstale config entries (present in plist, absent in fetched subscriptions):")
        for key in config_plan.stale_config_keys:
            print(f"- {key}")

    if sub_failures or cfg_failures:
        print("\ndecode errors:")
        for item in sub_failures:
            print(f"- {item.key}: {item.error}")
        for item in cfg_failures:
            print(f"- {item.key}: {item.error}")

    if config_plan and config_plan.errors:
        print("\nconfig planning errors:")
        for error in config_plan.errors:
            print(f"- {error}")


def _shorten(value: str | None, limit: int = 120) -> str:
    if value is None:
        return "-"
    if len(value) <= limit:
        return value
    return f"{value[:limit]}..."


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        raise SystemExit(0)
