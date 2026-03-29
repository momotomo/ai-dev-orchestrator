# Run One Cycle

この文書は ChatGPT → Codex → ChatGPT の 1 周を、手順どおりに回すための運用メモです。

## 事前確認

- `bridge/state.json` の `error` が `false`
- `bridge/state.json` の `pause` が `false`
- `bridge/STOP` が存在しない
- Chrome は事前に起動している
- ChatGPT は事前にログイン済みである
- 対象プロジェクト用のチャットを開いて前面にしている
- Playwright が接続できる Chrome DevTools endpoint が `bridge/browser_config.json` に設定されている

## 手順

1. 初回または次プロンプト要求時に `python scripts/run_one_cycle.py` を実行する
2. `idle + need_chatgpt_prompt=true` なら `request_next_prompt.py` が走り、前面の対象チャットへ次の Codex 用プロンプト要求を送る
3. ChatGPT の返答が出たら、対象チャットが前面のままになっていることを確認し、`python scripts/run_one_cycle.py` をもう一度実行する
4. `waiting_prompt_reply` なら `fetch_next_prompt.py` が走り、前面の対象チャット DOM から最後の `===CHATGPT_PROMPT_REPLY===` ブロックを `bridge/inbox/codex_prompt.md` に保存する
5. `ready_for_codex` になったら、人が `bridge/inbox/codex_prompt.md` を Codex に渡して実装する
6. Codex は実装開始時に `mode=codex_running`、完了時に `bridge/outbox/codex_report.md` を書いて `mode=codex_done` にする
7. `python scripts/run_one_cycle.py` を再実行すると、`codex_done` なら `archive_codex_report.py` が走り、完了報告を `bridge/history/` へ退避する
8. さらに `python scripts/run_one_cycle.py` を実行すると、`idle + need_chatgpt_next=true` なら `request_prompt_from_report.py` が走り、前面の対象チャットへ次フェーズ用の prompt request を送る

## 補足

- Chrome 起動とログインは事前準備であり、スクリプトは行いません
- 送信先も回収元も、前面にある対象 ChatGPT チャットです
- 前面タブが違う、入力欄がない、会話領域がない、`chat_hint` が一致しない場合は停止します
- 送信ログは `logs/sent_prompt_request_*.md`、画面コピーの raw dump は `logs/raw_chatgpt_prompt_dump_*.txt` に保存されます
- DOM 抽出の切り分け時だけは `fetch_next_prompt.py --raw-file <dump>` で抽出処理を再試行できます
