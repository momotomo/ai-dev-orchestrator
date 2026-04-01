# ChatGPT Prompt Request

次の Codex 用 1 フェーズ prompt だけを返してください。
共通ルールは固定 docs 側にあるので、今回差分だけに集中してください。
Git ルールや worker 共通ルールを prompt 本文へ長く重複記載せず、必要なら `追加確認 docs` だけを短く足してください。

## state

{CURRENT_STATUS}

## last_report

{LAST_REPORT}

## request

- next_todo: {NEXT_TODO}
- open_questions: {OPEN_QUESTIONS}

{RESUME_CONTEXT_SECTION}

live 1 周確認へ寄せる場合は、返答内で次を優先してください。

- live 実行前の最小 checklist
- `CURRENT_STATUS` が `idle + need_chatgpt_prompt=true` なら、最初に `python3 scripts/bridge_orchestrator.py` を 1 回実行して prompt request を送ること
- 返答取得後は fetch して `ready_for_codex` に進み、その後 `python3 scripts/run_until_stop.py --max-steps 6` で数手進める最短導線
- runtime 実ファイルは原則変更しないこと

返答は bridge がそのまま読むので、前置きや余計な説明を付けず、次のどちらか 1 つのブロックだけにしてください。

- Codex に渡す 1 フェーズ prompt があるなら `CHATGPT_PROMPT_REPLY`
- 今回は Codex に渡さないなら `CHATGPT_NO_CODEX`
- `CHATGPT_NO_CODEX` の先頭行は `completed` / `human_review` / `need_info` のいずれか

===CHATGPT_PROMPT_REPLY===
[Codex 用 1 フェーズ prompt 本文]
===END_REPLY===

または

===CHATGPT_NO_CODEX===
completed | human_review | need_info
[必要なら短い理由]
===END_NO_CODEX===
