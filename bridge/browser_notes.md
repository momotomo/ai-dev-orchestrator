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
- `bridge/state.json` が `error=false` であること
- `bridge/STOP` が無いこと
- request 送信前に、対象チャットが `/c/` の会話 URL であること

## 基本方針

- bridge 全体は state 管理、ChatGPT 送信 / 回収、Codex 1 回起動、archive を担当する
- Safari current tab を使う自動化部分は、ChatGPT 送信と回収だけを担当する
- Safari 起動、ログイン、アカウント切り替え、新規チャット作成は行わない
- Chrome は自動化対象にしない
- Safari の現在タブが ChatGPT でない、対象会話 URL でない、または対象チャット識別に失敗した場合は停止する
- 停止時に会話本文を読める場合は `logs/raw_chatgpt_prompt_dump_*.txt` を残す

## browser_config.json

- `app_name`: 自動化対象ブラウザ名。現状は `Safari` 固定
- `chat_url_prefix`: ChatGPT タブ判定用の URL 接頭辞
- `conversation_url_keywords`: 対象会話ページとみなす URL 断片
- `chat_hint`: 対象チャットを識別したいときの軽いヒント文字列
- `require_chat_hint`: `true` の場合、ヒントが見つからなければ停止する

## 運用メモ

- Safari の current tab をそのまま使うため、別タブや別チャットへ切り替えた状態で実行しない
- 実行中に Safari の current tab が別会話へ切り替わった場合は停止する
- 会話履歴に古い `CHATGPT_PROMPT_REPLY` が残っていても、抽出は直近のユーザー発話以降を優先する
- `fetch_next_prompt.py --raw-file <dump>` は DOM 抽出の切り分け用
- `AppleEvent timeout (-1712)` が出た場合は、Safari が前面の対象チャットで応答しているかと、Automation 許可ダイアログが残っていないかを確認する

## よくある失敗

- `Safari で Apple Events からの JavaScript 実行が許可されていません`
  Safari の Develop メニュー設定を確認する
- `Safari の現在タブが ChatGPT ではありません`
  Safari の current tab を対象チャットへ戻す
- `Safari の現在タブが ChatGPT の対象会話ではありません`
  新規チャットや一覧画面ではなく、対象会話の `/c/` URL を開く
- `state.error=true`
  先に原因を解消し、その後で error をクリアして再実行する
