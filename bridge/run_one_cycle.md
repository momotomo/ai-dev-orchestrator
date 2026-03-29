# Run One Cycle

この文書は ChatGPT → Codex → ChatGPT の 1 周を、手順どおりに回すための運用メモです。

## 事前確認

- `bridge/state.json` の `error` が `false`
- `bridge/state.json` の `pause` が `false`
- `bridge/STOP` が存在しない
- Safari は事前に起動している
- ChatGPT は Safari 上でログイン済みである
- 対象プロジェクト用のチャットを Safari の現在タブに表示している
- Safari の Develop メニューで `Allow JavaScript from Apple Events` を有効にしている

## 手順

1. 初回または次プロンプト要求時に `python3 scripts/run_one_cycle.py` を実行する
2. `idle + need_chatgpt_prompt=true` なら `request_next_prompt.py` が走り、Safari の現在タブにある対象チャットへ次の Codex 用プロンプト要求を送る
3. ChatGPT の返答が出たら、対象チャットが Safari の現在タブのままになっていることを確認し、`python3 scripts/run_one_cycle.py` をもう一度実行する
4. `waiting_prompt_reply` なら `fetch_next_prompt.py` が走り、Safari の現在タブにある対象チャット DOM から最後の `===CHATGPT_PROMPT_REPLY===` ブロックを `bridge/inbox/codex_prompt.md` に保存する
5. `ready_for_codex` になったら、人が `bridge/inbox/codex_prompt.md` を Codex に渡して実装する
6. Codex は実装開始時に `mode=codex_running`、完了時に `bridge/outbox/codex_report.md` を書いて `mode=codex_done` にする
7. `python3 scripts/run_one_cycle.py` を再実行すると、`codex_done` なら `archive_codex_report.py` が走り、完了報告を `bridge/history/` へ退避する
8. さらに `python3 scripts/run_one_cycle.py` を実行すると、`idle + need_chatgpt_next=true` なら `request_prompt_from_report.py` が走り、Safari の現在タブにある対象チャットへ次フェーズ用の prompt request を送る

## 補足

- Safari 起動とログインは事前準備であり、スクリプトは行いません
- Chrome は自動化対象にしません
- 送信先も回収元も、Safari の現在タブにある対象 ChatGPT チャットです
- 現在タブが違う、対象会話 URL でない、入力欄がない、会話領域がない、`chat_hint` が一致しない場合は停止します
- 送信ログは `logs/sent_prompt_request_*.md`、画面コピーの raw dump は `logs/raw_chatgpt_prompt_dump_*.txt` に保存されます
- DOM 抽出の切り分け時だけは `fetch_next_prompt.py --raw-file <dump>` で抽出処理を再試行できます

## ready_for_codex 以降

- `ready_for_codex` は自動処理の終点で、ここから先は人が Codex へ prompt を渡す
- Codex は 1 フェーズだけ実装する
- 実装開始時に `codex_running`、完了報告記入後に `codex_done` にする
- `codex_done` になったら `python3 scripts/run_one_cycle.py` を再実行して archive と次 prompt request 側へ進める

## 失敗時の切り分け

- `-1712 timeout`: Safari の応答待ち、Automation 許可、current tab 固定を確認する
- 期待外れの prompt が保存された: 最新の `logs/raw_chatgpt_prompt_dump_*.txt` と `logs/sent_prompt_request*.md` を見比べる
- `state.error=true`: 原因確認前に先へ進めない
