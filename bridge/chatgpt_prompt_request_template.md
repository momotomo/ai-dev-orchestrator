# ChatGPT Prompt Request

次の Codex 用 1 フェーズ prompt だけを返してください。
共通ルールは固定 docs 側にあるので、今回差分だけに集中してください。

## state

{CURRENT_STATUS}

## last_report

{LAST_REPORT}

## request

- next_todo: {NEXT_TODO}
- open_questions: {OPEN_QUESTIONS}

live 1 周確認へ寄せる場合は、返答内で次を優先してください。

- live 実行前の最小 checklist
- この状態なら次に何を実行するか
- runtime 実ファイルは原則変更しないこと

返答はこのブロックだけにしてください。

===CHATGPT_PROMPT_REPLY===
[Codex 用 1 フェーズ prompt 本文]
===END_REPLY===
