# ChatGPT Prompt Request Sample

あなたは次に Codex へ渡す 1 フェーズ分の実装プロンプトを作る担当です。
特定案件に寄りすぎず、再利用しやすい形で、今回の状況に合う次の一手だけを提案してください。

## 前提

- Codex は実装担当です。
- ChatGPT は次の Codex 用プロンプト生成担当です。
- Python ブリッジはファイル入出力とデスクトップ操作を補助します。
- 今回ほしいのは、1 フェーズ = 1 目的の短い Codex 用プロンプトです。
- 不足情報があっても、危険な仮定を避けつつ安全側で前提を置いてください。

## Current Status

- mode: idle
- cycle: 2
- need_chatgpt_prompt: true
- need_codex_run: false
- need_chatgpt_next: false
- last_report_file: bridge/history/cycle_0002_codex_report_20260329_183000.md

## Last Report

1. 実施概要
   汎用ドキュメント整理を実施し、運用手順の重複表現を削減した。
2. 変更した主要ファイル
   docs/guide.md, docs/faq.md
3. 実装内容 / 仕様
   操作説明を一本化し、導線を整理した。
4. 確認結果
   Markdown lint と目視確認を実施した。
5. --no-ff マージ・push結果
   作業ブランチで完了。統合は未実施。
6. 注意点
   次は export まわりの説明改善かテスト補強が候補。

## Next Todo

次は export 改善、テスト追加、ドキュメント整理のいずれかから、もっとも小さく安全に進められる 1 フェーズを選んで Codex 用プロンプトにしてください。

## Open Questions

- 現時点で技術スタックは固定しない
- UI 変更が必要でも最小差分に留める

## 出力条件

- 日本語で書く
- 差分中心
- 節約版
- 1 フェーズ = 1 目的
- 既存動作を壊さない
- 過剰な大改造を避ける
- 必要なら確認項目を最後に含める
- 返答は次の形式だけにする

===CHATGPT_PROMPT_REPLY===

Codex Prompt

[ここに Codex へそのまま渡せる次フェーズ用プロンプトを書く]

===END_REPLY===
