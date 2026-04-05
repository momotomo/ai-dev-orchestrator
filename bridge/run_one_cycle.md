# Run One Cycle

この文書は、bridge が次の 1 手だけ進める運用を前提にした手順メモです。

## live 1 周前の最小チェック

- Safari の current tab に対象 ChatGPT チャットを開き、live 中は別会話や別タブへ切り替えない
- Safari の Develop メニューで `Allow JavaScript from Apple Events` を有効にし、macOS の Automation 許可も済ませる
- docs 内の state 例より、実際の `bridge/state.json` を優先して読む
- `bridge/state.json` が今回の導線と矛盾していないことを確認する（`error=false`、`pause=false`、`bridge/STOP` 不在。prompt request 開始なら `mode=idle` と `need_chatgpt_prompt=true`）
- `bridge/inbox/codex_prompt.md` と `bridge/outbox/codex_report.md` は runtime 実ファイルなので、live 中は原則手編集しない
- 前回の完了報告が `bridge/outbox/codex_report.md` に残っている場合、`run_until_stop.py` は request を送る前に停止する。archive 済みか live 前の runtime かを確認する
- stale `codex_running`、未退避 report、blocked 停止がある時は、再実行より先に下の整理導線を優先する

ここまで問題なければ、通常は `python3 scripts/start_bridge.py --project-path /path/to/target-repo --max-execution-count 6` から始める。`bridge_orchestrator.py` は 1 手ずつ詳細確認したい時だけ使う。

## 通常入口

- 通常起動は `python3 scripts/start_bridge.py --project-path /path/to/target-repo --max-execution-count 6` の 1 コマンドでよい
- 初回だけ、bridge が短い例文を表示して本文入力を求める。この入力本文が初回 request の正本で、bridge は本文を改変せず固定の返答契約だけを追記して ChatGPT へ送る
- 2 回目以降は既存どおり Codex 完了報告ベースで継続する
- Safari fetch 待機は通常運用で 1800 秒前提。未完了なら追加 600 秒待機し、それでも未完了なら late completion mode で書き切りまで監視する
- `max_execution_count` は上限であり、ChatGPT が `Codex 不要` を返した時は途中で正常停止しうる
- `run_until_stop.py` は既定で継続実行する。archive 後の次 request / fetch へ進んでも、同じ report / same request は idempotency guard で再送しない
- report ベース継続は通常、同じチャットで続ける
- handoff / chat rotation は、1800 秒 + 600 秒を超えて late completion mode に入った reply を最後まで回収し、その reply を Codex に渡して使い切ったあと、次の ChatGPT request を送る前にだけ走る
- handoff request は、次チャットへそのまま貼る完成済みの最初のメッセージだけを返させる。要約メモは求めない
- late completion 後の handoff/new-chat 前処理は `handoff_requested` → `handoff_received` → `chat_rotated` → `sent_prompt_request_from_report*` の順で見れば追いやすい
- `sent_prompt_request_from_report_soft_wait` は、handoff 本文を新チャットへ送った可能性が高いため再送せず wait に入ったケースを表す
- `human_review` は 1 回だけ自動継続し、2 回連続した時だけ `人確認待ち` に倒す
- ChatGPT の通常返答契約は `CHATGPT_PROMPT_REPLY` と `CHATGPT_NO_CODEX` の 2 系統だけと考える
- `CHATGPT_NO_CODEX` の先頭行は `completed`、`human_review`、`need_info` のいずれかで、完了 / 人確認待ち / 情報待ちを表す

## Codex worker の docs 読み順

- fixed docs として毎回先に `bridge/codex_runner_rules.md`、`bridge/git_worker_rules.md`、`bridge/prompt_compaction_rules.md` を読む
- その後で今回差分の prompt を読む
- prompt に `追加確認 docs` がある時だけ、その path を追加で読む

## 3 つの通常パターン

