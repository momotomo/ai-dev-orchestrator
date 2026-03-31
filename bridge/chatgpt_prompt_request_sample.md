# ChatGPT Prompt Request Sample

次の Codex 用 1 フェーズ prompt だけを返してください。
共通ルールは固定 docs 側にあるので、今回差分だけに集中してください。

## state

- mode: idle
- cycle: 2
- need_chatgpt_prompt: true
- need_codex_run: false
- need_chatgpt_next: false
- last_report_file: bridge/history/codex_report_cycle_0002_20260329_183000.md

## last_report

===BRIDGE_SUMMARY===
- summary: 汎用ドキュメント整理を行い、運用手順の重複を削減した
- changed: docs/guide.md, docs/faq.md
- verify: Markdown lint と目視確認を実施
- next_state: codex_done
- risks: 次は export 説明改善かテスト補強が候補
===END_BRIDGE_SUMMARY===

## request

- next_todo: export 改善、テスト追加、ドキュメント整理のいずれかから、最も小さく安全に進められる 1 フェーズを選んでください
- open_questions: 技術スタックは固定しない。UI 変更が必要でも最小差分に留める

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
