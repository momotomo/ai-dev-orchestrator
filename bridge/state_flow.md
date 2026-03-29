# State Flow

## mode 一覧

- `idle`
  待機状態。次に ChatGPT へ prompt request を送るかどうかはフラグで判定する。
- `waiting_prompt_reply`
  ChatGPT へ要求を送ったので、返答回収待ちの状態。
- `ready_for_codex`
  `bridge/inbox/codex_prompt.md` に次の Codex 用プロンプトが入り、Codex 実行待ちの状態。
- `codex_running`
  Codex がその 1 フェーズを実装している状態。
- `codex_done`
  Codex が完了報告を書き終え、次の処理待ちの状態。

## 主なフラグ

- `need_chatgpt_prompt`
  初回や再開時に、ChatGPT へ次の Codex 用プロンプト要求を送る必要がある。
- `need_codex_run`
  `bridge/inbox/codex_prompt.md` が準備できており、Codex 実行が必要。
- `need_chatgpt_next`
  Codex の完了報告を踏まえて、次フェーズ用の prompt request を ChatGPT に送る必要がある。
- `pause`
  運用上の一時停止。
- `error`
  失敗が記録されており、原因確認までは継続しない。

## 代表的な遷移

1. `idle` + `need_chatgpt_prompt=true`
   `request_next_prompt.py` 実行後に `waiting_prompt_reply`
2. `waiting_prompt_reply`
   `fetch_next_prompt.py` 実行後に `ready_for_codex`
3. `ready_for_codex`
   人が Codex を起動し、開始時に `codex_running`
4. `codex_running`
   Codex 完了後に `codex_done`
5. `codex_done`
   `archive_codex_report.py` 実行後に `idle` + `need_chatgpt_next=true`
6. `idle` + `need_chatgpt_next=true`
   `request_prompt_from_report.py` 実行後に `waiting_prompt_reply`

## cycle の考え方

- `cycle` は完了報告を履歴へ退避した時点で 1 増やす
- つまり、完了した 1 周の数を表す