- 初回起動: `run_until_stop.py` を起点にし、ユーザーが最初の request 本文を入力する
- 通常継続: `ready_for_codex`、`codex_done`、`idle + need_chatgpt_next=true` などは同じ `run_until_stop.py` を再実行して進める
- handoff 再開: `human_review` / `need_info` で止まった時も同じ `run_until_stop.py` から再開し、補足だけ入力する
- `human_review` でここへ止まるのは、bridge が 1 回自動継続した後も再度 review 要求が返った時だけ

## stale runtime の最小整理

1. `bridge/outbox/codex_report.md` が ready なら stale ではない。archive 側を優先する
2. `state.error=true`、`pause=true`、`bridge/STOP` のどれかがあれば blocked 停止。先に原因を解消する
3. `mode=codex_running` で report も blocked 要因も無い場合は、Codex を起動した terminal がまだ動いているかを確認する
4. terminal も動いていなければ stale `codex_running` とみなし、同じ prompt をやり直すなら `ready_for_codex + need_codex_run=true` に戻して `python3 scripts/run_until_stop.py --max-steps 6 --fetch-timeout-seconds 1800 --heartbeat-seconds 10`、最初から prompt request をやり直すなら通常入口の `python3 scripts/start_bridge.py --project-path /path/to/target-repo --max-execution-count 6` に戻す

## `idle + need_chatgpt_prompt=true` からの最短導線

1. `python3 scripts/start_bridge.py --project-path /path/to/target-repo --max-execution-count 6` を実行する
2. bridge が初回だけ、ChatGPT に送る本文入力を求める。表示された例文をもとに本文を入力すると、bridge が固定の返答契約を付けて送る
3. 以後は fetch、`ready_for_codex`、Codex 実行、archive、次 request を既存フローのまま進める

state がこの前提と違う場合は、下の `state を見た次の 1 手` に従う。

## 初回起動と `ready_for_codex` 停止後の再起動

- 初回起動は `python3 scripts/start_bridge.py --project-path /path/to/target-repo --max-execution-count 6` を起点にし、表示された例文をもとに最初の request 本文を入力する
- `python3 scripts/run_until_stop.py` が `ready_for_codex` で止まった後は、summary の `suggested_next_command` / `suggested_next_note` を優先する
- `final_state=ready_for_codex` でも summary の `suggested_next_command` が `python3 scripts/run_until_stop.py ...` なら、そのまま runner を再実行して worker 起動へ進める

## 基本コマンド

- 推奨: `python3 scripts/start_bridge.py --project-path /path/to/target-repo --max-execution-count 6`
- 状態だけ見たい時: `python3 scripts/start_bridge.py --status`
- 続きからそのまま進めたい時: 同じ `python3 scripts/start_bridge.py ...` を再実行。明示したい時だけ `--resume`
- 軽い診断だけ見たい時: `python3 scripts/start_bridge.py --doctor`
- bridge 側 error だけを最小解除したい時: `python3 scripts/start_bridge.py --clear-error` または `--reset`
- live 確認前に runtime を保全したい時: `python3 scripts/runtime_snapshot.py backup --dest /tmp/...`、終了後は `python3 scripts/runtime_snapshot.py restore --src /tmp/...`
- 互換用: `python3 scripts/run_one_cycle.py`
- 初回 request を 1 手ずつ確認したい場合: `python3 scripts/bridge_orchestrator.py --project-path /path/to/target-repo`
- `ready_for_codex` 以降を数手まとめて進めたい場合: `python3 scripts/run_until_stop.py --max-steps 6`
- ChatGPT reply 待ちも含めて長めに回したい場合: `python3 scripts/run_until_stop.py --max-steps 12 --fetch-timeout-seconds 1800`
- worker だけ直接起動したい場合: `python3 scripts/launch_codex_once.py`
- 1 回目起動では `idle + need_chatgpt_prompt=true` から request / fetch を含めて進む
- `ready_for_codex` で止まったら、まず summary の `suggested_next_command` / `suggested_next_note` を確認してから再実行する
- `suggested_next_command: なし` の停止は blocked 状態なので、先に note 側の原因を解消する
- `stale_codex_running_candidate=true` の停止は blocked ではないが自動継続もしない。先に stale runtime かどうか確認する

