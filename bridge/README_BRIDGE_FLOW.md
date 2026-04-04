# Bridge Flow Guide

bridge は進行管理者、Codex は 1 回だけ動く worker という前提で運用する。
共通ルールは固定 docs に置き、毎回の prompt / report には今回差分だけを書く。

## live 1 周前の最小チェック

- Safari の current tab に対象 ChatGPT チャットを開き、live 中は別会話や別タブへ切り替えない
- `Develop > Allow JavaScript from Apple Events` と macOS の Automation 許可を済ませる
- docs 内の state 例より、実際の `bridge/state.json` を優先して読む
- `bridge/state.json` が今回の導線と矛盾していないことを確認する（`pause=false`、`error=false`、`bridge/STOP` 不在。prompt request 開始なら `mode=idle` と `need_chatgpt_prompt=true`）
- `bridge/inbox/codex_prompt.md` と `bridge/outbox/codex_report.md` は runtime 実ファイルなので、live 中は原則手編集しない
- 前回の完了報告が `bridge/outbox/codex_report.md` に残っている場合、`run_until_stop.py` は request を送る前に停止する。archive 済みか live 前の runtime かを確認する
- stale `codex_running`、未退避 report、blocked 停止がある時は、再実行より先に下の整理導線を優先する

## 通常入口

- 通常起動は `python3 scripts/start_bridge.py --project-path /path/to/target-repo --max-execution-count 6` の 1 コマンドでよい
- 初回だけ、bridge が短い例文を表示して本文入力を求める。この入力本文が初回 request の正本で、bridge は本文を改変せず固定の返答契約だけを追記して ChatGPT へ送る
- 2 回目以降は既存どおり Codex 完了報告ベースで継続する
- Safari fetch 待機は通常運用で 1800 秒前提。未完了なら追加 600 秒待機し、それでも未完了なら late completion mode で書き切りまで監視する
- `max_execution_count` は「必ずそこまで進む回数」ではなく上限。ChatGPT が `Codex 不要` を返した時は、その時点で正常停止しうる
- `run_until_stop.py` は既定で継続実行する。archive 後に次 cycle の request / fetch へ進んでも、同じ report / same request は idempotency guard で再送しない
- 通常継続では、完了報告を踏まえた次 request の前に現在チャットへ handoff を要求し、その handoff を project ページの「このプロジェクト内の新しいチャット」へ送ってローテーションする
- `human_review` は 1 回だけ自動継続し、2 回連続した時だけ `人確認待ち` に倒す

## 通常運用の 3 パターン

### 初回起動

- 入口は `python3 scripts/start_bridge.py --project-path /path/to/target-repo --max-execution-count 6`
- 初回だけ、ユーザーが ChatGPT に送る本文を入力する
- この入力本文が初回 request の正本で、bridge は本文を改変しない。内部 state や古い request 材料から補わず、送信直前に固定の返答契約だけを追記する

### 通常継続

- `ready_for_codex`、`codex_done`、`idle + need_chatgpt_next=true` など通常継続中は、同じ `start_bridge.py` を再実行してよい
- `max_execution_count` は上限なので、途中で完了や人確認待ちになれば、そこまでで正常停止しうる
- report ベース継続では、現在チャットで handoff を作ってから project 内の新しいチャットへ移る。左上のグローバル新規チャットは使わない

### handoff 再開

- `human_review` と `need_info` は blocked / error ではないので、同じ `start_bridge.py` からそのまま再開してよい
- 再開時は、初回本文を上書きせず、次の ChatGPT request に添える補足だけを入力する
- `human_review` では判断結果や方針、`need_info` では不足情報を補う
- `human_review` は 1 回だけ自動継続するため、ここへ止まるのは 2 回連続で review 要求が返った時だけ

## ChatGPT 返答の最小契約

