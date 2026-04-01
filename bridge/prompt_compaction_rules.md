# Prompt Compaction Rules

## 目的

- 共通説明を固定 docs に寄せる
- 毎回の request / prompt / report には今回差分だけを書く
- ChatGPT、Codex、人間が毎回読む量を減らす

## 固定 docs に置くもの

- 役割分担
- state flow
- Safari current tab 前提
- Codex worker の共通ルール
- Git ルール
- 停止条件と再開ポイント

## 毎回の request に書くもの

- 現在の state
- 前回 report の要点
- 今回ほしい 1 フェーズ
- 未解決事項があれば最小限

## 毎回の Codex prompt に書くもの

- 今回の目的
- 対象ファイル / 領域
- 完了条件
- 今回だけの注意

## 毎回の report に書くもの

- 今回やったこと
- 主な変更ファイル
- 確認結果
- 次状態と残課題

## 書かないほうがよいもの

- 毎回同じ背景説明
- 既に固定 docs にある一般ルール
- 次フェーズ以降の長い計画
- 未確認の前提を大量に並べた保険文
