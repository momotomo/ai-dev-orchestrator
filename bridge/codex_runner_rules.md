# Codex Runner Rules

## 役割

- bridge が進行管理者
- Codex は今回 1 回だけ動く worker
- ChatGPT への問い合わせや次フェーズ判断は bridge が行う

## Codex がやること

- 最初に `bridge/inbox/codex_prompt.md` を読む
- 今回の 1 フェーズだけ実装する
- 必要なら最小限の確認だけを行う
- `bridge/codex_report_template.md` に沿って `bridge/outbox/codex_report.md` を書く
- 外部 worker repo 実行で `bridge/outbox/codex_report.md` が書けない時だけ、launch prompt に出ている fallback report path へ同内容を書く
- report を書いたら終了する

## Codex がやらないこと

- ChatGPT に問い合わせない
- bridge script を起動しない
- loop 継続判断をしない
- 次フェーズを勝手に決めない
- 過剰な大改造をしない

## 品質原則

- 差分中心で進める
- 既存動作を壊さないことを優先する
- 特定案件依存の前提を勝手に増やさない
- 不明点は危険な推測で埋めず、report の残課題に残す

## state の考え方

- `codex_running` への遷移は bridge launcher が行う
- `codex_done` への遷移も bridge launcher が report 有無を見て行う
- Codex は原則 `bridge/state.json` を更新しない
