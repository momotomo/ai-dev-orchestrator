#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys

import run_until_stop


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "bridge の通常入口です。project path と max execution count を渡して起動します。"
            " 初回だけ最初の ChatGPT request 本文を入力し、その後は必要時だけ再実行します。"
        )
    )
    parser.add_argument(
        "--project-path",
        required=True,
        help="worker 対象 repo root",
    )
    parser.add_argument(
        "--max-execution-count",
        type=int,
        default=run_until_stop.DEFAULT_MAX_STEPS,
        help="1 回の run で最大何手まで進めるか",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    forwarded_argv = [
        "--project-path",
        args.project_path,
        "--max-execution-count",
        str(args.max_execution_count),
        "--entry-script",
        "scripts/start_bridge.py",
    ]
    return run_until_stop.run(forwarded_argv)


if __name__ == "__main__":
    sys.exit(main())
