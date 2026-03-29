# Browser Notes

## 事前準備

- Chrome は事前に起動しておく
- ChatGPT はログイン済みであること
- 対象プロジェクト用のチャットを開いておく
- 自動化開始時は、その対象チャットを前面にしておく
- Playwright 接続のため、Chrome は事前に remote debugging 有効で起動しておく

## 基本方針

- 自動化は、前面にある ChatGPT チャットの送信と回収だけを担当する
- Chrome 起動、ログイン、アカウント切り替え、新規チャット作成は行わない
- 前面タブが ChatGPT でない、または対象チャット識別に失敗した場合は停止する
- 停止時に会話本文を読める場合は `logs/raw_chatgpt_prompt_dump_*.txt` を残す

## browser_config.json

- `cdp_endpoint`: 既存 Chrome に接続する DevTools endpoint
- `chat_url_prefix`: ChatGPT タブ判定用の URL 接頭辞
- `chat_hint`: 対象チャットを識別したいときの軽いヒント文字列
- `require_chat_hint`: `true` の場合、ヒントが見つからなければ停止する

## 運用メモ

- 前面タブをそのまま使うため、別タブや別チャットへ切り替えた状態で実行しない
- `fetch_next_prompt.py --raw-file <dump>` は DOM 抽出の切り分け用
