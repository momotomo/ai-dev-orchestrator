# Run One Cycle

この文書は、bridge が次の 1 手だけ進める運用を前提にした手順メモです。

## live 1 周前の最小チェック

- `bridge/state.json` の `error` が `false`
- `bridge/state.json` の `pause` が `false`
- `bridge/STOP` が存在しない
- Safari は事前に起動している
- ChatGPT は Safari 上でログイン済みである
- 対象プロジェクト用のチャットを Safari の現在タブに表示している
- Safari の Develop メニューで `Allow JavaScript from Apple Events` を有効にしている
- macOS の Automation 許可で、実行元から Safari 操作を許可している
- `bridge/inbox/codex_prompt.md` と `bridge/outbox/codex_report.md` は runtime 受け渡し用として扱い、live 中は原則手編集しない

ここまで問題なければ、`bridge/state.json` を見て `python3 scripts/bridge_orchestrator.py` を 1 回だけ実行する。

## 基本コマンド

- 推奨: `python3 scripts/bridge_orchestrator.py`
- 互換用: `python3 scripts/run_one_cycle.py`
- worker だけ直接起動したい場合: `python3 scripts/launch_codex_once.py`

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

## 失敗時の切り分け

- `-1712 timeout`: Safari の応答待ち、Automation 許可、current tab 固定を確認する
- 期待外れの prompt が保存された: 最新の `logs/raw_chatgpt_prompt_dump_*.txt` と `logs/sent_prompt_request*.md` を見比べる
- runtime ファイルの内容を変えてしまった: `bridge/inbox/codex_prompt.md` と `bridge/outbox/codex_report.md` を運用上の受け渡し物として扱い直す
- Codex 起動失敗: `logs/*codex_launch_prompt*`、`logs/*codex_launch_stdout*`、`logs/*codex_launch_stderr*` を見る
- `state.error=true`: 原因確認前に先へ進めない
