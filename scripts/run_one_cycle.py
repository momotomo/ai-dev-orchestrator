#!/usr/bin/env python3
from __future__ import annotations

import sys

import archive_codex_report
import fetch_next_prompt
import request_next_prompt
import request_prompt_from_report
from _bridge_common import guarded_main


def run(state: dict[str, object]) -> int:
    mode = str(state.get("mode", "idle"))

    if mode == "idle" and bool(state.get("need_chatgpt_prompt")):
        print("state=idle / need_chatgpt_prompt=true のため、ChatGPT へ次プロンプト要求を送ります。")
        return request_next_prompt.run(dict(state))

    if mode == "waiting_prompt_reply":
        print("state=waiting_prompt_reply のため、ChatGPT 返答から次の Codex 用プロンプトを回収します。")
        return fetch_next_prompt.run(dict(state))

    if mode == "ready_for_codex":
        print("state=ready_for_codex です。bridge/inbox/codex_prompt.md を Codex に渡して実装を進めてください。")
        return 0

    if mode == "codex_running":
        print("state=codex_running です。Codex 実装中なので完了報告の生成を待ってください。")
        return 0

    if mode == "codex_done":
        print("state=codex_done のため、完了報告を履歴へ退避します。")
        return archive_codex_report.run(dict(state))

    if mode == "idle" and bool(state.get("need_chatgpt_next")):
        print("state=idle / need_chatgpt_next=true のため、完了報告をもとに次フェーズ要求を送ります。")
        return request_prompt_from_report.run(dict(state))

    print("今回の 1 手はありません。state.json を確認してください。")
    return 0


if __name__ == "__main__":
    sys.exit(guarded_main(run))
