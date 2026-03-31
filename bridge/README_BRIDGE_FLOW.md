# Bridge Flow Guide

bridge は進行管理者、Codex は 1 回だけ動く worker という前提で運用する。
共通ルールは固定 docs に置き、毎回の prompt / report には今回差分だけを書く。

## 事前準備チェック

- Safari は起動済み
- ChatGPT は Safari 上でログイン済み
- 対象プロジェクト用チャットを Safari の current tab に表示済み
- `Develop > Allow JavaScript from Apple Events` を有効化済み
- macOS の Automation 許可で、実行元から Safari 操作を許可済み
- `bridge/state.json` の `pause=false`、`error=false`、`bridge/STOP` 不在を確認済み

## 新しい責務分離

- bridge: request、fetch、Codex 起動、archive、次 request、state 更新
- ChatGPT: 次の 1 フェーズ prompt 生成だけを担当
- Codex: 1 フェーズ実装と report 記入だけを担当
- 人: Safari current tab を整える、停止時に原因を見る、必要なら再実行する

## 自動と手動の境界

- 自動: ChatGPT 送信、prompt 回収、Codex 1 回起動、report archive、state 更新、logs/history 保存
- 手動: Safari の対象チャットを current tab に合わせること
- 手動: エラー時に raw dump / log / state を見て原因を直すこと
- 手動: 必要に応じて作業結果を確認し、次に進めるか判断すること

## 最短フロー

1. Safari の対象チャットを current tab に合わせる
2. `python3 scripts/bridge_orchestrator.py` を実行して request を送る
3. 返答後にもう一度実行して prompt を回収する
4. さらにもう一度実行して Codex worker を 1 回起動する
5. report が出たら再実行して archive する
6. もう一度実行して次 request を送る

`python3 scripts/run_one_cycle.py` は互換用で、同じ orchestrator を呼ぶ。

## ready_for_codex の意味

- `ready_for_codex` は「人が prompt を貼る状態」ではなく「bridge が worker を起動できる状態」
- bridge 再実行で `launch_codex_once.py` が動く
- Codex は `bridge/inbox/codex_prompt.md` と固定 docs を読み、report を書いたら終了する

## 短文化の原則

- 共通ルールは `bridge/codex_runner_rules.md` や `bridge/prompt_compaction_rules.md` に置く
- ChatGPT request には state、前回 report 要点、今回ほしい出力だけを書く
- Codex prompt には今回の目的、対象、完了条件だけを書く
- Codex report には次 request 材料として必要な要点だけを書く

## よくある失敗

- `Allow JavaScript from Apple Events` 未設定: Safari 送信 / 回収が止まる
- `-1712 timeout`: Safari の応答待ち、Automation 許可、current tab 固定を確認する
- 対象チャット誤り: Safari の current tab を対象会話へ戻し、必要なら `chat_hint` を設定する
- `state.error=true`: 原因を解消するまで先へ進めず、解消後に error をクリアする
- Codex report 未生成: `logs/*codex_launch*` を見て worker 側の失敗を切り分ける
