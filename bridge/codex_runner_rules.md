# Codex Runner Rules

## 基本方針

- 毎回 `bridge/inbox/codex_prompt.md` を最初に読む
- 1 回の実行では 1 フェーズだけ実装する
- 特定案件依存の大きな前提を勝手に増やさない
- 過剰な大改造を避け、差分中心で進める
- 既存動作を壊さないことを優先する

## state 更新ルール

- 実装開始時に `bridge/state.json` の `mode` を `codex_running` にする
- 実装中は `need_codex_run` を `true` のままにしてよい
- 完了時に `bridge/outbox/codex_report.md` を書く
- 完了時に `mode` を `codex_done` にする
- 完了時に `need_codex_run` を `false` にする
- 失敗時は `error=true` と `error_message` を記録して無理に継続しない

## 実装ルール

- 目的外の変更を広げない
- 必要なら最小限のテストや確認だけを追加する
- 既存コードが読める範囲で自然に沿う
- 不明点があっても危険な推測は避け、注意点に残す

## 完了報告ルール

- 報告先は `bridge/outbox/codex_report.md`
- 形式は `bridge/codex_report_template.md` に従う
- 日本語で簡潔にまとめる
