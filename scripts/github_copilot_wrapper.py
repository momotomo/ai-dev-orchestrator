#!/usr/bin/env python3
"""github_copilot_wrapper.py — GitHub Copilot custom wrapper with model selection.

launch_github_copilot.py から ``github_copilot_bin`` に指定して使用します。

使い方:
    github_copilot_wrapper.py [--model <model>] [--exec <command>]

stdin に prompt を渡してください。

Model サポートについて:
    ``gh copilot suggest`` には現時点で安定した --model フラグがありません。
    デフォルト動作 (--exec 未指定) では ``gh copilot suggest --target=shell -`` を
    呼び出し、--model 値は環境変数 COPILOT_MODEL にセットされますが
    gh CLI には転送されません。

    model-aware な provider を使いたい場合は --exec に実行バイナリを指定してください。
    その場合、wrapper は ``<exec> --model <model>`` として呼び出し、stdin を渡します。
    provider は stdout/stderr と終了コードを素直に返してください。

project_config.json の設定例:
    "execution_agent": "github_copilot",
    "agent_model": "sonnet-4.6",
    "github_copilot_bin": "/path/to/scripts/github_copilot_wrapper.py"

    → launch_github_copilot.py が以下を実行します:
      github_copilot_wrapper.py --model sonnet-4.6

    --exec を指定した場合:
    "github_copilot_bin": "/path/to/scripts/github_copilot_wrapper.py --exec /usr/local/bin/my-copilot-provider"
    ※ --exec は wrapper の argv に含めるか、別途ラッパー設定ファイルで管理してください。

gh 直呼びとの差分:
    - gh 直呼び (github_copilot_bin = "gh"):
        model は argv に含まれない (gh CLI は --model を受け付けないため)
    - this wrapper (github_copilot_bin = "/path/to/github_copilot_wrapper.py"):
        --model <value> を受け取り、環境変数 COPILOT_MODEL にセット
        --exec 指定時はカスタム provider にも --model を転送
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys


_DEFAULT_GH_COMMAND = ["gh", "copilot", "suggest", "--target=shell", "-"]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="GitHub Copilot custom wrapper — model 指定対応 shim.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--model",
        default="",
        help=(
            "使用する model 名 (例: sonnet-4.6)。"
            "gh copilot suggest はこの値を受け付けないため、"
            "--exec 未指定時は環境変数 COPILOT_MODEL にセットのみ行います。"
        ),
    )
    parser.add_argument(
        "--exec",
        default="",
        metavar="COMMAND",
        help=(
            "model-aware な代替 provider のパス。"
            "指定した場合、このコマンドに --model を転送します。"
            "未指定時は gh copilot suggest にフォールバックします。"
        ),
    )
    return parser.parse_args(argv)


def build_command(args: argparse.Namespace) -> list[str]:
    """Build the subprocess command to run.

    --exec 指定時: [args.exec, "--model", args.model] (model が空なら --model は付かない)
    未指定時: gh copilot suggest --target=shell - (model は転送不可)
    """
    model = args.model.strip()
    exec_cmd = args.exec.strip()

    if exec_cmd:
        cmd = [exec_cmd]
        if model:
            cmd.extend(["--model", model])
        return cmd

    # Default: gh copilot suggest fallback
    # Note: model is NOT forwarded because gh copilot suggest has no --model flag.
    return list(_DEFAULT_GH_COMMAND)


def run(argv: list[str] | None = None) -> int:
    """Main entry point. Reads prompt from stdin, runs provider command, returns exit code."""
    args = parse_args(argv)
    model = args.model.strip()
    exec_cmd = args.exec.strip()

    command = build_command(args)

    # Set COPILOT_MODEL so callers / log consumers can observe the requested model,
    # even when the provider does not accept --model directly.
    env = dict(os.environ)
    if model:
        env["COPILOT_MODEL"] = model
        if not exec_cmd:
            # gh copilot suggest does not support --model; emit a notice to stderr.
            print(
                f"[github_copilot_wrapper] NOTE: --model '{model}' requested but "
                "gh copilot suggest does not accept --model. "
                "Set COPILOT_MODEL is available for logging only. "
                "Use --exec to forward model to a model-aware provider.",
                file=sys.stderr,
            )

    prompt = sys.stdin.read()

    try:
        result = subprocess.run(
            command,
            input=prompt,
            capture_output=False,
            text=True,
            env=env,
        )
    except FileNotFoundError as exc:
        print(f"[github_copilot_wrapper] ERROR: command not found: {exc}", file=sys.stderr)
        return 127

    return result.returncode


if __name__ == "__main__":
    sys.exit(run())
