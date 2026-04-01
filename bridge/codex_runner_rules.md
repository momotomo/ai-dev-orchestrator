# Codex Runner Rules

## 役割

- bridge が進行管理者
- Codex は今回 1 回だけ動く worker
- ChatGPT への問い合わせや次フェーズ判断は bridge が行う

## Codex がやること

- 最初に launch prompt に出ている固定 docs を順に読む
- fixed docs で足りない今回差分だけを prompt file から読む
- prompt file に `追加確認 docs` がある時だけ、その path を追加で読む
- 今回の 1 フェーズだけ実装する
- 必要なら最小限の確認だけを行う
- launch prompt に出ている report template file の絶対 path に沿って、launch prompt に出ている report file へ書く
- 外部 worker repo 実行で report file へ書けない時だけ、launch prompt に出ている fallback report path へ同内容を書く
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

## docs の見方

- fixed docs は毎回着手前に確認する
- prompt file は今回差分の正本として扱う
- 追加 docs は prompt file で必要と明記された時だけ読む

## state の考え方

- `codex_running` への遷移は bridge launcher が行う
- `codex_done` への遷移も bridge launcher が report 有無を見て行う
- Codex は原則 `bridge/state.json` を更新しない
