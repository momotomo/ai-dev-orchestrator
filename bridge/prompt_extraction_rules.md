# Prompt Extraction Rules

`fetch_next_prompt.py` は Safari の現在 ChatGPT 対象タブ DOM から、最後に出現した次のブロックだけを採用する。

```text
===CHATGPT_PROMPT_REPLY===
[Codex 用 1 フェーズ prompt 本文]
===END_REPLY===
```

## 抽出ルール

- `===CHATGPT_PROMPT_REPLY===` と `===END_REPLY===` の組を複数見つけた場合でも、直近のユーザー発話以降にある ChatGPT 側の 1 組を優先する
- ユーザー request テンプレート内に含まれる `CHATGPT_PROMPT_REPLY` は採用しない
- マーカー外の文章は無視する
- ブロック先頭に `Codex Prompt` 見出しがあれば保存時に取り除いてよい
- 保存先は `bridge/inbox/codex_prompt.md`
- 会話全文の raw dump は必ず `logs/raw_chatgpt_prompt_dump_*.txt` に保存する
- Safari の現在タブが ChatGPT でない場合は停止する
- Safari の現在タブが対象会話 URL でない場合は停止する
- `chat_hint` が設定されていて一致しない場合は停止する
- 停止時に会話本文が取得できている場合も `logs/raw_chatgpt_prompt_dump_*.txt` に raw dump を残す
- DOM 抽出の切り分け時は `python3 scripts/fetch_next_prompt.py --raw-file <dump>` で同じ抽出処理だけを再実行できる

## 失敗扱い

- 開始マーカーが見つからない
- 終了マーカーが見つからない
- 抽出後の本文が空

上記のいずれかなら `state.error=true` と `error_message` を記録して停止する
