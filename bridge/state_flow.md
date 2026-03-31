# State Flow

## 役割

- bridge: state を見て次の 1 手を決める管理者
- ChatGPT: 次の Codex 用 1 フェーズ prompt を返す担当
- Codex: 渡された 1 フェーズだけ実装して report を書く worker
- 人: Safari current tab を整え、停止時の再開判断をする担当

## 実 mode

- `idle`
  bridge が次の依頼送信または待機を判断する基点。
- `waiting_prompt_reply`
  Safari の対象チャットへ request 済みで、ChatGPT 返答待ち。
- `ready_for_codex`
  `bridge/inbox/codex_prompt.md` がそろい、bridge が Codex worker を起動できる状態。
- `codex_running`
  bridge が Codex を 1 回起動し、report 待ちの状態。live 再開前に長く残っている時は stale runtime 候補として扱い、report / blocked 要因 / 実 worker 継続中かを先に確認する。
- `codex_done`
  report がそろい、archive へ進める状態。

## 停止系の扱い

- `pause=true`
  運用上の一時停止。bridge は次へ進めない。
- `error=true`
  原因未解消の停止。`error_message` を確認してから再開する。
- stale `codex_running`
  `mode=codex_running` のまま report が無く、`pause` / `error` / `bridge/STOP` でもない残留 state。blocked ではないが、自動継続前に人が安全な再開点へ戻す。
- `completed`
  専用 mode は置かず、`idle` かつ pending フラグなしを完了相当とみなす。

## 代表的な遷移

1. `idle` + `need_chatgpt_prompt=true`
   bridge が `request_next_prompt.py` を実行し、`waiting_prompt_reply`
2. `waiting_prompt_reply`
   bridge が `fetch_next_prompt.py` を実行し、`ready_for_codex`
3. `ready_for_codex`
   bridge が `launch_codex_once.py` を実行し、起動前に `codex_running`
4. `codex_running`
   Codex worker が `bridge/outbox/codex_report.md` を書き、bridge が `codex_done`
5. `codex_done`
   bridge が `archive_codex_report.py` を実行し、`idle` + `need_chatgpt_next=true`
6. `idle` + `need_chatgpt_next=true`
   bridge が `request_prompt_from_report.py` を実行し、`waiting_prompt_reply`

## 誰が state を動かすか

- ChatGPT は state を更新しない
- Codex は原則 state を更新しない
- bridge script が `waiting_prompt_reply`、`ready_for_codex`、`codex_running`、`codex_done` を動かす
- 失敗時は bridge が `error=true` と `error_message` を残して止まる

## Safari 前提

- Safari 起動とログインは事前準備
- 対象チャット表示も事前準備
- 送信 / 回収は Safari の current tab だけを対象にする
- current tab が違う場合は停止する
- `Develop > Allow JavaScript from Apple Events` が必要

## 再開ポイント

- `waiting_prompt_reply`: ChatGPT 返答後に bridge を再実行
- `ready_for_codex`: bridge 再実行で Codex worker を 1 回起動
- `codex_running`: 直前に起動した worker なら report が出るまで待つ。長時間放置後に report も blocked 要因も無ければ stale runtime として整理する
- `codex_done`: bridge 再実行で archive
- `error=true`: 原因を直して error を解消後に再実行

## cycle の考え方

- `cycle` は archive 完了時に 1 増える
- つまり、完了報告まで閉じた周回数を表す
