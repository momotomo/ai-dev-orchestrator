# Bridge Flow Guide

bridge は進行管理者、Codex は 1 回だけ動く worker という前提で運用する。
共通ルールは固定 docs に置き、毎回の prompt / report には今回差分だけを書く。

## live 1 周前の最小チェック

- Safari の current tab に対象 ChatGPT チャットを開き、live 中は別会話や別タブへ切り替えない
- `Develop > Allow JavaScript from Apple Events` と macOS の Automation 許可を済ませる
- `bridge/state.json` が今回の導線と矛盾していないことを確認する（`pause=false`、`error=false`、`bridge/STOP` 不在。prompt request 開始なら `mode=idle` と `need_chatgpt_prompt=true`）
- `bridge/inbox/codex_prompt.md` と `bridge/outbox/codex_report.md` は runtime 実ファイルなので、live 中は原則手編集しない

## `idle + need_chatgpt_prompt=true` からの最短導線

1. `python3 scripts/bridge_orchestrator.py` を 1 回実行して prompt request を送る
2. ChatGPT 返答が同じ current tab に出たら、もう 1 回 `python3 scripts/bridge_orchestrator.py` を実行して fetch し、`ready_for_codex` に進める
3. `python3 scripts/run_until_stop.py --max-steps 6` を実行して `ready_for_codex` 以降を数手まとめて進める

state がこの前提と違う場合は、下の `state を見た次の 1 手` を優先する。

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

## 1 手ずつ進める場合

1. Safari の対象チャットを current tab に合わせる
2. `python3 scripts/bridge_orchestrator.py` を実行して request を送る
3. 返答後にもう一度実行して prompt を回収する
4. さらにもう一度実行して Codex worker を 1 回起動する
5. report が出たら再実行して archive する
6. もう一度実行して次 request を送る

`python3 scripts/run_one_cycle.py` は互換用で、同じ orchestrator を呼ぶ。

## 1 手実行と連続実行の使い分け

- `python3 scripts/bridge_orchestrator.py`: `idle + need_chatgpt_prompt=true` から request / fetch を 1 手ずつ確認したいときに使う
- `python3 scripts/run_until_stop.py --max-steps 6`: `ready_for_codex` 以降を launch / archive / 次 request まで数手まとめて進めたいときに使う
- live 前 checklist はこの文書と `bridge/run_one_cycle.md` を参照する
- どちらを使う場合も Safari の current tab は途中で切り替えない
- `bridge/inbox/codex_prompt.md` と `bridge/outbox/codex_report.md` は runtime 実ファイルなので原則手編集しない

## state を見た次の 1 手

- `idle + need_chatgpt_prompt=true`: `python3 scripts/bridge_orchestrator.py` を 1 回実行する
- `waiting_prompt_reply`: Safari の current tab が対象チャットか確認して `python3 scripts/bridge_orchestrator.py` を 1 回実行する
- `ready_for_codex`: `python3 scripts/bridge_orchestrator.py` を 1 回実行して worker を起動する
- `codex_running`: `bridge/outbox/codex_report.md` の生成を待つ。report が出たら `python3 scripts/bridge_orchestrator.py` を 1 回実行する
- `codex_done` または `idle + need_chatgpt_next=true`: `python3 scripts/bridge_orchestrator.py` を 1 回実行する

`python3 scripts/run_until_stop.py` は、上の 1 手を `pause=true`、`error=true`、completed 相当、`--max-steps` 到達、失敗、ユーザー中断のいずれかまで繰り返す。

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
- runtime ファイルを手編集: prompt / report の受け渡し判定がぶれるため、切り分け時以外は触らない
- `state.error=true`: 原因を解消するまで先へ進めず、解消後に error をクリアする
- Codex report 未生成: `logs/*codex_launch*` を見て worker 側の失敗を切り分ける