- 通常の返答契約は `===CHATGPT_PROMPT_REPLY=== ... ===END_REPLY===` と `===CHATGPT_NO_CODEX=== ... ===END_NO_CODEX===` の 2 系統だけ
- Codex に渡す 1 フェーズ prompt がある時は `CHATGPT_PROMPT_REPLY`、今回は渡さない時は `CHATGPT_NO_CODEX` を返す
- `CHATGPT_NO_CODEX` の先頭行は `completed`、`human_review`、`need_info` のいずれかにする
- `completed` はもう Codex に渡さず完了、`human_review` は自動継続せず人判断へ渡す、`need_info` は情報不足で人入力待ち
- blocked や retry 未回復は ChatGPT 返答契約ではなく、bridge 側の `人確認待ち` / `異常` 停止として扱う

## Codex worker が最初に確認する固定 docs

- 毎回必ず先に確認するのは `bridge/codex_runner_rules.md`、`bridge/git_worker_rules.md`、`bridge/prompt_compaction_rules.md`
- その後で今回差分の正本として `bridge/inbox/codex_prompt.md` を読む
- prompt に `追加確認 docs` が書かれている時だけ、その path を追加で読む
- この順にしているので、Git ルールや worker 共通ルールを毎回の phase prompt に長く重複記載しなくてよい

## stale runtime / blocked / 未退避 report の見分け方

1. `bridge/outbox/codex_report.md` が ready なら stale ではない。`codex_done` へ進めるか archive 側を優先する
2. `state.error=true`、`pause=true`、`bridge/STOP` のどれかがあれば blocked 停止。原因を解消するまで再実行しない
3. `mode=codex_running` で report も blocked 要因も無い場合は、実 worker 継続中と決め打ちしない。まず Codex を起動した terminal がまだ意図通り動いているかを確認する
4. terminal も動いておらず、`mode=codex_running` だけが残っているなら stale runtime とみなし、安全停止として整理してから live を再開する

## stale `codex_running` の最小復帰導線

- 同じ `bridge/inbox/codex_prompt.md` をやり直すなら、`bridge/state.json` を `mode=ready_for_codex`、`need_codex_run=true`、`need_chatgpt_prompt=false`、`need_chatgpt_next=false`、`pause=false`、`error=false` に戻してから `python3 scripts/run_until_stop.py --max-steps 6 --fetch-timeout-seconds 1800 --heartbeat-seconds 10` を実行する
- 最初から prompt request をやり直すなら、`bridge/state.json` を `mode=idle`、`need_chatgpt_prompt=true`、`need_codex_run=false`、`need_chatgpt_next=false`、`pause=false`、`error=false` に戻してから `python3 scripts/bridge_orchestrator.py` を 1 回実行する
- 最初から prompt request をやり直すなら、通常入口として `python3 scripts/start_bridge.py --project-path /path/to/target-repo --max-execution-count 6` に戻してよい
- どちらの場合も `bridge/inbox/codex_prompt.md` / `bridge/outbox/codex_report.md` は原則そのまま扱い、先に未退避 report が無いことを確認する
- `run_until_stop.py` の summary で `blocked=false` かつ `stale_codex_running_candidate=true` が出た時は、自動継続ではなくこの復帰導線を優先する

## `idle + need_chatgpt_prompt=true` からの最短導線

1. `python3 scripts/start_bridge.py --project-path /path/to/target-repo --max-execution-count 6` を実行する
2. bridge が初回だけ、ChatGPT に送る本文の入力を求める。表示される例文をもとに本文を入力すると、bridge が固定の返答契約を付けて送信する
3. 以後は同じ current tab で fetch、`ready_for_codex`、Codex 実行、archive、次 request まで既存フローのまま進む

state がこの前提と違う場合は、下の `state を見た次の 1 手` を優先する。

長めに放置したい時は、`python3 scripts/run_until_stop.py --max-steps 12 --fetch-timeout-seconds 1800` のように runner 側の待機秒数を少し伸ばして使う。既存チャットをそのまま使う前提は変えない。

## 通常運用の最小 runbook

