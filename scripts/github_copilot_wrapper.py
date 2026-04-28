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
from pathlib import Path


_DEFAULT_GH_COMMAND = ["gh", "copilot", "suggest", "--target=shell", "-"]

BRIDGE_SUMMARY_START = "===BRIDGE_SUMMARY==="
BRIDGE_SUMMARY_END = "===END_BRIDGE_SUMMARY==="

# Direct copilot CLI binary name (new-style, non-gh path).
COPILOT_CLI_BIN = "copilot"

REASONING_EFFORT_ALLOWED = frozenset({"low", "medium", "high"})

# Stub safety guard: identifiers used to detect the stub provider.
# These prevent the stub from being mistaken for a real AI execution.
_STUB_PROVIDER_MODULE = "github_copilot_provider_stub.py"
_STUB_OUTPUT_MARKERS = ("provider: github_copilot_provider_stub", "stub 応答")


def _sanitize_prompt_text(prompt: str) -> str:
    """Strip embedded NUL bytes from free-form prompt text and warn.

    NUL bytes (\\x00) in subprocess argv elements cause
    ``ValueError: embedded null byte`` on POSIX systems.  For free-form prompt
    text (read from stdin or a prompt file) they are always spurious binary
    encoding artefacts and can be safely removed.

    Logs a WARNING to stderr if any bytes are stripped.
    """
    if "\x00" not in prompt:
        return prompt
    count = prompt.count("\x00")
    sanitized = prompt.replace("\x00", "")
    print(
        f"[github_copilot_wrapper] WARNING: {count} NUL byte(s) stripped from prompt "
        "(binary encoding artefact). Continuing with sanitized prompt.",
        file=sys.stderr,
    )
    return sanitized


