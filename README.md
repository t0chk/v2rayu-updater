# v2rayU-updater

External updater for V2RayU subscriptions on macOS.

## What it does

- Read and update `~/Library/Preferences/net.yanue.V2rayU.plist`
- Decode/encode `subscribe.*` and `config.*` NSKeyedArchive blobs
- Fetch subscriptions over HTTP(S) with optional custom headers (`x-hwid`, `Authorization`, etc.)
- Parse plain/base64 URI lists (`vmess`, `vless`, `trojan`, `ss`)
- Build V2RayU `config.*` entries from subscription URIs (replace strategy)
- Write changes safely with backup and atomic plist update

## Local setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

## CLI

```bash
v2rayu-updater --help
```

### Main commands

```bash
v2rayu-updater -l                 # --list-subscriptions
v2rayu-updater -d                 # --dry-run
v2rayu-updater -a                 # --apply
v2rayu-updater -a -f              # --apply --force
```

### Dry-run options

```bash
v2rayu-updater -d --x-hwid YOUR_HWID
v2rayu-updater -d --header "Authorization: Bearer TOKEN"
v2rayu-updater -d --timeout 30
v2rayu-updater -d --insecure
```

### Apply changes

```bash
# preferred: V2RayU is closed
v2rayu-updater -a

# allow write while V2RayU is running (at your own risk)
v2rayu-updater -a -f
```

## Backup and rollback

- On each `-a/--apply`, one rolling backup is written to `backups/net.yanue.V2rayU.plist.bak`.
- Backup file is replaced on each next apply (no backup accumulation).

Rollback:

```bash
cp backups/net.yanue.V2rayU.plist.bak ~/Library/Preferences/net.yanue.V2rayU.plist
```

## Safety behavior

- `--apply` is blocked if V2RayU is running.
- Use `-f/--force` to bypass this check.
- If any subscription fetch fails, apply is aborted.
- If planning errors exist, apply is aborted.

## Development checks

```bash
PYTHONPATH=src .venv/bin/python -m unittest -q
PYTHONPATH=src .venv/bin/python -m py_compile src/v2rayu_updater/*.py
```