- 通常運用は `python3 scripts/start_bridge.py --project-path /path/to/target-repo --max-execution-count 6` から始めてよい
- 初回だけ、ユーザーが入力した本文を正本として受け取り、bridge が固定の返答契約だけを追記して ChatGPT へ送る。初回 request のソースオブトゥルースはその入力本文
- 2 回目以降は既存どおり Codex の完了報告ベースで次 request を継続生成する
- `run_until_stop.py` は継続実行してよいが、同じ report / same request は二重送信しない
- cycle 境界で止めたい時だけ `--stop-at-cycle-boundary` を付ける
- fetch timeout 周りは live で通常、retry 回復、retry 未回復 blocked まで確認済みなので、通常運用では追加対処なしで進めてよい
- 人確認へ切り替えるのは、Safari の current tab 前提が崩れた時か、blocked 停止で summary / note を見ても再開判断しづらい時だけでよい

## 普段見る状態表示

- 通常表示では `mode` や flag を前面に出さず、`初回依頼文の入力待ち`、`ChatGPT返答待ち`、`Codex実行中`、`人確認待ち`、`異常` などの人向け表示を優先する
- `ready_for_codex` は通常表示では `Codex実行待ち`、`codex_done` は `完了報告整理中` として扱う
- ChatGPT が `Codex 不要` を返した時は、完了なら `完了`、人判断が必要なら `人確認待ち` として扱う
- blocked 停止、pause、stale 候補は安全側で `人確認待ち`、`error=true` は `異常` として扱う
- 内部 state 名は summary や runbook の詳細確認時だけ見ればよい

## 停止時の handoff の見方

- bridge が止まったら、内部 state を追う前に `handoff:` 行と summary の `suggested_next_note` を先に見る
- `human_review` は ChatGPT が人判断へ渡した停止、`need_info` は ChatGPT が追加入力待ちと判断した停止
- blocked は ChatGPT 判断ではなく、Safari / runtime / pause / error など bridge 側事情で自動継続しない停止
- handoff 回収失敗、project ページ遷移失敗、新チャット送信失敗も blocked として止まり、人確認へ渡す
- handoff を回収済みのまま project ページ送信だけ失敗した時は、error を clear して再実行すると同じ handoff を再利用して再試行する
- `完了`、`人確認待ち`、`異常` で見るべきものが違うので、handoff の文面を優先し、必要な時だけ summary / report / logs を開く

## 停止理由の見方

- `完了`: ChatGPT が `completed` を返したか、追加の 1 手が無くなった正常終了。Codex 実行は不要
- `人確認待ち`: `human_review` / `need_info` の正常 handoff か、blocked / stale 候補の安全停止。handoff と summary の note で見分ける
- `異常`: `error=true` の停止。詳細ログを確認してから再開する
- `上限到達`: `max_execution_count` まで進めた一旦停止。summary の `suggested_next_command` をそのまま使って続けられる
- `retry 未回復 blocked`: Safari timeout などで自動継続しない停止。まず handoff と summary の note を確認する

## `human_review` / `need_info` からの再開

- `human_review` と `need_info` は blocked / error ではなく、通常入口の `python3 scripts/start_bridge.py --project-path /path/to/target-repo --max-execution-count 6` からそのまま再開してよい
- 初回 request は起動時のユーザー入力本文が正本のままで、再開時はそれを上書きせず、次の ChatGPT request に添える補足だけを入力する
- `human_review` では判断結果や方針を補い、`need_info` では不足情報を補う
- 再開用の補足はその回の request 文面にだけ載せて送る。残り滓として保存し続けず、記録が必要なら `logs/sent_prompt_request_from_report_*.md` を見る
- `human_review` は 1 回だけ自動継続する。bridge が止まっている場合は、2 回連続で review 要求が返ったか、Safari / blocked 側の理由がある
- handoff を回収済みのまま止まった時は、同じ handoff を再利用して再開するので、人が handoff 文を再入力する必要はない

## 初回入力の短い例

```text
対象案件: melody-craft-studio
対象 repo: /Users/kasuyatomohiro/projects/melody-craft-studio
現在の継続テーマ: sample browser の apply action 文言整理
狙い: sample browser 内の軽い UI polish に留め、schema / resolver / preview / playback / export は変えない
次の 1 フェーズ分の Codex 用 prompt を返してください。
```

## 初回起動と `ready_for_codex` 停止後の再起動

