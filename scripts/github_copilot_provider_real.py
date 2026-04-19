#!/usr/bin/env python3
"""github_copilot_provider_real.py — GitHub Models API provider for github_copilot_wrapper.py

Uses GitHub Models API (models.inference.ai.azure.com) with the user's gh auth token.
No external Python dependencies — uses only stdlib (urllib, subprocess, json).

契約 (github_copilot_wrapper.py と同じ):
  stdin:  prompt 全文
  argv:   --model <model>
  stdout: 実 AI 応答本文 (成功時)
  exit 0: 成功
  exit non-zero: 失敗 (理由を stderr に出力)

モデルマッピング:
  GitHub Models は OpenAI 互換エンドポイントを提供します。
  orchestrator の agent_model 設定値 (例: sonnet-4.6) を GitHub Models の
  モデル名にマッピングします。マッピングにない場合は DEFAULT_MODEL にフォールバックします。

認証:
  gh CLI の 'gh auth token' で取得したトークンを Bearer として使用します。
  事前に 'gh auth login' を完了させてください。

使い方:
  echo "prompt text" | github_copilot_provider_real.py --model gpt-4o-mini

  wrapper 経由 (project_config.json):
    "github_copilot_bin": "/path/to/github_copilot_wrapper.py --exec /path/to/github_copilot_provider_real.py",
    "agent_model": "gpt-4o-mini"
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request

GITHUB_MODELS_API_URL = "https://models.inference.ai.azure.com/chat/completions"
DEFAULT_MODEL = "gpt-4o-mini"

# Map from orchestrator model names to GitHub Models API model names.
# Update this mapping when a new model becomes available in GitHub Models.
# To see available models: GET https://models.inference.ai.azure.com/models
MODEL_MAP: dict[str, str] = {
    # Claude / Sonnet variants → gpt-4o-mini (GitHub Models does not offer Claude as of 2026-04)
    "sonnet-4.6": "gpt-4o-mini",
    "sonnet-4-5": "gpt-4o-mini",
    "claude-sonnet-4-6": "gpt-4o-mini",
    "claude-sonnet-4-5": "gpt-4o-mini",
    # OpenAI variants available in GitHub Models
    "gpt-4o": "gpt-4o",
    "gpt-4o-mini": "gpt-4o-mini",
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="GitHub Models API provider for github_copilot_wrapper.",
    )
    parser.add_argument(
        "--model",
        default="",
        help="Model name forwarded from github_copilot_wrapper (e.g. sonnet-4.6, gpt-4o-mini).",
    )
    return parser.parse_args(argv)


def resolve_model(requested: str) -> str:
    """Resolve the orchestrator model name to a GitHub Models API model name.

    If the requested model is in MODEL_MAP, return the mapped name.
    If the requested model is unknown but non-empty, try using it as-is
    (the API will return a clear error if the model does not exist).
    If the requested model is empty, return DEFAULT_MODEL.
    """
    requested = requested.strip()
    if requested in MODEL_MAP:
        return MODEL_MAP[requested]
    if requested:
        # Unknown model name — try using as-is; API will fail with a clear error.
        return requested
    return DEFAULT_MODEL


def get_gh_token() -> str:
    """Return the gh auth token or raise RuntimeError on failure."""
    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        raise RuntimeError(
            "gh CLI が見つかりません。GitHub CLI (gh) をインストールして 'gh auth login' を実行してください。"
        )
    if result.returncode != 0:
        raise RuntimeError(
            f"gh auth token が失敗しました (exit {result.returncode}): {result.stderr.strip()}"
        )
    token = result.stdout.strip()
    if not token:
        raise RuntimeError(
            "gh auth token が空のトークンを返しました。'gh auth login' を実行してください。"
        )
    return token


def call_github_models_api(prompt: str, model: str, token: str) -> str:
    """Call GitHub Models completions API and return the response text.

    Raises RuntimeError on HTTP errors, network errors, or unexpected response format.
    """
    payload = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        GITHUB_MODELS_API_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"GitHub Models API HTTP エラー {exc.code}: {error_body}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"GitHub Models API 接続エラー: {exc.reason}"
        ) from exc

    data = json.loads(body)
    choices = data.get("choices")
    if not choices:
        raise RuntimeError(
            f"GitHub Models API が choices を返しませんでした: {body[:200]}"
        )
    content = choices[0].get("message", {}).get("content", "")
    if not content:
        raise RuntimeError(
            f"GitHub Models API が空の content を返しました: {body[:200]}"
        )
    return content


def run(argv: list[str] | None = None) -> int:
    """Main entry point. Reads prompt from stdin, calls GitHub Models API, prints response."""
    args = parse_args(argv)
    model = resolve_model(args.model)

    prompt = sys.stdin.read().strip()
    if not prompt:
        print("[github_copilot_provider_real] ERROR: prompt が空です。", file=sys.stderr)
        return 1

    try:
        token = get_gh_token()
    except RuntimeError as exc:
        print(f"[github_copilot_provider_real] ERROR: {exc}", file=sys.stderr)
        return 1

    try:
        response = call_github_models_api(prompt, model, token)
    except RuntimeError as exc:
        print(f"[github_copilot_provider_real] ERROR: {exc}", file=sys.stderr)
        return 1

    print(response)
    return 0


if __name__ == "__main__":
    sys.exit(run())
