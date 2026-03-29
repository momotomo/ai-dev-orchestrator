# State Flow

## mode 一覧

- `idle`
  待機状態。次に ChatGPT へ prompt request を送るかどうかはフラグで判定する。
- `waiting_prompt_reply`
  Safari の現在 ChatGPT 対象タブへ要求を送ったので、返答回収待ちの状態。
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
   `fetch_next_prompt.py` が Safari の現在対象タブ DOM から返信を回収し、`ready_for_codex`
3. `ready_for_codex`
   人が Codex を起動し、開始時に `codex_running`
4. `codex_running`
   Codex 完了後に `codex_done`
5. `codex_done`
   `archive_codex_report.py` 実行後に `idle` + `need_chatgpt_next=true`
6. `idle` + `need_chatgpt_next=true`
   `request_prompt_from_report.py` 実行後に `waiting_prompt_reply`

## Safari 運用メモ

- Safari 起動とログインは事前準備
- 対象チャット表示も事前準備
- 自動化は、その後の送信・回収・state 遷移だけを担当する
- 対象チャットが違う場合は停止する
- Chrome は自動化対象にしない
- `ready_for_codex` から `codex_done` までは Codex 実装フェーズで、人手確認を含む

## 失敗時の考え方

- `error=true` は「原因未解消のまま進めない」ための停止状態
- Safari 送信 / 回収の失敗時は、current tab、Automation 許可、raw dump の順に見る
- archive 後は `cycle` が 1 増えるため、何周目まで完了したかを追える

## cycle の考え方

- `cycle` は完了報告を履歴へ退避した時点で 1 増やす
- つまり、完了した 1 周の数を表す
