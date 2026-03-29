# Browser Notes

## 事前準備

- Safari は事前に起動しておく
- ChatGPT は Safari 上でログイン済みであること
- 対象プロジェクト用のチャットを Safari の front window/current tab に開いておく
- 自動化開始時は、その対象チャットが Safari の現在タブになっていること
- Safari の Develop メニューで `Allow JavaScript from Apple Events` を有効にしておく

## 基本方針

- 自動化は、Safari の現在タブにある ChatGPT チャットの送信と回収だけを担当する
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