def _assert_no_null_in_structural_input(value: str, label: str) -> None:
    """Raise a descriptive ValueError if a structural subprocess input contains NUL.

    Unlike free-form prompt text, structural inputs (command paths, model names)
    cannot be safely stripped — a NUL byte there indicates a configuration error
    and the subprocess launch must be aborted.
    """
    if "\x00" in value:
        raise ValueError(
            f"[github_copilot_wrapper] FATAL: NUL byte in structural input '{label}'. "
            "Cannot continue. "
            "Check project_config.json, environment variables, and argv."
        )


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
    parser.add_argument(
        "--report-file",
        default="",
        metavar="PATH",
        help=(
            "bridge report の書き込み先パス。"
            "指定時は provider の stdout を bridge 形式 report に変換してここに書き出します。"
            "未指定時は従来どおり stdout/stderr を透過させます。"
        ),
    )
    parser.add_argument(
        "--autopilot",
        action="store_true",
        default=False,
        help=(
            "--autopilot を copilot CLI に付与する。"
            "--report-file + no --exec 経路 (copilot CLI 直接呼び出し) でのみ有効。"
        ),
    )
    parser.add_argument(
        "--reasoning-effort",
        default="",
        metavar="EFFORT",
        help=(
            "reasoning effort (low/medium/high)。"
            "--report-file + no --exec 経路 (copilot CLI 直接呼び出し) でのみ有効。"
            "空または未設定なら付与しない。"
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


def build_bridge_report(provider_output: str, *, model: str = "", exec_cmd: str = "") -> str:
    """Format provider output as a bridge-compatible markdown report.

    Produces a minimal report containing the BRIDGE_SUMMARY block required by
    compact_last_report_text() and the raw provider output for downstream review.
    """
    provider = exec_cmd.strip() if exec_cmd.strip() else "gh copilot suggest"
    model_note = f" (model: {model})" if model.strip() else ""
    lines = [
        BRIDGE_SUMMARY_START,
        f"- summary: GitHub Copilot 実行完了{model_note}",
        "- issue_context: なし",
        "- changed: なし",
        "- verify: 未実施",
        "- next_state: codex_done",
        "- result: completed",
        "- live_ready: confirmed",
        "- risks: なし",
        BRIDGE_SUMMARY_END,
        "",
        "# GitHub Copilot 実行結果",
        "",
        "## Provider Output",
        "",
        provider_output.strip(),
    ]
    return "\n".join(lines) + "\n"


def build_copilot_cli_command(prompt_text: str, args: argparse.Namespace) -> list[str]:
    """Build the direct copilot CLI command used in --report-file mode without --exec.

    Produces: ``copilot [--model M] [--reasoning-effort R] [--autopilot] -p <prompt> -s --allow-all-tools``
    """
    cmd = [COPILOT_CLI_BIN]
    model = args.model.strip()
    if model:
        cmd.extend(["--model", model])
    reasoning_effort = getattr(args, "reasoning_effort", "").strip()
    if reasoning_effort:
        cmd.extend(["--reasoning-effort", reasoning_effort])
    if getattr(args, "autopilot", False):
        cmd.append("--autopilot")
    cmd.extend(["-p", prompt_text])
    cmd.extend(["-s", "--allow-all-tools"])
    return cmd


def run(argv: list[str] | None = None) -> int:
    """Main entry point. Reads prompt from stdin, runs provider command, returns exit code."""
    args = parse_args(argv)
    model = args.model.strip()
    exec_cmd = args.exec.strip()
    report_file = args.report_file.strip()

    command = build_command(args)

    # Set COPILOT_MODEL so callers / log consumers can observe the requested model,
    # even when the provider does not accept --model directly.
    env = dict(os.environ)
    if model:
        env["COPILOT_MODEL"] = model
        if not exec_cmd and not report_file:
            # gh copilot suggest does not support --model; emit a notice to stderr.
            # (In --report-file mode without --exec, copilot CLI is used directly and
            # does accept --model, so the notice is suppressed there.)
            print(
                f"[github_copilot_wrapper] NOTE: --model '{model}' requested but "
                "gh copilot suggest does not accept --model. "
                "Set COPILOT_MODEL is available for logging only. "
                "Use --exec to forward model to a model-aware provider.",
                file=sys.stderr,
            )

    prompt = sys.stdin.read()
    prompt = _sanitize_prompt_text(prompt)

    # Fail-fast: structural subprocess inputs (command path, model) must not contain NUL.
    try:
        _assert_no_null_in_structural_input(exec_cmd, "--exec")
        _assert_no_null_in_structural_input(model, "--model")
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if report_file:
        if exec_cmd:
            # --exec path: run custom provider, capture stdout, synthesize bridge report.
            # Stub guard (pre-execution): reject stub provider in --report-file mode.
            # Checked before subprocess to avoid any side-effects from running the stub.
            if _STUB_PROVIDER_MODULE in exec_cmd:
                print(
                    f"[github_copilot_wrapper] STUB DETECTED: --exec に stub provider "
                    f"({_STUB_PROVIDER_MODULE}) が指定されています。\n"
                    "これは stub であり、実 AI 実行ではありません。"
                    "bridge report を生成せずに終了します。\n"
                    "実 provider に差し替えて再実行してください。",
                    file=sys.stderr,
                )
                return 1

            # stdout and stderr are echoed to our own streams so launch logs capture them.
            try:
                result = subprocess.run(
                    command,
                    input=prompt,
                    capture_output=True,
                    text=True,
                    env=env,
                )
            except FileNotFoundError as exc:
                print(f"[github_copilot_wrapper] ERROR: command not found: {exc}", file=sys.stderr)
                return 127
            except ValueError as exc:
                print(f"[github_copilot_wrapper] FATAL: subprocess input error: {exc}", file=sys.stderr)
                return 1
            if result.stdout:
                sys.stdout.write(result.stdout)
            if result.stderr:
                sys.stderr.write(result.stderr)
            if result.returncode == 0:
                provider_output = result.stdout.strip()
                if not provider_output:
                    print(
                        "[github_copilot_wrapper] ERROR: provider exited 0 but produced no output.",
                        file=sys.stderr,
                    )
                    return 1
                # Stub guard (post-execution): detect stub output even when --exec path
                # did not contain the stub module name (e.g. symlink or renamed binary).
                if any(marker in provider_output for marker in _STUB_OUTPUT_MARKERS):
                    print(
                        "[github_copilot_wrapper] STUB DETECTED: provider の出力に stub 識別子が含まれています。\n"
                        "これは stub であり、実 AI 実行ではありません。"
                        "bridge report を生成せずに終了します。\n"
                        "実 provider に差し替えて再実行してください。",
                        file=sys.stderr,
                    )
                    return 1
                report_text = build_bridge_report(provider_output, model=model, exec_cmd=exec_cmd)
                out_path = Path(report_file)
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(report_text, encoding="utf-8")
            return result.returncode

        else:
            # no --exec path: call copilot CLI directly with new syntax.
            # Prompt is embedded via -p <prompt>; stdin is not used.
            copilot_cmd = build_copilot_cli_command(prompt, args)
            try:
                result = subprocess.run(
                    copilot_cmd,
                    input=None,
                    capture_output=True,
                    text=True,
                    env=env,
                )
            except FileNotFoundError as exc:
                print(f"[github_copilot_wrapper] ERROR: command not found: {exc}", file=sys.stderr)
                return 127
            except ValueError as exc:
                print(f"[github_copilot_wrapper] FATAL: subprocess input error: {exc}", file=sys.stderr)
                return 1
            if result.stdout:
                sys.stdout.write(result.stdout)
            if result.stderr:
                sys.stderr.write(result.stderr)
            if result.returncode == 0:
                provider_output = result.stdout.strip()
                if not provider_output:
                    print(
                        "[github_copilot_wrapper] ERROR: copilot exited 0 but produced no output.",
                        file=sys.stderr,
                    )
                    return 1
                report_text = build_bridge_report(
                    provider_output, model=model, exec_cmd=COPILOT_CLI_BIN
                )
                out_path = Path(report_file)
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(report_text, encoding="utf-8")
            return result.returncode

    # Transparent passthrough mode (no --report-file): existing behavior.
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
    except ValueError as exc:
        print(f"[github_copilot_wrapper] FATAL: subprocess input error: {exc}", file=sys.stderr)
        return 1

    return result.returncode


if __name__ == "__main__":
    sys.exit(run())