## 保守コマンドの目安

- そのまま再開でよい: `python3 scripts/start_bridge.py --resume --project-path /path/to/target-repo --max-execution-count 6`
- `human_review` / `need_info` の補足入力へ進めたい: 同じ `--resume`
- handoff 再送待ちや bridge error を先に片付けたい: `python3 scripts/start_bridge.py --clear-error --project-path /path/to/target-repo --max-execution-count 6`
- 手動 pause / `bridge/STOP` / stale 候補 / 未退避 report を先に見たい: `python3 scripts/start_bridge.py --doctor --project-path /path/to/target-repo --max-execution-count 6`
- summary に `おすすめ 1 コマンド` が出ている時は、その行を最優先に見る
- `clear-error` / `--reset` は prompt / report / handoff / logs を消さない。doctor が `clear-error 可能` の時だけ使う

## 普段見る状態表示

- 通常運用では内部 `mode` 名より、`初回依頼文の入力待ち`、`ChatGPT返答待ち`、`Codex実行待ち`、`Codex実行中`、`人確認待ち`、`異常` といった表示を優先して見る
- ChatGPT が `Codex 不要` を返した時は、完了なら `完了`、人判断が必要なら `人確認待ち` を優先して見る
- blocked 停止、pause、stale 候補は `人確認待ち`、`error=true` は `異常` として扱う
- 詳細な内部 state は summary や切り分け時だけ見ればよい
- `run_until_stop.py` が止まったら、まず `next step:` 行と summary の `suggested_next_note` を見て、内部 state は必要な時だけ開く
- `human_review` は人判断待ち、`need_info` は追加入力待ち、blocked は bridge 側事情で自動継続しない停止として見る
- handoff 回収失敗、project ページ遷移失敗、新チャット送信失敗も blocked として止まり、人確認へ渡す
- handoff を回収済みのまま project ページ送信だけ失敗した時は、error を clear して再実行すると同じ handoff を再利用し、composer 入力確認と送信確認から再試行する

## `human_review` / `need_info` の再開

- どちらも blocked / error ではないので、通常入口の `python3 scripts/start_bridge.py --project-path /path/to/target-repo --max-execution-count 6` からそのまま再開してよい
- bridge は再開時だけ短い補足入力を求め、その内容を次の ChatGPT request に添える
- 初回 request 本文は上書きせず、再開時の補足入力はその回の request 文面にだけ使う
- `human_review` は 1 回だけ自動継続する。bridge が止まっている場合は 2 回連続 review 要求か blocked 側の理由を疑う
- handoff を回収済みのまま止まった時は、人が handoff 文を再入力せずに同じ handoff で再試行できる

## 停止理由の見方

- `完了`: 追加の Codex 実行が不要な正常終了
- `人確認待ち`: `human_review` / `need_info` の正常 handoff、または blocked / stale 候補の安全停止
- `異常`: `error=true` の停止
- `上限到達`: `max_execution_count` に達した一旦停止
- retry 未回復や blocked は bridge 側事情の停止なので、hand off と summary の note を優先して見る

## 他 repo へ持っていく時の最小変更

- `bridge/project_config.json` の `project_name` を対象 repo 名に合わせる
- 同居運用では `bridge/project_config.json` の `bridge_runtime_root` と `worker_repo_path` をどちらも `"."` のまま使う
- 別 repo 運用では `bridge_runtime_root` は `"."` のまま、`worker_repo_path` だけ対象 repo root に合わせる
- 外部 worker repo 向けの最小例は `bridge/project_config.example.json` を見る。まず `worker_repo_path` を対象 repo へ合わせる
- 旧 `repo_path` は後方互換のため `bridge_runtime_root` の alias として読まれる
- report 後の request 既定文を変えたい場合は `report_request_next_todo` と `report_request_open_questions` を直す
- Codex CLI の呼び方を変えたい場合は `codex_bin`、`codex_model`、`codex_sandbox`、`codex_timeout_seconds` を直す

## 外部 worker repo 導入 3 ステップ

