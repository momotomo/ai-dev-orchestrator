#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_NAME = "runtime_snapshot_manifest.json"
SNAPSHOT_TARGETS = [
    "bridge/state.json",
    "bridge/project_config.json",
    "bridge/inbox",
    "bridge/outbox",
    "bridge/history",
    "logs",
    "bridge/STOP",
    "reset_backup",
]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="bridge runtime 実ファイルの snapshot helper")
    subparsers = parser.add_subparsers(dest="command", required=True)

    backup_parser = subparsers.add_parser("backup", help="runtime 実ファイルを snapshot する")
    backup_parser.add_argument("--dest", required=True, help="snapshot 保存先ディレクトリ")

    restore_parser = subparsers.add_parser("restore", help="snapshot から runtime 実ファイルを復元する")
    restore_parser.add_argument("--src", required=True, help="backup 時に作った snapshot ディレクトリ")
    return parser.parse_args(argv)


def resolve_root(raw_path: str) -> Path:
    return Path(raw_path).expanduser().resolve()


def remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def copy_path(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, destination)
    else:
        shutil.copy2(source, destination)


def backup(snapshot_root: Path) -> int:
    if snapshot_root.exists():
        print(f"snapshot 先が既に存在します: {snapshot_root}", file=sys.stderr)
        return 1

    snapshot_root.mkdir(parents=True, exist_ok=False)
    manifest: dict[str, object] = {
        "repo_root": str(REPO_ROOT),
        "targets": [],
    }

    for relative in SNAPSHOT_TARGETS:
        source = REPO_ROOT / relative
        target_info = {
            "path": relative,
            "exists": source.exists(),
            "type": "dir" if source.is_dir() else "file" if source.is_file() else "missing",
        }
        targets = manifest.setdefault("targets", [])
        assert isinstance(targets, list)
        targets.append(target_info)
        if source.exists():
            copy_path(source, snapshot_root / relative)

    (snapshot_root / MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"runtime snapshot を作成しました: {snapshot_root}")
    return 0


def restore(snapshot_root: Path) -> int:
    manifest_path = snapshot_root / MANIFEST_NAME
    if not manifest_path.exists():
        print(f"manifest が見つかりません: {manifest_path}", file=sys.stderr)
        return 1

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    targets = manifest.get("targets", [])
    if not isinstance(targets, list):
        print("manifest の targets が不正です。", file=sys.stderr)
        return 1

    for entry in targets:
        if not isinstance(entry, dict):
            continue
        relative = str(entry.get("path", "")).strip()
        if not relative:
            continue
        destination = REPO_ROOT / relative
        snapshot_source = snapshot_root / relative
        if destination.exists():
            remove_path(destination)
        if snapshot_source.exists():
            copy_path(snapshot_source, destination)

    print(f"runtime snapshot を復元しました: {snapshot_root}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "backup":
        return backup(resolve_root(args.dest))
    if args.command == "restore":
        return restore(resolve_root(args.src))
    raise AssertionError(f"unknown command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
