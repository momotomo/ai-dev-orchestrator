#!/usr/bin/env python3
"""github_copilot_provider_stub.py — deterministic stub provider for wrapper疎通確認.

``github_copilot_wrapper.py`` の --exec に指定して使います。
外部 API / CLI への依存は一切ありません。

目的:
    wrapper → provider → stdout → wrapper report 生成
    この経路を壊れにくく再現可能な形で疎通するための stub です。
    実 AI 応答は返しません。入力の要約だけを stdout に出力します。

使い方:
    echo "prompt text" | github_copilot_provider_stub.py --model sonnet-4.6

    wrapper 経由 (project_config.json):
        "github_copilot_bin": "/path/to/github_copilot_wrapper.py --exec /path/to/github_copilot_provider_stub.py",
        "agent_model": "sonnet-4.6"
"""
from __future__ import annotations

import argparse
import sys

PROVIDER_NAME = "github_copilot_provider_stub"
FIRST_LINE_MAX = 80


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Deterministic stub provider for github_copilot_wrapper.",
    )
    parser.add_argument(
        "--model",
        default="",
        help="Model name forwarded from github_copilot_wrapper (e.g. sonnet-4.6).",
    )
    return parser.parse_args(argv)


def run(argv: list[str] | None = None) -> int:
    """Read prompt from stdin, write deterministic summary to stdout, return 0."""
    args = parse_args(argv)
    model = args.model.strip() or "(none)"

    prompt = sys.stdin.read()
    char_count = len(prompt)

    first_line = prompt.strip().splitlines()[0] if prompt.strip() else ""
    if len(first_line) > FIRST_LINE_MAX:
        first_line = first_line[:FIRST_LINE_MAX] + "..."

    output_lines = [
        f"provider: {PROVIDER_NAME}",
        f"model: {model}",
        f"input_chars: {char_count}",
        f"first_line: {first_line}",
        "",
        "stub 応答: provider 疎通確認用の出力です。実 AI 応答は含まれません。",
        "実装を進める際はこの stub を実 provider に差し替えてください。",
    ]
    print("\n".join(output_lines))
    return 0


if __name__ == "__main__":
    sys.exit(run())