1. `bridge/project_config.example.json` を見ながら、対象環境の `bridge/project_config.json` を用意する
2. `project_name`、`worker_repo_path`、必要なら `report_request_*`、`codex_*` を更新する
3. Safari current tab、Automation、`state.json` 前提を確認して、`python3 scripts/start_bridge.py --project-path /path/to/target-repo --max-execution-count 6` を実行し、表示された例文をもとに初回本文を入力する

困った時は `bridge/project_config.example.json` と `bridge/README_BRIDGE_FLOW.md` を先に見る。

## project config と browser / 環境準備の境界

- `bridge/project_config.json` は repo 固有差分を置く。`project_name`、`bridge_runtime_root`、`worker_repo_path`、必要なら `worker_repo_markers`、Codex CLI 設定、request 既定文だけを持つ
- `codex_sandbox` を空のままにすると bridge は `--sandbox` を付けず、Codex の user / project `.codex/config.toml` に委ねる。project ごとに上書きしたい時だけ `codex_sandbox` を入れる
- 初回 request 本文は config や state から自動生成せず、起動時のユーザー入力本文を正本として使い、bridge は固定の返答契約だけを自動付与する
- `bridge/browser_config.json` は Safari 側の調整値を置く。`fetch_timeout_seconds`、`extended_fetch_timeout_seconds`、`poll_interval_seconds`、`apple_event_timeout_retry_count`、`apple_event_timeout_retry_delay_seconds`、`runner_heartbeat_seconds`、必要なら `chat_hint` と `project_page_url` を持つ
- 通常運用の Safari fetch 待機は 1800 秒前提で扱い、既存チャットをそのまま使う前提は変えない
- Safari current tab、ChatGPT ログイン、`Allow JavaScript from Apple Events`、Automation 許可は環境準備であり config には入れない
- `bridge_runtime_root` は bridge の runtime 実ファイルを持つ現在の workspace root、`worker_repo_path` は Codex が作業する対象 repo
- 現在の最小導入では `bridge_runtime_root` は通常 `.` のままでよく、別 repo 運用では `worker_repo_path` だけを変える
- 旧 `reply_timeout_seconds` は後方互換で読むが、fetch 待機秒数の正本は `fetch_timeout_seconds` と考える
- 外部 `worker_repo_path` は空でないことに加え、`.git`、`.github`、`package.json`、`pyproject.toml`、`Cargo.toml` か `worker_repo_markers` に足した file / dir 名のどれかを持つと repo らしいとみなす
- `worker_repo_marker_mode=strict` は marker 不足を失敗扱いにする。通常はこちらを使う
- project 固有の印があるなら、先に `worker_repo_markers` を足して `strict` のまま通す
- `worker_repo_marker_mode=warning` は marker 不足だけ警告で続行する。custom marker でも表しにくい時だけ使う
- `worker_repo_marker_mode=warning` の警告は stdout と `run_until_stop.py` の summary log の両方に出る
- config 不備で止まったら、まず `bridge/project_config.json` を直してから再実行する
- `bridge/inbox/codex_prompt.md` と `bridge/outbox/codex_report.md` は runtime 実ファイルなので、導入作業でも原則手編集しない

## 使い分け

