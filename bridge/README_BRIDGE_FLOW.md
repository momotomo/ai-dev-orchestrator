# Bridge Flow Guide

この文書は、Safari 前提の ChatGPT ↔ Codex ループを最短で回すための総合メモです。
実案件の仕様ではなく、bridge 基盤の運用手順だけを短く整理します。

## 事前準備チェック

- Safari は起動済み
- ChatGPT は Safari 上でログイン済み
- 対象プロジェクト用チャットを Safari の current tab に表示済み
- Safari の Develop メニューで `Allow JavaScript from Apple Events` を有効化済み
- macOS の Automation 許可で、実行元から Safari 操作を許可済み
- `bridge/state.json` の `pause=false`、`error=false`、`bridge/STOP` 不在を確認済み

## 自動と手動の境界

- 自動: prompt request 送信、prompt 回収、report archive、state 更新、logs/history 保存
- 手動: Safari の対象チャットを current tab に合わせること
- 手動: `ready_for_codex` 以降に `bridge/inbox/codex_prompt.md` を Codex に渡して 1 フェーズ実装すること
- 手動: Codex 実装後の内容確認と、必要に応じたコミット判断

## 最短 1 周手順

1. Safari の対象チャットを current tab に合わせる
2. `python3 scripts/run_one_cycle.py` で prompt request を送る
3. 返答後にもう一度 `python3 scripts/run_one_cycle.py` を実行して `ready_for_codex` にする
4. `bridge/inbox/codex_prompt.md` を Codex に渡し、開始時に `codex_running`、完了時に `codex_done` にする
5. `python3 scripts/run_one_cycle.py` を実行して report archive を行う
6. さらに `python3 scripts/run_one_cycle.py` を実行して次 prompt request を送り、返答後にもう一度実行して次の `ready_for_codex` に進む

## ready_for_codex でやること

- `bridge/inbox/codex_prompt.md` の 1 目的だけを実行する
- 過剰な大改造は避ける
- 完了時に `bridge/outbox/codex_report.md` を 6 項目形式で書く
- report 書き込み後に `mode=codex_done`、`need_codex_run=false` にする

## よくある失敗

- `Allow JavaScript from Apple Events` 未設定: Safari 送信 / 回収が止まる
- Automation 許可未完了や `-1712 timeout`: Safari の許可ダイアログ、タブ応答、current tab 固定を確認する
- 対象チャット誤り: Safari の current tab を対象会話へ戻し、必要なら `chat_hint` を設定する
- `state.error=true`: 原因を解消するまで先へ進めず、解消後に error をクリアする
- 抽出結果が不自然: `logs/raw_chatgpt_prompt_dump_*.txt` と `logs/sent_prompt_request*.md` を見比べる
