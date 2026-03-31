# Run One Cycle

この文書は、bridge が次の 1 手だけ進める運用を前提にした手順メモです。

## 事前確認

- `bridge/state.json` の `error` が `false`
- `bridge/state.json` の `pause` が `false`
- `bridge/STOP` が存在しない
- Safari は事前に起動している
- ChatGPT は Safari 上でログイン済みである
- 対象プロジェクト用のチャットを Safari の現在タブに表示している
- Safari の Develop メニューで `Allow JavaScript from Apple Events` を有効にしている

## 基本コマンド

- 推奨: `python3 scripts/bridge_orchestrator.py`
- 互換用: `python3 scripts/run_one_cycle.py`
- worker だけ直接起動したい場合: `python3 scripts/launch_codex_once.py`

## 1 手ごとの進み方

1. `idle + need_chatgpt_prompt=true`
   bridge が `request_next_prompt.py` を実行して `waiting_prompt_reply`
2. `waiting_prompt_reply`
   bridge が `fetch_next_prompt.py` を実行して `ready_for_codex`
3. `ready_for_codex`
   bridge が `launch_codex_once.py` を実行して `codex_running`
4. `codex_running`
   Codex worker の report 完了待ち。report が見つかれば `codex_done`
5. `codex_done`
   bridge が `archive_codex_report.py` を実行して `idle + need_chatgpt_next=true`
6. `idle + need_chatgpt_next=true`
   bridge が `request_prompt_from_report.py` を実行して `waiting_prompt_reply`

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
- Codex 起動失敗: `logs/*codex_launch_prompt*`、`logs/*codex_launch_stdout*`、`logs/*codex_launch_stderr*` を見る
- `state.error=true`: 原因確認前に先へ進めない