- 初回起動は `python3 scripts/start_bridge.py --project-path /path/to/target-repo --max-execution-count 6` を起点にし、表示された例文をもとに最初の request 本文を入力する
- `python3 scripts/run_until_stop.py` が `ready_for_codex` で止まった後は再起動扱いとし、`bridge/state.json` と summary の `suggested_next_command` / `suggested_next_note` を優先する
- summary の `suggested_next_command` が再度 `python3 scripts/run_until_stop.py ...` なら、そのまま runner を再実行してよく、`bridge_orchestrator.py` に戻す必要はない

## 新しい責務分離

- bridge: request、fetch、Codex 起動、archive、次 request、state 更新
- ChatGPT: 次の 1 フェーズ prompt 生成だけを担当
- Codex: 1 フェーズ実装と report 記入だけを担当
- 人: Safari current tab を整える、停止時に原因を見る、必要なら再実行する

## 自動と手動の境界

- 自動: ChatGPT 送信、prompt 回収、Codex 1 回起動、report archive、state 更新、logs/history 保存
- 自動: `run_until_stop.py` は `waiting_prompt_reply` 中の fetch 待ちと、`codex_running` 中の report 待ちを一定時間までは自動で続け、heartbeat を出す
- 手動: Safari の対象チャットを current tab に合わせること
- 手動: エラー時に raw dump / log / state を見て原因を直すこと
- 手動: stop 後に summary の `suggested_next_command` と `suggested_next_note` を見て再開すること
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

- `python3 scripts/start_bridge.py --project-path /path/to/target-repo --max-execution-count 6`: 通常入口
- `python3 scripts/bridge_orchestrator.py --project-path /path/to/target-repo`: 1 手ずつ詳細確認したい時だけ使う。`run_until_stop.py` は engine として残す
- `python3 scripts/run_until_stop.py --max-steps 6`: `ready_for_codex` 以降を launch / archive / 次 request まで数手まとめて進めたいときに使う
- `python3 scripts/run_until_stop.py --max-steps 12 --fetch-timeout-seconds 1800`: ChatGPT reply 待ちも含めて、ある程度放置しやすい形で回したいときに使う
- live 前 checklist はこの文書と `bridge/run_one_cycle.md` を参照する
- 1 回目起動では `run_until_stop.py` が初回本文入力を受け、その本文を正本として固定の返答契約を追記して ChatGPT へ送り、request / fetch を含めて進む
- `ready_for_codex` で止まったあとは、summary の `suggested_next_command` をそのまま再実行するのが基本
- summary の `suggested_next_command` が `なし` の場合は再実行より先に、`suggested_next_note` に書かれた blocked 原因を解消する
- どちらを使う場合も Safari の current tab は途中で切り替えない
- `bridge/inbox/codex_prompt.md` と `bridge/outbox/codex_report.md` は runtime 実ファイルなので原則手編集しない
- `run_until_stop.py` の summary には stop reason に加えて `suggested_next_command` と `suggested_next_note` が残るので、再開前にそこを確認する
- `run_until_stop.py` の summary には `blocked`、`stale_codex_running_candidate`、`fetch_retry_timeouts`、`fetch_retry_recoveries`、`safari_timeout_blocked` が残るので、retry 発生、blocked 停止、stale 候補を上段だけで追える
- `state.error=true`、`pause=true`、`bridge/STOP`、未退避 report など「先に解消が必要」な停止では `suggested_next_command` が `なし` になる
- `blocked=false` でも `stale_codex_running_candidate=true` の時は、`mode=codex_running` を実行継続中と見なさず、安全停止として先に整理する
- `fetch_next_prompt` は Safari AppleEvent timeout だけ 1 回だけ短く再試行する。それでも失敗した時は summary の note に current tab / 対象チャット / `Allow JavaScript from Apple Events` / Automation の確認点が出る

## state を見た次の 1 手

