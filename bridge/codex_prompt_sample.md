# Codex Prompt Sample

> **Note:** The stability-first instruction and mandatory execution rules preamble is now
> **repo-enforced** and automatically prepended before the prompt title by
> `build_issue_centric_codex_prompt` in `scripts/issue_centric_codex_launch.py`.
> Do not manually add the preamble to the request body — it is injected from
> `COPILOT_STABILITY_PREAMBLE` (single source of truth).

## 目的

- export 成功時 / 失敗時の表示文言だけを小さく整理する

## 対象

- `src/exporter.py`
- `src/ui/export_panel.py`
- UI 全面改修には広げない

## 追加確認 docs

- なし

## 完了条件

- export 失敗時メッセージが利用者向けに読みやすくなる
- export 成功時表示が簡潔になる
- 必要なら関連テストを最小限で追加する

## 今回の注意

- 既存の export 条件分岐は壊さない
- 共通ルールは fixed docs にあるので、ここには今回差分だけを書く

## 別テーマにも流用しやすい例

- UI polish
- テスト追加
- 小さめのリファクタ
- ドキュメント整理
