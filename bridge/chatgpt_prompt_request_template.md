# ChatGPT Prompt Request

{REQUEST_GUIDANCE}

## state

{CURRENT_STATUS}

## last_report

{LAST_REPORT}

## request

- next_todo: {NEXT_TODO}
- open_questions: {OPEN_QUESTIONS}

{ISSUE_CENTRIC_NEXT_REQUEST_SECTION}

{RESUME_CONTEXT_SECTION}

live 1 周確認へ寄せる場合は、返答内で次を優先してください。

- live 実行前の最小 checklist
- `CURRENT_STATUS` が `idle + need_chatgpt_prompt=true` なら、最初に `python3 scripts/bridge_orchestrator.py` を 1 回実行して prompt request を送ること
- 返答取得後は fetch して `ready_for_codex` に進み、その後 `python3 scripts/run_until_stop.py --max-steps 6` で数手進める最短導線
- runtime 実ファイルは原則変更しないこと

返答は bridge がそのまま読むので、前置きや余計な説明を付けず、
bridge reply contract (issue-centric contract only) に従った形式で返してください。
reply contract の内容は request 末尾の `## bridge_reply_contract` セクションに記載されています。
