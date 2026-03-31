# Browser Notes

## 事前準備

- Safari は事前に起動しておく
- ChatGPT は Safari 上でログイン済みであること
- 対象プロジェクト用のチャットを Safari の front window/current tab に開いておく
- 自動化開始時は、その対象チャットが Safari の現在タブになっていること
- Safari の Develop メニューで `Allow JavaScript from Apple Events` を有効にしておく
- macOS の Automation 許可で、実行元アプリから Safari を操作できる状態にしておく

## 毎回の確認

- 送信前から回収完了まで、Safari の current tab を別会話へ切り替えない
- docs の状態例より、実際の `bridge/state.json` を優先して確認する
- `bridge/state.json` が `error=false` であること
- `bridge/STOP` が無いこと
- `mode=codex_running` のままなら、`bridge/outbox/codex_report.md`、`state.pause`、`state.error`、`bridge/STOP` を先に確認し、stale runtime なら復帰導線を優先する
- request 送信前に、対象チャットが `/c/` の会話 URL であること

## 基本方針

- bridge 全体は state 管理、ChatGPT 送信 / 回収、Codex 1 回起動、archive を担当する
- Safari current tab を使う自動化部分は、ChatGPT 送信と回収だけを担当する
- Safari 起動、ログイン、アカウント切り替え、新規チャット作成は行わない
- Chrome は自動化対象にしない
- 重い既存チャットでも通常運用はその current tab をそのまま使い、fetch 待機は 30 分前提で扱う
- Safari の現在タブが ChatGPT でない、対象会話 URL でない、または対象チャット識別に失敗した場合は停止する
- 停止時に会話本文を読める場合は `logs/raw_chatgpt_prompt_dump_*.txt` を残す

## browser_config.json

- `app_name`: 自動化対象ブラウザ名。現状は `Safari` 固定
- `chat_url_prefix`: ChatGPT タブ判定用の URL 接頭辞
- `conversation_url_keywords`: 対象会話ページとみなす URL 断片
- `chat_hint`: 対象チャットを識別したいときの軽いヒント文字列
- `require_chat_hint`: `true` の場合、ヒントが見つからなければ停止する
- `fetch_timeout_seconds`: fetch 側で ChatGPT reply を待つ上限秒数。通常運用の既定値は 1800 秒で、reply を見つけたらこの秒数より前でもすぐ返る
- `poll_interval_seconds`: fetch 中に会話 DOM を見直す間隔
- `apple_event_timeout_retry_count`: Safari AppleEvent timeout 時に追加で試す回数。通常 poll には影響しない
- `apple_event_timeout_retry_delay_seconds`: timeout retry の前に待つ秒数。timeout 時だけ効く
- `runner_heartbeat_seconds`: `run_until_stop.py` の長待ち heartbeat 既定秒数。`waiting_prompt_reply` / `codex_running` の待機ログ間隔として使う

## 運用メモ

- Safari の current tab をそのまま使うため、別タブや別チャットへ切り替えた状態で実行しない
- 実行中に Safari の current tab が別会話へ切り替わった場合は停止する
- 会話履歴に古い `CHATGPT_PROMPT_REPLY` が残っていても、抽出は直近のユーザー発話以降を優先する
- `fetch_next_prompt.py --raw-file <dump>` は DOM 抽出の切り分け用
- `AppleEvent timeout (-1712)` が出た場合は、Safari が前面の対象チャットで応答しているかと、Automation 許可ダイアログが残っていないかを確認する

## timeout 時の確認順

- 1. Safari の current tab が対象チャットのままか確認する
- 2. `Allow JavaScript from Apple Events` が有効なままか確認する
- 3. macOS Automation の許可ダイアログや設定未確定がないか確認する
- 4. `logs/raw_chatgpt_prompt_dump_*.txt` と summary の note を見て、そのまま再実行するか blocked 停止として扱うか判断する

## fetch timeout の閉じ条件

- live で確認済み: 通常 fetch、`retry 1/1` 後の回復、`retry 1/1` 後も未回復で blocked 停止
- blocked になったら bridge は自動継続せず、summary の `suggested_next_command: なし` と note を見て人確認へ渡す
- この領域を再オープンするのは、Safari / macOS 側の挙動が変わって既存の 3 パターンと違う止まり方をした時か、summary / note だけで再開判断しづらくなった時だけでよい

## よくある失敗

- `Safari で Apple Events からの JavaScript 実行が許可されていません`
  Safari の Develop メニュー設定を確認する
- `Safari の現在タブが ChatGPT ではありません`
  Safari の current tab を対象チャットへ戻す
- `Safari の現在タブが ChatGPT の対象会話ではありません`
  新規チャットや一覧画面ではなく、対象会話の `/c/` URL を開く
- `state.error=true`
  先に原因を解消し、その後で error をクリアして再実行する
