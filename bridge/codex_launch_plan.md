# Codex Launch Plan

## 現段階の方針

- 現段階では Python から Codex を自動起動しない
- `run_one_cycle.py` は次の 1 手だけを進める司令塔に留める
- `ready_for_codex` になったら、人が `bridge/inbox/codex_prompt.md` を Codex に渡して実装を開始する

## 理由

- 最初は 1 周分を確実に回せる準備完了状態を優先する
- ChatGPT デスクトップ操作と Codex 実装を切り分けるほうが障害点を減らせる
- 別案件へ流用するときも、人の確認ポイントを残したほうが安全

## 将来の拡張候補

- Codex CLI やデスクトップ起動の補助
- `codex_running` の監視補助
- 完了報告の自動回収と次プロンプト要求の半自動化
