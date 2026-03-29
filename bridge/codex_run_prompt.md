# Codex Run Prompt

以下の運用で Codex を実行する。

1. `bridge/inbox/codex_prompt.md` を読む
2. `bridge/state.json` の `mode` を `codex_running` に更新する
3. 今回の 1 フェーズだけ実装する
4. 必要なら最小限の確認を行う
5. `bridge/outbox/codex_report.md` に完了報告を書く
6. `bridge/state.json` を次の状態に更新する

```json
{
  "mode": "codex_done",
  "need_codex_run": false
}
```

追加原則:

- 既存動作を壊さない
- 過剰な大改造をしない
- 1 回で 1 目的だけ進める
- 未解決事項は完了報告の注意点に残す