- `idle + need_chatgpt_prompt=true`: 通常は `python3 scripts/start_bridge.py --project-path /path/to/target-repo --max-execution-count 6` を実行する。1 手ずつ確認したい時だけ `python3 scripts/bridge_orchestrator.py --project-path /path/to/target-repo` を使う
- `waiting_prompt_reply`: Safari の current tab が対象チャットか確認して `python3 scripts/bridge_orchestrator.py` を 1 回実行する
- `ready_for_codex`: 1 手だけ進めるなら `python3 scripts/bridge_orchestrator.py` を 1 回実行して worker を起動する。`run_until_stop.py` の summary から再開する場合は `suggested_next_command` を優先する
- `codex_running`: 直前に起動した worker の待機中なら `bridge/outbox/codex_report.md` の生成を待つ。長時間放置後の再開で report も blocked 要因も無いなら stale 候補なので、先に上の復帰導線で整理する
- `codex_done` または `idle + need_chatgpt_next=true`: `python3 scripts/bridge_orchestrator.py` を 1 回実行する

`python3 scripts/run_until_stop.py` は、上の 1 手を `pause=true`、`error=true`、completed 相当、`--max-steps` 到達、失敗、ユーザー中断のいずれかまで繰り返す。`waiting_prompt_reply` では fetch 側の timeout まで、`codex_running` では `--codex-running-wait-seconds` の範囲で自動待機する。

## ready_for_codex の意味

- `ready_for_codex` は「人が prompt を貼る状態」ではなく「bridge が worker を起動できる状態」
- bridge 再実行で `launch_codex_once.py` が動く
- Codex は `bridge/inbox/codex_prompt.md` と固定 docs を読み、report を書いたら終了する

## 短文化の原則

- 共通ルールは `bridge/codex_runner_rules.md` や `bridge/prompt_compaction_rules.md` に置く
- ChatGPT request には state、前回 report 要点、今回ほしい出力だけを書く
- Codex prompt には今回の目的、対象、完了条件だけを書く
- Codex report には次 request 材料として必要な要点だけを書く

## 他 repo へ流用するときに変える場所

- まず `bridge/project_config.json` を開き、`project_name`、`bridge_runtime_root`、`worker_repo_path` を対象案件向けに変える
- 同居運用では `bridge_runtime_root` と `worker_repo_path` をどちらも `.` にする
- 別 repo 運用では `bridge_runtime_root` は `.` のまま、`worker_repo_path` だけ対象 repo へ向ける
- 外部 worker repo 向けの最小例は `bridge/project_config.example.json` を参照し、まず `worker_repo_path` だけを書き換える
- 旧 `repo_path` は後方互換のため `bridge_runtime_root` の alias として読む
- Codex 実行方法を変えたい場合は `codex_bin`、`codex_model`、`codex_sandbox`、`codex_timeout_seconds` を変える
- 毎回の差分は config ではなく、実行時の `--next-todo` や `bridge/inbox/codex_prompt.md` 側に載せる

## 外部 worker repo 導入 3 ステップ

1. bridge 一式を対象環境に置き、`bridge/project_config.example.json` を見ながら `bridge/project_config.json` を用意する
2. `project_name`、`worker_repo_path`、必要なら `report_request_*`、`codex_*` を対象案件向けに更新する
3. Safari current tab、Automation、`state.json` 前提を確認して、`python3 scripts/start_bridge.py --project-path /path/to/target-repo --max-execution-count 6` を実行し、表示された例文をもとに初回本文を入力する

困った時は、まず `bridge/project_config.example.json` とこの README を見直す。

## project config と browser / 環境前提の分け方

