# Run One Cycle

この文書は ChatGPT → Codex → ChatGPT の 1 周を、手順どおりに回すための運用メモです。

## 事前確認

- `bridge/state.json` の `error` が `false`
- `bridge/state.json` の `pause` が `false`
- `bridge/STOP` が存在しない
- macOS のアクセシビリティ許可で ChatGPT デスクトップ操作ができる

## 手順

1. 初回または次プロンプト要求時に `python scripts/run_one_cycle.py` を実行する
2. `idle + need_chatgpt_prompt=true` なら `request_next_prompt.py` が走り、ChatGPT に次の Codex 用プロンプト要求を送る
3. ChatGPT の返答が出たら、必要に応じて会話画面を前面にし、`python scripts/run_one_cycle.py` をもう一度実行する
4. `waiting_prompt_reply` なら `fetch_next_prompt.py` が走り、最後の `===CHATGPT_PROMPT_REPLY===` ブロックを `bridge/inbox/codex_prompt.md` に保存する
5. `ready_for_codex` になったら、人が `bridge/inbox/codex_prompt.md` を Codex に渡して実装する
6. Codex は実装開始時に `mode=codex_running`、完了時に `bridge/outbox/codex_report.md` を書いて `mode=codex_done` にする
7. `python scripts/run_one_cycle.py` を再実行すると、`codex_done` なら `archive_codex_report.py` が走り、完了報告を `bridge/history/` へ退避する
8. さらに `python scripts/run_one_cycle.py` を実行すると、`idle + need_chatgpt_next=true` なら `request_prompt_from_report.py` が走り、次フェーズ用の prompt request を ChatGPT に送る

## 補足

- `fetch_next_prompt.py` は画面全文コピー前提です
- ChatGPT アプリの挙動によっては、会話領域を一度クリックしてから取得したほうが安定します
- raw dump は `logs/` に保存されます
