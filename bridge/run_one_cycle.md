# Run One Cycle

この文書は、bridge が次の 1 手だけ進める運用を前提にした手順メモです。

## live 1 周前の最小チェック

- Safari の current tab に対象 ChatGPT チャットを開き、live 中は別会話や別タブへ切り替えない
- Safari の Develop メニューで `Allow JavaScript from Apple Events` を有効にし、macOS の Automation 許可も済ませる
- `bridge/state.json` が今回の導線と矛盾していないことを確認する（`error=false`、`pause=false`、`bridge/STOP` 不在。prompt request 開始なら `mode=idle` と `need_chatgpt_prompt=true`）
- `bridge/inbox/codex_prompt.md` と `bridge/outbox/codex_report.md` は runtime 実ファイルなので、live 中は原則手編集しない

ここまで問題なければ、`bridge/state.json` を見て `python3 scripts/bridge_orchestrator.py` を 1 回だけ実行する。

## `idle + need_chatgpt_prompt=true` からの最短導線

1. `python3 scripts/bridge_orchestrator.py` を 1 回実行して prompt request を送る
2. ChatGPT 返答が同じ current tab に出たら、もう 1 回 `python3 scripts/bridge_orchestrator.py` を実行して fetch し、`ready_for_codex` に進める
3. `python3 scripts/run_until_stop.py --max-steps 6` を実行して `ready_for_codex` 以降を数手まとめて進める

state がこの前提と違う場合は、下の `state を見た次の 1 手` に従う。

## 基本コマンド

- 推奨: `python3 scripts/bridge_orchestrator.py`
- 互換用: `python3 scripts/run_one_cycle.py`
- `ready_for_codex` 以降を数手まとめて進めたい場合: `python3 scripts/run_until_stop.py --max-steps 6`
- worker だけ直接起動したい場合: `python3 scripts/launch_codex_once.py`

## 使い分け

- `idle + need_chatgpt_prompt=true` から request / fetch を 1 手ずつ見たい: `bridge_orchestrator.py`
- `ready_for_codex` 以降を launch / archive / 次 request までまとめて進めたい: `run_until_stop.py`
- `run_until_stop.py` は常駐しない。止まる条件まで進めたら終了する
- 再開時は、止まった時点の `bridge/state.json` を見て再度 `run_until_stop.py` か `bridge_orchestrator.py` を選ぶ

## state を見た次の 1 手

1. `idle + need_chatgpt_prompt=true`
   `python3 scripts/bridge_orchestrator.py` を 1 回実行し、bridge が `request_next_prompt.py` を動かして `waiting_prompt_reply` へ進める
2. `waiting_prompt_reply`
   Safari current tab を確認してから `python3 scripts/bridge_orchestrator.py` を 1 回実行し、bridge が `fetch_next_prompt.py` を動かして `ready_for_codex` へ進める
3. `ready_for_codex`
   `python3 scripts/bridge_orchestrator.py` を 1 回実行し、bridge が `launch_codex_once.py` を動かして `codex_running` へ進める
4. `codex_running`
   Codex worker の report 完了待ち。`bridge/outbox/codex_report.md` が出るまで待ち、生成後に `python3 scripts/bridge_orchestrator.py` を 1 回実行すると `codex_done`
5. `codex_done`
   `python3 scripts/bridge_orchestrator.py` を 1 回実行し、bridge が `archive_codex_report.py` を動かして `idle + need_chatgpt_next=true` へ進める
6. `idle + need_chatgpt_next=true`
   `python3 scripts/bridge_orchestrator.py` を 1 回実行し、bridge が `request_prompt_from_report.py` を動かして `waiting_prompt_reply` へ進める

## runtime file の扱い

- `bridge/inbox/codex_prompt.md`: fetch 済み prompt。bridge から worker への受け渡し用で、live 中は原則手編集しない
- `bridge/outbox/codex_report.md`: worker 出力。生成待ちまたは archive 対象として扱い、先回りで作成や削除をしない

## Codex worker の前提

- bridge が `bridge/inbox/codex_prompt.md` を入力として 1 回だけ起動する
- Codex は 1 フェーズだけ実装する
- Codex は `bridge/outbox/codex_report.md` を書いたら終了する
- Codex は ChatGPT へ問い合わせない
- Codex は bridge script を起動しない
- Codex は loop 継続判断をしない

## 停止条件

- Safari の current tab が違う
- 対象会話 URL でない
- 入力欄や会話領域が見つからない
- `chat_hint` が一致しない
- `state.error=true`
- Codex 実行後も report が見つからない
- `run_until_stop.py` は `pause=true`、completed 相当、`--max-steps` 到達、ユーザー中断でも止まる

## 失敗時の切り分け

- `-1712 timeout`: Safari の応答待ち、Automation 許可、current tab 固定を確認する
- 期待外れの prompt が保存された: 最新の `logs/raw_chatgpt_prompt_dump_*.txt` と `logs/sent_prompt_request*.md` を見比べる
- runtime ファイルの内容を変えてしまった: `bridge/inbox/codex_prompt.md` と `bridge/outbox/codex_report.md` を運用上の受け渡し物として扱い直す
- Codex 起動失敗: `logs/*codex_launch_prompt*`、`logs/*codex_launch_stdout*`、`logs/*codex_launch_stderr*` を見る
- `state.error=true`: 原因確認前に先へ進めない