- `bridge/project_config.json` で変えるもの: `project_name`、`bridge_runtime_root`、`worker_repo_path`、必要なら `worker_repo_markers`、Codex CLI 設定、report 後 request 既定文
- `codex_sandbox` を空のままにすると bridge は `--sandbox` を渡さず、Codex の user / project `.codex/config.toml` 側の解決に委ねる。project ごとに明示上書きしたい時だけ `codex_sandbox` を入れる
- `bridge/browser_config.json` で変えるもの: `fetch_timeout_seconds`、`extended_fetch_timeout_seconds`、`poll_interval_seconds`、`apple_event_timeout_retry_count`、`apple_event_timeout_retry_delay_seconds`、`runner_heartbeat_seconds`、必要なら `chat_hint` と `project_page_url`
- 通常運用の Safari fetch 待機は 1800 秒前提で扱い、重い単一チャットでも新しいチャット自動作成には寄せない
- Safari / browser 側で毎回準備するもの: current tab、ChatGPT ログイン、`Allow JavaScript from Apple Events`、Automation 許可
- 初回 request 本文は `project_config.json` から自動生成しない。起動時のユーザー入力本文を正本として使い、bridge は固定の返答契約だけを自動付与する
- `project_config.json` は repo ごとの差分を持つ場所で、Safari の画面状態やログイン状態は持たない
- `browser_config.json` は Safari の fetch / retry / 待機秒数の調整値を持つ場所で、repo 名や worker path は持たない
- `bridge_runtime_root` は bridge runtime 実ファイルを持つ場所、`worker_repo_path` は Codex が作業する repo を指す
- 現在の実装では `bridge_runtime_root` はこの workspace root と一致している必要があり、外部 repo へ向けるのは `worker_repo_path` 側だけにする
- 旧 `reply_timeout_seconds` は後方互換で読むが、今後は `fetch_timeout_seconds` を正本として使う
- 外部 `worker_repo_path` は空でないことに加え、`.git`、`.github`、`package.json`、`pyproject.toml`、`Cargo.toml` または `worker_repo_markers` に足した file / dir 名のどれかがあると repo らしいとみなす
- `worker_repo_marker_mode=strict` は marker 不足を失敗扱いにする。通常はこちらを使う
- marker が弱いが project 固有の印はある: `worker_repo_markers` に追加して `strict` のまま通す
- `worker_repo_marker_mode=warning` は marker 不足だけ警告で続行する。正当だが custom marker も置けない repo を扱う時だけ使う
- `worker_repo_marker_mode=warning` の警告は stdout だけでなく `run_until_stop.py` の summary log にも残る
- `bridge/inbox/codex_prompt.md` と `bridge/outbox/codex_report.md` は runtime 実ファイルなので、導入時も原則手編集しない

## よくある失敗

- `Allow JavaScript from Apple Events` 未設定: Safari 送信 / 回収が止まる
- `-1712 timeout`: Safari の応答待ち、Automation 許可、current tab 固定を確認する
- `fetch_next_prompt` の Safari timeout: bridge は短い再試行を 1 回だけ行う。それでも止まったら current tab、対象チャット、`Allow JavaScript from Apple Events`、Automation を確認する
- fetch / retry / heartbeat の秒数を変えたい時は `bridge/browser_config.json` を先に見る
- 対象チャット誤り: Safari の current tab を対象会話へ戻し、必要なら `chat_hint` を設定する
- `project_config.json` の `bridge_runtime_root` 不整合: この bridge を含む現在の workspace root を指しているか確認する。通常は `.` のままでよい
- `project_config.json` の `worker_repo_path` 不整合: Codex が作業する対象 repo を指しているか確認する。同居運用なら `.` でよい
- `project_config.json` の `worker_repo_path` が空ディレクトリ: 実際の対象 repo root を指しているか確認する。外部 worker 用 sample を使うと合わせやすい
- `project_config.json` の `worker_repo_path` に repo らしい印がない: `.git`、`.github`、`package.json`、`pyproject.toml`、`Cargo.toml` か、`worker_repo_markers` に足した印がある repo root か確認する。project 固有の印があるなら `worker_repo_markers` を追加し、それも難しい時だけ `worker_repo_marker_mode=warning` を検討する
- `project_config.json` の文字列キーが空: `project_name`、`codex_bin`、`report_request_*` を埋める
- `project_config.json` の `codex_timeout_seconds` が異常: 1 以上の整数に直す
- runtime ファイルを手編集: prompt / report の受け渡し判定がぶれるため、切り分け時以外は触らない
- `state.error=true`: 原因を解消するまで先へ進めず、解消後に error をクリアする
- Codex report 未生成: `logs/*codex_launch*` を見て worker 側の失敗を切り分ける