- `idle + need_chatgpt_prompt=true` から request / fetch を 1 手ずつ見たい: `bridge_orchestrator.py --project-path /path/to/target-repo`
- `ready_for_codex` 以降を launch / archive / 次 request までまとめて進めたい: `run_until_stop.py`
- `run_until_stop.py` は `waiting_prompt_reply` 中の fetch 待ちと `codex_running` 中の report 待ちを一定時間まで自動で続け、長待ち中は heartbeat を出す
- 1 cycle ごとに止めたい時だけ `--stop-at-cycle-boundary` を使う。既定では継続実行する
- `fetch_next_prompt` の Safari AppleEvent timeout は 1 回だけ短く再試行する。それでも止まった時は summary の note に current tab / 対象チャット / `Allow JavaScript from Apple Events` / Automation の確認点が出る
- `run_until_stop.py` は常駐しない。止まる条件まで進めたら終了する
- 再開時は、止まった時点の `bridge/state.json` と summary の `suggested_next_command` / `suggested_next_note` を見て再度 `run_until_stop.py` か `bridge_orchestrator.py` を選ぶ
- summary 上段の `blocked`、`stale_codex_running_candidate`、`fetch_retry_timeouts`、`fetch_retry_recoveries`、`safari_timeout_blocked` を見ると、retry 発生、blocked 停止、stale 候補の有無を history を開かずに把握できる
- `state.error=true`、`pause=true`、`bridge/STOP`、未退避 report など「先に解消が必要」な停止では `suggested_next_command` が `なし` になる
- `blocked=false` でも `stale_codex_running_candidate=true` の時は、`mode=codex_running` を実行継続中と見なさず、先に stale runtime の整理を行う

## state を見た次の 1 手

1. `idle + need_chatgpt_prompt=true`
   `python3 scripts/start_bridge.py --project-path /path/to/target-repo --max-execution-count 6` を起動するか、1 手ずつ進めるなら `python3 scripts/bridge_orchestrator.py --project-path /path/to/target-repo` を実行する。初回だけ本文入力を求める
2. `waiting_prompt_reply`
   Safari current tab を確認してから `python3 scripts/bridge_orchestrator.py` を 1 回実行し、bridge が `fetch_next_prompt.py` を動かして `ready_for_codex` へ進める
3. `ready_for_codex`
   1 手だけ進めるなら `python3 scripts/bridge_orchestrator.py` を 1 回実行し、bridge が `launch_codex_once.py` を動かして `codex_running` へ進める。`run_until_stop.py` の summary から再開する場合は `suggested_next_command` を優先する
4. `codex_running`
   直前に起動した worker の report 完了待ち。長時間放置後の再開で report も blocked 要因も無いなら stale 候補なので、先に上の整理導線へ戻る
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
- `run_until_stop.py` は `pause=true`、completed 相当、`--max-steps` 到達、fetch 待ち timeout、`codex_running` 待ち timeout、ユーザー中断でも止まる

## 失敗時の切り分け

- `-1712 timeout`: Safari の応答待ち、Automation 許可、current tab 固定を確認する
- `fetch_next_prompt` の Safari timeout: bridge は短い再試行を 1 回だけ行う。それでも失敗したら current tab、対象チャット、`Allow JavaScript from Apple Events`、Automation を確認する
- fetch / retry / heartbeat の秒数調整は `bridge/browser_config.json` を優先して見る
- `project_config.json` が読めない: JSON 構文かトップレベル形式を修正する
- `bridge_runtime_root` 不一致: この bridge を含む現在の workspace root を指しているか確認する。通常は `.` でよい
- `worker_repo_path` 不一致: Codex が作業する対象 repo を指しているか確認する。同居運用なら `.` に戻す
- `worker_repo_path` が空ディレクトリ: 実際の対象 repo root を指しているか確認する。必要なら `bridge/project_config.example.json` を出発点にする
- `worker_repo_path` に repo らしい印がない: `.git`、`.github`、`package.json`、`pyproject.toml`、`Cargo.toml` か `worker_repo_markers` に足した印がある repo root か確認する。project 固有の印があるなら `worker_repo_markers` を追加し、それでも表しにくい時だけ `worker_repo_marker_mode=warning` を検討する
- `codex_timeout_seconds` 異常: 1 以上の整数に直す
- 期待外れの prompt が保存された: 最新の `logs/raw_chatgpt_prompt_dump_*.txt` と `logs/sent_prompt_request*.md` を見比べる
- runtime ファイルの内容を変えてしまった: `bridge/inbox/codex_prompt.md` と `bridge/outbox/codex_report.md` を運用上の受け渡し物として扱い直す
- Codex 起動失敗: `logs/*codex_launch_prompt*`、`logs/*codex_launch_stdout*`、`logs/*codex_launch_stderr*` を見る
- `state.error=true`: 原因確認前に先へ進めない
