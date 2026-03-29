# Prompt Extraction Rules

`fetch_next_prompt.py` は ChatGPT の会話全文から、最後に出現した次のブロックだけを採用する。

```text
===CHATGPT_PROMPT_REPLY===

Codex Prompt

...
===END_REPLY===
```

## 抽出ルール

- `===CHATGPT_PROMPT_REPLY===` と `===END_REPLY===` の組を複数見つけた場合は最後の 1 組を使う
- マーカー外の文章は無視する
- ブロック先頭の `Codex Prompt` 見出しは保存時に取り除いてよい
- 保存先は `bridge/inbox/codex_prompt.md`
- 会話全文の raw dump は必ず `logs/raw_chatgpt_prompt_dump_*.txt` に保存する
- UI フォーカス切り分け時は `python scripts/fetch_next_prompt.py --raw-file <dump>` で同じ抽出処理だけを再実行できる

## 失敗扱い

- 開始マーカーが見つからない
- 終了マーカーが見つからない
- 抽出後の本文が空

上記のいずれかなら `state.error=true` と `error_message` を記録して停止する
