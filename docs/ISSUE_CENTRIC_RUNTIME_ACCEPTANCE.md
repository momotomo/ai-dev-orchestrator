# Issue-Centric Runtime — Acceptance & Operations Guide

この文書は issue-centric runtime の **現状の完成ライン** を明文化した正本です。  
通常運用でよい状態かどうかの acceptance checklist と、  
operator-facing stop path の完全な対応表を 1 か所にまとめています。

関連文書:
- 実行ループの契約仕様: `docs/ISSUE_CENTRIC_RUNTIME_CONTRACT.md`
- フロー設計と段階移行計画: `docs/ISSUE_CENTRIC_FLOW.md`
- 毎サイクルの手順メモ: `bridge/README_BRIDGE_FLOW.md` / `bridge/run_one_cycle.md`

---

## 1. First-party path の定義

issue-centric runtime の **first-party path** は以下の組み合わせを前提とします。

| 要素 | 値 |
|------|------|
| AI フロントエンド | ChatGPT Projects (macOS Safari) |
| コード実行 worker | Codex CLI |
| チャットセッション方針 | same-chat 継続 (既定) |
| 初回 request の source of truth | ユーザー入力本文 |
| ChatGPT reply 取得方式 | Plan A BODY base64 transport (優先) / visible DOM text (fallback) |
| 継続 request の source of truth | Codex 完了報告ベースの自動生成 |

**Plan A = BODY base64 transport** とは、ChatGPT 返答の先頭に
`===CHATGPT_DECISION_JSON=== ... ===END_DECISION_JSON===` を置き、  
本文 (issue body / codex prompt / review / followup body) は
`CHATGPT_ISSUE_BODY` / `CHATGPT_CODEX_BODY` / `CHATGPT_REVIEW` /
`CHATGPT_FOLLOWUP_ISSUE_BODY` として base64 encode して渡す contract です。

visible DOM text (legacy) contract は `===CHATGPT_PROMPT_REPLY===` /
`===CHATGPT_NO_CODEX===` ブロックで、後方互換 fallback としてのみ残ります。

---

## 2. Issue-centric runtime の実行単位

通常運用での実行単位は **1 つの `ready` issue** です。

```
open ready issue を 1 件選ぶ
  ↓
start_bridge.py --ready-issue-ref <ref>  (または起動後に ref を入力)
  ↓
ChatGPT へ initial request 送信
  ↓
fetch → Plan A reply parse → IssueCentricDecision decode
  ↓
dispatch_issue_centric_execution() で action 実行
  ↓
continuation summary → next request 準備
  ↓
同 issue が完了するまで same-chat で継続
```

- `ready` issue が 0 件の場合は `planned` backlog を見て次の bounded issue を 1 件選ぶ
- free-form 初回本文は recovery / override 用の例外経路であり、通常入口ではありません

---

## 3. fetch → execution → next request ループ概要

```
[fetch]
  fetch_next_prompt.py
    ├─ Plan A extractor (優先)
    │    CHATGPT_DECISION_JSON → IssueCentricDecision
    │    CHATGPT_*_BODY → base64 decode → materialized artifacts
    └─ fallback: visible DOM text extractor
         CHATGPT_PROMPT_REPLY / CHATGPT_NO_CODEX

[execution]
  dispatch_issue_centric_execution()
    ├─ issue_create
    ├─ codex_run          → launch_issue_centric_codex_run()
    ├─ human_review_needed → execute_human_review_action()
    ├─ no_action
    ├─ close_current_issue (各 action に附随)
    ├─ create_followup_issue (各 action に附随)
    └─ current_issue_project_state_sync (codex_run 後の lifecycle sync)

[next request]
  request_prompt_from_report.py (issue-centric preferred route)
    ├─ continuation summary の target_issue を解決
    ├─ route 選択: issue_centric / fallback_legacy
    └─ ChatGPT へ次 request 送信 → fetch ループへ戻る
```

---

## 4. Operator-facing stop path 一覧

runtime が停止した場合、`start_bridge.py --status` / `--doctor` と  
`run_until_stop.py` の stop summary で以下のいずれかが表示されます。

### 4.1 Stop path 対応表

| stop path | 状態説明 | operator 表示 | 次にすること | 入口間の一貫性 | 備考 |
|-----------|---------|--------------|------------|--------------|------|
| `initial_selection_stop` | ChatGPT が ready issue を選定し、operator に確認を求めている | `ready issue選定済み` | `--ready-issue-ref <ref>` を付けて bridge 再実行 | `--status` / `--doctor` / stop summary すべて同じ意味 | `selected_ready_issue_ref` に issue ref が書き込まれている |
| `human_review_needed` | ChatGPT が人レビュー必要と判定した | `人確認待ち` | 補足を入れて `--resume` で再実行 | すべて同じ意味 | `chatgpt_decision_note` に詳細あり。1 回は自動継続、2 回目のみ停止 |
| `codex_run_stop` | prepared Codex body があり dispatch 待ち | `Codex実行待ち` | bridge をそのまま再実行（自動進行） | すべて同じ意味 | `has_pending_issue_centric_codex_dispatch()` が True のとき |
| `legacy_contract_detected` | legacy visible-text contract だった (Plan A 非対応返答) | `人確認待ち` (または `完了`) | stop summary の note を確認してから再実行 | 通常は fallback route 経由で進む | Plan A なし → fallback 経由で処理。operator 操作なし可 |
| `completed` | 実行単位の完了。追加 Codex 不要 | `完了` | 次の ready issue を選んで新規起動 | すべて同じ意味 | `chatgpt_decision` が `no_action` 系で終端した場合 |
| `blocked / error` | `error=true` または `pause=true` または `bridge/STOP` | `異常` / `先に確認が必要です` | `--doctor` で原因確認後、`--clear-error` か手動修復 | すべて同じ意味 | `bridge/STOP` があれば `clear-error` は不可 |
| ready issue 参照で開始待ち | `mode=idle`、`need_chatgpt_prompt=true`、issue ref 未指定 | `新規開始待ち` | `--ready-issue-ref <ref>` 付きで起動 または起動後に ref を入力 | すべて同じ意味 | 通常の初回起動前状態 |
| current issue continuation | same-chat 継続中。Codex 完了報告から次 request 準備中 | `ChatGPT返答待ち` / `Codex実行待ち` / `Codex実行中` | そのまま bridge を再実行 | すべて同じ意味 | 通常継続フロー。operator 操作は不要 |
| completion followup | 完了後の followup issue 作成・close など附随ステップ | (上記 continuation と同様) | そのまま bridge を再実行 | すべて同じ意味 | `create_followup_issue` / `close_current_issue` は自動実行 |

### 4.2 Stop path から表示されるコマンド例

```
# initial_selection_stop
python3 scripts/start_bridge.py \
  --ready-issue-ref "#7" \
  --project-path /path/to/target-repo \
  --max-execution-count 6

# human_review_needed
python3 scripts/start_bridge.py \
  --resume \
  --project-path /path/to/target-repo \
  --max-execution-count 6

# codex_run_stop / 通常継続
python3 scripts/start_bridge.py \
  --project-path /path/to/target-repo \
  --max-execution-count 6

# error / blocked 解除
python3 scripts/start_bridge.py \
  --clear-error \
  --project-path /path/to/target-repo
```

---

## 5. Start / Resume / Doctor / Orchestrator から見た運用

### 5.1 start_bridge.py

| オプション | 用途 |
|-----------|------|
| `(なし)` / `--resume` | 現在の state から次の 1 手へ進む。通常再実行はこれ |
| `--status` | 内部 state を開かずに「次に何をすべきか」を確認する |
| `--doctor` | prompt / report / error / STOP を軽く診断する。runtime 変更なし |
| `--clear-error` / `--reset` | bridge 側 error / error_message だけを最小解除する |
| `--ready-issue-ref <ref>` | initial_selection_stop 後の再起動に使う |
| `--select-issue` | ChatGPT に issue 選定だけを依頼する初回選定モード |

### 5.2 run_until_stop.py

- 複数手をまとめて実行するランナー
- `--status` 相当の stop summary を常に出力する
- `suggested_next_note` / `recommended_operator_step` / `suggested_next_command` が  
  `initial_selection_stop` / `human_review_needed` / `codex_run_stop` を区別して適切なコマンドを返す

### 5.3 bridge_orchestrator.py

- 1 手だけ進めて返す低レベル orchestrator
- IC stop path (`initial_selection_stop` / `human_review_needed`) のとき、  
  generic な plan note ではなく `chatgpt_decision_note` を優先して出力する (Phase 45 適用済み)
- 通常は `start_bridge.py` から間接的に呼ばれる

### 5.4 doctor の診断項目

`start_bridge.py --doctor` が確認する項目:

- `status_label` (現在の状況)
- `recommendation_label` / `recommended_command` (おすすめ 1 コマンド)
- `guidance` / `note` (次に起きること / まず見るもの)
- `prompt_ready` / `report_ready` / `stop_file` / `pause` / `bridge_error`
- `pending_request_log` / `pending_handoff_log`
- `lifecycle_sync_state`
- `clear_error` 可否

---

## 6. 残る legacy compat 境界

以下は意図的な後方互換として残しています。今フェーズで変更しません。

| 項目 | 現在の扱い |
|------|----------|
| `===CHATGPT_PROMPT_REPLY===` / `===CHATGPT_NO_CODEX===` | legacy fallback として継続サポート |
| `mode` フィールド | 後方互換のため維持。read-side は `last_issue_centric_state_view` 等を優先 |
| free-form 初回本文 override | 例外経路として維持。通常入口は `--ready-issue-ref` |
| `codex_run + close_current_issue = true` 組み合わせ | Phase 47 で post-launch narrow close path を実装 (trigger → launch → close) |
| multi-flag 組み合わせの制限 | narrow execution matrix の範囲内のみサポート |

---

## 7. Acceptance Checklist

「今どこまで完成していて、通常運用で何を確認すればよいか」の checklist です。

### A. Issue-centric contract

- [x] `parse_issue_centric_reply()` が DECISION_JSON block を parse できる
- [x] `maybe_parse_issue_centric_reply()` で valid / invalid / not_present を区別できる
- [x] `target_issue` format validation が `42` / `#42` / `owner/repo#42` / URL を受け付ける
- [x] unknown action は dispatcher がブロックし、known combination のみ実行する

### B. Outbound (fetch / Plan A)

- [x] `fetch_next_prompt.py` が Plan A extractor を優先し、失敗時のみ visible DOM fallback に落ちる
- [x] `_IcFetchOutcome` で `path` / `stop_message` / `selected_issue_ref` を整理済み
- [x] `_resolve_ic_fetch_outcome()` が fetch 結果から stop path を判定できる
- [x] `_apply_ic_fetch_stop_state()` が `initial_selection_stop` 時に `selected_ready_issue_ref` を書き込む

### C. Execution / dispatch

- [x] `dispatch_issue_centric_execution()` が narrow execution matrix を実行できる
- [x] `issue_create` / `codex_run` / `human_review_needed` / `no_action` が実行できる
- [x] `close_current_issue` / `create_followup_issue` の附随ステップが動作する
- [x] `current_issue_project_state_sync` が lifecycle sync を実行できる
- [x] `no_action` issue-management slices (Phase 49)
  - `no_action + create_followup_issue` → `no_action_followup` (follow-up issue create)
  - `no_action + close_current_issue` → `no_action_close` (current issue close)
  - `no_action + create_followup_issue + close_current_issue` → `no_action_followup_then_close` (follow-up → close の順)
  - close は follow-up より先に実行しない
  - `_resolve_no_action_matrix_path()` helper で 4 パスが名前で読める
  - continuation state: followup ありの場合は followup が principal_issue に昇格、close のみの場合は closed issue を next cycle target に残さない
- [x] `issue_create + close_current_issue` narrow path (Phase 50)
  - 実行順: `issue_create → close_current_issue`
  - create が失敗した場合は close を実行しない
  - created primary issue が `principal_issue` / `principal_issue_kind = primary_issue` になる
  - closed current issue は次 cycle target に残らない
  - `_resolve_issue_create_matrix_path()` helper で 4 パス (`issue_create` / `issue_create_followup` / `issue_create_then_close` / `issue_create_followup_then_close`) が名前で読める
- [x] `issue_create + create_followup_issue + close_current_issue` 3 段 path (Phase 51)
  - 実行順: `issue_create → followup_issue_create → close_current_issue` で固定
  - primary create 失敗時は followup も close も実行しない
  - followup create 失敗時は close を実行しない
  - close は `allow_issue_create_followup_close=True` で既存 narrow close helper 経由
  - followup issue が `principal_issue` / `principal_issue_kind = followup_issue` になる (current closed → next cycle principal にしない)
  - primary issue は `last_issue_centric_primary_issue_number` に保持
- [x] `codex_run + create_followup_issue` → `codex_run_followup` (Phase 52)
  - 実行順: `codex trigger → codex launch/continuation → followup_issue_create`
  - codex が `completed` のときのみ followup に進む
  - followup issue URL/number が `last_issue_centric_followup_issue_*` に記録される
  - `_resolve_codex_run_matrix_path(True, False)` == `"codex_run_followup"`
- [x] `codex_run + create_followup_issue + close_current_issue` → `codex_run_followup_then_close` (Phase 52)
  - 実行順: `codex trigger → codex launch/continuation → followup_issue_create → close_current_issue`
  - codex launch が `completed` のときのみ followup に進む
  - followup が `completed` のときのみ close に進む
  - close は `allow_codex_run_followup_close=True` で既存 narrow close helper 経由
  - followup issue が `principal_issue` / `principal_issue_kind = followup_issue` になる
  - closed current issue は次 cycle principal / target に残らない
  - `_resolve_codex_run_matrix_path(True, True)` == `"codex_run_followup_then_close"`

### D. Next request / continuation

- [x] `_IcNextCycleContext` で continuation summary から next request context を整理済み
- [x] `request_prompt_from_report.py` が issue-centric preferred route で次 request を準備できる
- [x] completion followup / recovery が coherent summary ベースで再開できる
- [x] restart-safe recovery / rehydration が正しく機能する

### D2. Project sync state family coverage (Phase 53)

supported slices に対して、3 つの project-sync state family が一貫したルールで読めるようになった。

**状態値の意味**:
| 値 | 意味 |
|---|---|
| `not_requested_no_project` | GitHub Project URL が未設定 |
| `issue_only_fallback` | Project は設定済みだが project state field は更新されていない (issue 作成のみ) |
| `project_state_synced` | Project state field が正常に更新された |
| `project_state_sync_failed` | Project 更新を試みたが失敗した |

**state family**:
| family | state keys | 設定タイミング |
|---|---|---|
| `primary_project_*` | `last_issue_centric_primary_project_sync_status` 他 4 keys | `issue_create` 実行後 |
| `followup_project_*` | `last_issue_centric_followup_project_sync_status` 他 4 keys | `followup_issue_create` 実行後 |
| `lifecycle_sync_*` | `last_issue_centric_lifecycle_sync_status` 他 5 keys | `current_issue_project_state_sync` 実行後 |

**helper**:
- `_normalize_project_sync_result(execution)` — 任意の execution result から 5 project-sync フィールドを正規化して dict で返す
- 3 state family すべてが同じ vocabulary (`project_sync_status` / `project_url` / `project_item_id` / `project_state_field_name` / `project_state_value_name`) を使う

**coverage 確認 (IcProjectSyncStateFamilyTests)**:
- `_normalize_project_sync_result` helper が 3 パターン (no-project / fallback / synced) で正しく動作する
- `issue_create` / `issue_create_followup_then_close` で `primary_project_*` / `followup_project_*` が独立して設定される
- `no_action_followup` / `codex_run_followup` で `followup_project_*` が no-project / fallback / synced で読める
- `codex_run` / `codex_run_followup_then_close` で `lifecycle_sync_*` が in_progress → done の順で記録される
- `lifecycle_sync_*` と `followup_project_*` が別 family として混ざらない

**未対応**:
- project sync の実際の外部 API 呼び出し精度向上 (execution 関数側の問題)
- `project_state_sync_failed` パスの retry / fallback ロジック
### D3. Project sync execution precision / failed-path hardening (Phase 54)

`_normalize_project_sync_result` と 3 つの `_apply_*` helper の語彙を揃え、lifecycle sync に narrow 1-retry を追加した。

**失敗語彙の確定**:

| 値 | 発生条件 | retry 可否 |
|---|---|---|
| `not_requested_no_project` | GitHub Project URL 未設定 | retry なし (no-op) |
| `issue_only_fallback` | Project 設定済み、issue 作成のみ (field 更新未実施) | retry なし (deliberate) |
| `project_state_synced` | Project state field 更新成功 | retry 不要 |
| `project_state_sync_failed` | Project state field 更新失敗 | **lifecycle sync のみ 1 retry** |

**実装内容**:
- `_normalize_project_sync_result(execution)` — `project_sync_status` 優先、なければ `sync_status` に fallback (lifecycle sync fn compatibility)
- `_should_retry_project_sync(result)` — `project_state_sync_failed` のみ True を返す helper
- `_apply_issue_create_execution_state` — `_normalize_project_sync_result` 経由に統一
- `_apply_followup_execution_state` — `_normalize_project_sync_result` 経由に統一
- `_apply_current_issue_project_state_sync_state` — `_normalize_project_sync_result` 経由に統一 (sync_status fallback で lifecycle sync fn と互換)
- `_run_current_issue_project_state_sync` — 1st attempt が `project_state_sync_failed` なら 1 度だけ retry

**retry 方針 (narrow)**:
- retry するのは `lifecycle sync` (`_run_current_issue_project_state_sync`) のみ。理由: lifecycle sync は standalone callable なので retry 可能
- `issue_create` / `followup_create` の project sync は execution fn に埋め込みのため retry 不可。`project_state_sync_failed` はそのまま state family に書く
- `issue_only_fallback` は retry しない (deliberate — project field update 未実施)
- `not_requested_no_project` は retry しない (no-op — project URL 未設定)
- 最大 retry 回数: 1 回。2 度目以降の失敗は state に `project_state_sync_failed` として残る

**coverage 確認 (IcProjectSyncFailedPathTests, 32 tests)**:
- `_normalize_project_sync_result` が `sync_status` fallback / `project_sync_status` 優先 を正しく扱う
- `_should_retry_project_sync` が 4 パターン (failed/not_requested/fallback/synced) で正しく判定
- `project_state_sync_failed` が primary_project_* / followup_project_* / lifecycle_sync_* の 3 family で同じ語彙
- lifecycle sync が fail-then-succeed で 2 コール (1 attempt + 1 retry) を確認
- lifecycle sync が success では 1 コール (retry なし) を確認
- `not_requested_no_project` と `project_state_sync_failed` が state で区別可能
- `issue_only_fallback` は issue-create dispatcher レベルで retry されない (call 数 = 1)
- Phase 53 の `sync_status` 属性を使う lifecycle sync fn との後方互換を確認
- supported slices の matrix_path / final_status が回帰しない

**未対応**:
- `project_state_sync_failed` の operator 通知 / alert
- multi-retry (2 retry 以上) の設計 — 今回は意図的に 1 retry に留める

### D4. project sync 外部 GitHub API mutation 実装精度 (Phase 55)

`execute_issue_create_draft` / `execute_current_issue_project_state_sync` の内部語彙を Phase 53 標準 4-value vocabulary に完全統一し、mutation helper を整理した。

**修正内容**:

1. **`issue_centric_issue_create.py`**:
   - `resolved_project is None` (project URL 未設定) の返り値を `"issue_only_fallback"` → `"not_requested_no_project"` に修正
     - 旧: `"issue_only_fallback"` は「project 設定済みで field 更新省略」を意味する語彙 — 混用していた
     - 新: `"not_requested_no_project"` = project URL が存在しない → no-op
   - `issue_create_project_sync_signal()` に `"not_requested_no_project"` を `"skipped_no_project"` へのマッピングを追加
   - `_map_project_mutation_result_to_sync_status(internal_status)` helper 追加
     - 内部細粒度ステータス → 標準 4 値語彙へのマッピング集中管理

2. **`issue_centric_current_issue_project_state.py`**:
   - `except (IssueCentricCurrentIssueProjectStateError, IssueCentricGitHubError)` → `sync_status = "blocked_project_state_sync"` を `"project_state_sync_failed"` に修正
   - `except Exception` → `sync_status = "failed_project_state_sync"` を `"project_state_sync_failed"` に修正
   - `_ensure_project_item_for_issue(...)` helper 追加 — cache hit/miss 分岐 + resolver 呼び出しを集約
   - `_sync_project_state_field(...)` helper 追加 — state setter 呼び出しを集約

**`_map_project_mutation_result_to_sync_status` マッピング**:

| 内部ステータス | 標準語彙 |
|---|---|
| `"not_requested_no_project"`, `"not_requested"` | `"not_requested_no_project"` |
| `"issue_only_fallback"` | `"issue_only_fallback"` |
| `"project_state_synced"` | `"project_state_synced"` |
| `"issue_created_project_item_failed"`, `"issue_created_project_state_failed"`, その他 | `"project_state_sync_failed"` |

**coverage 確認 (IcProjectSyncGitHubMutationTests, 21 tests, 4 subtests)**:
- `_map_project_mutation_result_to_sync_status` が全パターンを正しくマッピング
- `execute_issue_create_draft`: no-project → `not_requested_no_project` / item fail → `issue_created_project_item_failed` / state fail → `issue_created_project_state_failed` / 全成功 → `project_state_synced`
- `_ensure_project_item_for_issue`: cache hit → resolver 未呼び出し / cache miss → resolver 呼び出し
- `_sync_project_state_field`: setter が正しい引数で呼ばれる
- `execute_current_issue_project_state_sync`: no-project → `not_requested_no_project` / 全成功 → `project_state_synced` / item fail → `project_state_sync_failed` / state fail → `project_state_sync_failed`
- no-project 語彙が issue_create / lifecycle sync で統一されている
- sync_failed 語彙が issue_create / lifecycle sync で統一されている
- `issue_create_project_sync_signal()` が全 4 語彙を正しく 3-signal に変換

**未対応**:
- `project_state_sync_failed` の operator alert delivery (外部通知)
- multi-retry (2 retry 以上) の設計 — 今回は意図的に 1 retry に留める

### D5. project_state_sync_failed operator-facing warning surface (Phase 56)

`project_state_sync_failed` を operator が読める形で surface し、hard error / deliberate stop / success with warning の 3 種を明確に区別するようにした。

**追加した helpers** (`scripts/_bridge_common.py`):

| helper | 役割 |
|---|---|
| `_detect_project_sync_warning(state)` | primary / followup / lifecycle のいずれかが `project_state_sync_failed` か判定 |
| `_resolve_project_sync_warning_family(state)` | 失敗 family 名の list を返す |
| `_build_project_sync_warning_note(state)` | primary / followup の失敗を inline warning note として返す (lifecycle は `_bridge_lifecycle_sync_suffix` が担当) |
| `bridge_project_sync_warning_suffix(state)` | `_build_project_sync_warning_note` の public wrapper |
| `format_project_sync_warning_note(state)` | 全 3 family カバーの stop summary 用診断 note |

**更新した operator-facing surface**:

- `format_operator_stop_note()` (completed path): `_bridge_lifecycle_sync_suffix` + `_build_project_sync_warning_note`
- `suggested_next_note()` (completed action): 同様に `bridge_project_sync_warning_suffix` を追加
- `summarize_run()` stop summary: `project_sync_warning:` 行を追加

**severity 設計 (注意付き成功)**:
- `project_state_sync_failed` だけでは runtime 全体を hard error にしない
- `error: True` (hard error) / IC stop paths (codex_run_stop / initial_selection_stop / human_review_needed) / project sync warning は相互に独立した語彙・経路

**coverage 確認 (ProjectSyncWarningOperatorSurfaceTests, 48 tests)**:
- `_detect_project_sync_warning` / `_resolve_project_sync_warning_family` の全パターン
- `bridge_project_sync_warning_suffix`: primary/followup 失敗 → warning あり / lifecycle のみ → empty / no-project / fallback / synced → empty
- `format_project_sync_warning_note`: 全 3 family それぞれ + all-3 + none
- `format_operator_stop_note` completed: 失敗 → warning 出現、no-project/fallback/synced → なし
- `suggested_next_note` completed: 同上
- error 状態は warning と混在しない / 既存 lifecycle sync は回帰しない

**未対応**:
- `project_state_sync_failed` の operator alert delivery (外部通知)
- multi-retry (2 retry 以上) の設計

### D6. project_state_sync_failed warning — 残り operator-facing surface への伝播 (Phase 57)

Phase 56 で `_bridge_common.py` / `run_until_stop.py` に追加した warning surface を、
オペレータが日常的に使う残り 3 入口 (`bridge_orchestrator.py` / `start_bridge.py` / `run_until_stop.entry_guidance`) へ揃えて反映した。

**変更した operator-facing surface**:

| 入口 | 変更内容 |
|---|---|
| `bridge_orchestrator.run()` completed path | `format_operator_stop_note(state, plan=plan)` を使用 (project sync warning 付加) |
| `start_bridge.print_doctor()` | `format_project_sync_warning_note(state)` の `project_sync_warning:` 行を追加 |
| `run_until_stop.entry_guidance()` completed path | `bridge_project_sync_warning_suffix(state)` を追加 |

**設計制約 (回帰防止)**:
- `bridge_orchestrator.run()` は `plan.next_action == "completed"` のときのみ `format_operator_stop_note` を使用。他の `next_action` (request_prompt_from_report / fetch_next_prompt 等) は引き続き `plan.note` を使用し、既存テストと語彙を維持
- IC stop paths (initial_selection_stop / human_review_needed) は変更なし — IC branch は `_ic_note or plan.note` を継続使用
- `not_requested_no_project` / `issue_only_fallback` → warning なし
- hard error (`error=True`) → `present_bridge_status.label = "異常"` は維持。warning は `format_operator_stop_note` の別信号として独立

**coverage 確認 (ProjectSyncWarningSurfaceAlignmentTests, 22 tests)**:
- `bridge_orchestrator` completed: primary/followup 失敗 → warning あり / no-project / fallback / synced → なし / IC stop is unchanged
- `start_bridge` doctor: `project_sync_warning:` 行 → primary 失敗 / lifecycle 失敗 / 失敗なし (none)
- `entry_guidance` completed: primary/followup 失敗 → warning あり / lifecycle のみ → empty (_pw は primary+followup のみ) / no-project / fallback / synced → なし
- vocabulary は 3 surface で揃っている
- hard error / IC stop は project_sync warning と混在しない
- Phase 56 の surfaces は回帰しない

**未対応**:
- `project_state_sync_failed` の alert delivery 前段 → Phase 58 で alert signal / payload / dedupe 実装済み
- `project_state_sync_failed` の実際の外部通知 delivery (Slack / Discord / email 等)
- multi-retry (2 retry 以上) の設計

### D7. project_state_sync_failed — alert signal / payload / dedupe (Phase 58)

Phase 57 までで warning surface を揃えた後、alert delivery の前段として runtime 内部で  
alert を一意に検出・記録・再送可能にする signal / payload / log を整えた。

**追加した helpers (`_bridge_common.py`)**:

| helper / 定数 | 役割 |
|---|---|
| `ALERT_PAYLOAD_PATH` | alert payload artifact のパス (`bridge/project_sync_alert.json`) |
| `_ALERT_FAMILY_STATE_KEYS` | family ごとの state キーマップ (primary / followup / lifecycle) |
| `_ProjectSyncAlertCandidate` dataclass | 検出した alert candidate の内部表現 |
| `_detect_project_sync_alert_candidate(state)` | alert candidate の唯一の検出ゲート |
| `_build_project_sync_alert_candidate(state)` | state から candidate を構築 (失敗なし → None) |
| `_build_project_sync_alert_payload(candidate)` | candidate → JSON-serializable dict |
| `record_project_sync_alert_if_new(state)` | 新規 alert を記録 (dedupe あり) → "recorded" / "skipped_duplicate" / "none" |
| `format_project_sync_alert_status(state)` | doctor 表示用 "pending file=..." / "none" |

**DEFAULT_STATE 追加キー**:
- `last_project_sync_alert_status` — "pending" / ""
- `last_project_sync_alert_hash` — stable dedupe hash (key identity fields の SHA-256)
- `last_project_sync_alert_file` — 最後に書いた payload file のパス

**alert candidate の boundary**:
- `project_state_sync_failed` のみが alert candidate
- `not_requested_no_project` / `issue_only_fallback` → alert candidate にならない
- hard error (`error=True`) / deliberate stop (IC stop paths) は alert candidate と独立した別信号

**dedupe の考え方**:
- `alert_hash` = SHA-256 of `sorted_families|sync_status|issue_ref|project_url|project_item_id|project_state_value`
- 同じ state → 同じ hash → `skipped_duplicate` (payload 再生成しない)
- 異なる issue / project_url / state_value → 異なる hash → 新規 payload 生成

**operator-facing surface**:
- `start_bridge.print_doctor()` に `project_sync_alert: pending file=.../none` 行追加
- `run_until_stop.finish()` で `record_project_sync_alert_if_new(final_state)` を呼び出し (runs 終了時に自動記録)

**coverage 確認 (ProjectSyncAlertSignalPayloadTests, 35 tests)**:
- primary / followup / lifecycle sync failed → alert candidate 検出 ✓
- not_requested_no_project / issue_only_fallback → alert candidate にならない ✓
- hard error + sync failed → 2 つは独立信号 ✓
- stable hash (same state → same hash / different event → different hash) ✓
- payload contains required JSON fields / JSON serializable ✓
- record "none" / "skipped_duplicate" / "recorded" ✓
- doctor shows project_sync_alert pending/none ✓
- warning surface と alert candidate の一貫性 ✓
- Phase 57 回帰なし ✓

**未対応**:
- `project_state_sync_failed` の実際の外部通知 delivery (Slack / Discord / email 等) → Phase 59 で generic webhook delivery 実装済み
- multi-retry (2 retry 以上) の設計

### D8. project_state_sync_failed — generic webhook delivery (Phase 59)

Phase 58 で記録した alert payload を generic webhook (JSON POST) で送信する 1-transport delivery を実装した。
Phase 60 でこれを bounded multi-retry に拡張した（D9 参照）。

**追加した helpers (`_bridge_common.py`)**:

| helper / 定数 | 役割 |
|---|---|
| `_WEBHOOK_SUCCESS_CODES` | HTTP 2xx range (frozenset) |
| `_WEBHOOK_TIMEOUT_SECONDS` | HTTP タイムアウト (10 秒) |
| `_deliver_project_sync_alert_to_webhook(payload, url)` | JSON POST 低レベル transport (1 attempt)。tuple を返す |
| `deliver_project_sync_alert_if_pending(state, config)` | 高レベル delivery gate (dedupe / guard / retry 込み) |
| `format_project_sync_alert_delivery_status(state)` | doctor 表示用 "delivered hash=... attempts=N" / "delivery_failed attempts=N error=..." / "none" |

**DEFAULT_STATE 追加キー (Phase 59)**:
- `last_project_sync_alert_delivery_status` — "delivered" / "delivery_failed" / "not_requested_no_webhook" / "skipped_already_delivered" / "invalid_payload" / ""
- `last_project_sync_alert_delivery_hash` — 成功時に保存する alert hash (dedupe キー)
- `last_project_sync_alert_delivery_attempted_at` — 最後の試行 UTC タイムスタンプ
- `last_project_sync_alert_delivery_error` — 失敗時エラー文字列 (最大 200 文字)
- `last_project_sync_alert_delivery_url` — 試行した webhook URL

**DEFAULT_PROJECT_CONFIG 追加キー**:
- `project_sync_alert_webhook_url` — POST 先 URL (空文字列 = delivery 無効)

**return value vocabulary**:
- `"none"` — pending alert なし
- `"not_requested_no_webhook"` — webhook URL 未設定
- `"skipped_already_delivered"` — 同一 hash を既に deliver 済み
- `"delivered"` — HTTP 2xx 成功 (任意の attempt で成功)
- `"delivery_failed"` — 全 attempt 失敗 (hard error にならない)
- `"invalid_payload"` — payload ファイル未存在 / JSON 無効

**design constraints**:
- delivery failure は絶対に hard runtime error にしない (`mark_error()` を呼ばない)
- 1 run 1 回まで (同じ alert hash → skipped_already_delivered)
- HTTP タイムアウト 10 秒
- 外部 transport: `urllib.request` + `urllib.error` のみ (標準ライブラリ)

**operator-facing surface 追加**:
- `start_bridge.print_doctor()` に `project_sync_alert_delivery:` 行追加
- `run_until_stop.finish()` で `deliver_project_sync_alert_if_pending(final_state, config)` を呼び出し (alert 記録後に即座に delivery 試行)

**coverage 確認 (ProjectSyncAlertWebhookDeliveryTests)**:
- no pending alert → "none" ✓
- webhook URL 未設定 / config=None → "not_requested_no_webhook" ✓
- same hash already delivered → "skipped_already_delivered" ✓
- file missing → "invalid_payload" ✓
- JSON broken → "invalid_payload" ✓
- unreachable URL → "delivery_failed" (non-raising) ✓
- mock HTTP 200 → "delivered"; body correct ✓
- mock HTTP 500 → "delivery_failed" ✓
- format_project_sync_alert_delivery_status delivered / delivery_failed / not_requested / skipped ✓
- DEFAULT_STATE has 5 delivery keys ✓
- DEFAULT_PROJECT_CONFIG has webhook_url key ✓
- doctor output includes delivery line ✓
- Phase 58 regression: record_project_sync_alert_if_new still works ✓
- double-delivery prevention ✓

### D9. project_state_sync_failed — bounded multi-retry for webhook delivery (Phase 60)

Phase 59 の 1-attempt delivery を **最大 3 attempts の bounded retry** に拡張した。

**追加した helpers / constants (`_bridge_common.py`)**:

| helper / 定数 | 役割 |
|---|---|
| `_WEBHOOK_MAX_ATTEMPTS = 3` | 最大試行回数 (初回 + retry 2 回) |
| `_WEBHOOK_RETRY_DELAYS_SECONDS = (1, 3)` | attempt 間の固定待機秒数 |
| `_deliver_project_sync_alert_with_retry(payload, url, ...)` | retry policy を回す中間層 (1~3 attempt, tuple を返す) |

**retry 設計**:
- `_deliver_project_sync_alert_to_webhook` (1 attempt) は変更なし
- `_deliver_project_sync_alert_with_retry` が retry loop を担う
- `deliver_project_sync_alert_if_pending` が gate + dedupe + state 更新を担う
- retry 対象: `delivery_failed` のみ
- retry しない: `none` / `not_requested_no_webhook` / `skipped_already_delivered` / `invalid_payload`
- 遅延: 1 秒 → 3 秒 (固定。exponential backoff / jitter なし)
- 無限 retry 禁止。1 run 内で完結

**state 追加キー (Phase 60)**:
- `last_project_sync_alert_delivery_attempt_count` (int, default 0) — 今回の run で何回試みたか

**return value vocabulary**:
- 変更なし。`delivered` / `delivery_failed` で最終結果を表す (retry したことは attempt_count で追える)

**doctor 表示更新**:
- `format_project_sync_alert_delivery_status` に `attempts=N` suffix を追加
  - 例: `delivered hash=abc123xyz789 attempts=1`
  - 例: `delivery_failed attempts=3 error=URLError: ...`

**coverage 確認 (ProjectSyncAlertBoundedRetryTests)**:
- 1 回目成功 → "delivered" attempts=1 ✓
- 1 回目失敗 / 2 回目成功 → "delivered" attempts=2 ✓
- 1・2 回目失敗 / 3 回目成功 → "delivered" attempts=3 ✓
- 3 回とも失敗 → "delivery_failed" attempts=3 ✓
- max_attempts を超えてトランスポートを呼ばない ✓
- sleep 回数 / 秒数が仕様どおり ✓
- 最終 attempt 後に不要な sleep を呼ばない ✓
- attempt_count が state に保存される ✓
- not_requested_no_webhook / invalid_payload / skipped_already_delivered は retry しない ✓
- format_status に attempts= suffix ✓
- all-attempts-failed でも例外なし (hard error にならない) ✓
- DEFAULT_STATE に attempt_count key ✓
- Phase 59 dedupe 回帰なし ✓
- integration: HTTP 200 → delivered attempts=1 ✓

### D10. project_sync_alert — webhook config validation / doctor guidance / runbook hardening (Phase 61)

Phase 59-60 で整えた webhook delivery / retry を前提に、**設定ミスを実運用前に検出しやすくする** hardening を追加した。

**追加した helpers (`_bridge_common.py`)**:

| helper | 役割 |
|---|---|
| `validate_project_sync_alert_webhook_url(url)` | 静的 URL 検証。`(bool, reason)` を返す。例外なし |
| `format_project_sync_alert_webhook_config_note(config)` | doctor 表示用の config 状態文字列を返す |

**`validate_project_sync_alert_webhook_url` の設計**:
- 空文字列 → `(True, "")` — delivery 無効扱い (config error ではない)
- `http://` / `https://` 以外の scheme → `(False, "unsupported scheme '...'")`
- scheme のみで host なし (`https://` のみ) → `(False, "URL starts with '...' but has no host")`
- 前後空白はstrip後に検証
- 疎通確認なし (静的チェックのみ)、例外なし

**`format_project_sync_alert_webhook_config_note` の返値**:
- `"disabled (no URL set)"` — URL 未設定 (delivery 無効)
- `"ok url=https://..."` — URL 有効
- `"config_warning: <reason>"` — URL 不正 (static check 失敗)

**doctor 更新 (`start_bridge.py`)**:
- `print_doctor()` に `project_sync_alert_webhook_config:` 行を追加
- `project_sync_alert` / `project_sync_alert_delivery` / `project_sync_alert_webhook_config` の 3 行が独立して表示される
- config_warning が出ていても runtime hard error にならない (delivery は skip or attempt される)

**signal boundary の維持**:
- `runtime_hard_error` / `project_sync_warning` / `alert_delivery_failed` / `webhook_config_warning` は4つの独立した信号
- config invalid → `config_warning` (delivery path を変えない)
- delivery_failed → `delivery_failed` (config を変えない)
- skipped_already_delivered → 正常な dedupe 信号 (warning 扱いしない)

**coverage 確認 (ProjectSyncAlertWebhookConfigValidationTests)**:
- 空 URL → (True, "") ✓
- 前後空白のみ → (True, "") ✓
- https:// 有効 URL → (True, "") ✓
- http:// 有効 URL → (True, "") ✓
- https:// のみ (host なし) → (False, ...) ✓
- ftp:// → invalid_scheme → (False, ...) ✓
- 前後空白付き有効 URL → trim 後 valid → (True, "") ✓
- format_note: 空 URL → disabled ✓
- format_note: https:// 有効 → "ok url=..." ✓
- format_note: ftp:// → "config_warning: ..." ✓
- doctor source に project_sync_alert_webhook_config 行が含まれる ✓
- delivery_failed と config_invalid が混在しない ✓
- skipped_already_delivered は warning 扱いしない ✓
- Phase 59-60 の delivery / retry / dedupe は回帰しない ✓

### E. Operator-facing surface

- [x] `detect_ic_stop_path()` が `codex_run_stop` / `initial_selection_stop` / `human_review_needed` / `""` を正しく返す
- [x] `present_bridge_status()` が IC stop path で適切な label / detail を返す
  - `initial_selection_stop` → `ready issue選定済み`
  - `human_review_needed` → `人確認待ち`
  - `codex_run_stop` → `Codex実行待ち`
- [x] `present_bridge_handoff()` が `human_review_needed` IC で適切な title を返す
- [x] `format_operator_stop_note()` が IC stop path で `chatgpt_decision_note` を優先する
- [x] `recommended_operator_step()` が IC stop path で適切なコマンドを返す
- [x] `suggested_next_note()` が IC stop path で適切な note を返す

### F. Start / Resume / Doctor / Orchestrator 整合

- [x] `start_bridge.py --status` が IC stop path を適切に表示する
- [x] `start_bridge.py --doctor` が IC stop path の `recommendation_label` / `recommended_command` を正しく表示する
- [x] `run_until_stop.py` stop summary が IC stop path を一貫して表示する
- [x] `bridge_orchestrator.py run()` が IC stop path で `chatgpt_decision_note` を出力する (Phase 45)

### G. Ready issue selection

- [x] `--ready-issue-ref` CLI オプションが `request_next_prompt.py` へ渡る
- [x] `--select-issue` CLI オプションが issue 選定モードで動作する
- [x] `initial_selection_stop` 後に `selected_ready_issue_ref` が state に書き込まれる
- [x] `_build_ic_operator_decision_note()` が選定 issue ref を note に含める

### H. Codex run dispatch 準備

- [x] `launch_issue_centric_codex_run()` が trigger comment 作成から Codex 起動まで一通り動作する
- [x] `has_pending_issue_centric_codex_dispatch()` で dispatch 待ち状態を検出できる
- [x] `bridge_orchestrator.py` が `codex_run_stop` で自動 dispatch へ進む

### I. Human review / supplement 入力

- [x] `execute_human_review_action()` が review comment を投稿できる
- [x] `human_review_needed` 後の `--resume` で補足入力を受けて次 request を準備できる
- [x] `human_review` は 1 回だけ自動継続し、2 回目のみ `人確認待ち` 停止

### J. Legacy detect-only stop

- [x] `is_blocked_codex_lifecycle_state()` が legacy compat の blocked lifecycle を検出できる
- [x] legacy contract (visible DOM text) 経由の fetch が fallback として動作する
- [x] `legacy_contract_detected` パスで operator 操作なしに fallback route を経由できる

### K. 未着手 / 今後課題

- [x] Plan A → visible DOM fallback の自動判定精度向上 (Phase 48: `_IcReplyRouteDecision` + `_resolve_ic_reply_route_decision` 追加 / Plan A 破損時は `stop_broken` で explicit stop / legacy fallback 非混在)
- [x] `codex_run + close_current_issue = true` の dispatcher サポート (Phase 47: trigger → launch → post-launch close / matrix_path `codex_run_then_close`)
- [ ] multi-flag 組み合わせの拡張 (narrow matrix 外)
  - Phase 49: `no_action` issue-management slices (followup-only / close-only / followup+close) 対応済み
  - Phase 50: `issue_create + close_current_issue` → `issue_create_then_close` 対応済み
  - Phase 51: `issue_create + create_followup_issue + close_current_issue` → `issue_create_followup_then_close` 対応済み
  - Phase 52: `codex_run + create_followup_issue` → `codex_run_followup` 対応済み
  - Phase 52: `codex_run + create_followup_issue + close_current_issue` → `codex_run_followup_then_close` 対応済み、`_resolve_codex_run_matrix_path()` helper 追加
  - Phase 53: supported slices の project sync state family coverage 拡張 (`_normalize_project_sync_result` helper 追加、`IcProjectSyncStateFamilyTests` 40 tests 追加)
  - Phase 54: project sync execution precision / failed-path hardening (`_should_retry_project_sync` 追加、3 apply helper を `_normalize_project_sync_result` 経由に統一、lifecycle sync 1-retry 実装、`IcProjectSyncFailedPathTests` 32 tests 追加)
  - Phase 55: project sync 外部 GitHub API mutation 実装精度 (`"not_requested_no_project"` 語彙統一、`_map_project_mutation_result_to_sync_status` / `_ensure_project_item_for_issue` / `_sync_project_state_field` helper 追加、`"blocked_project_state_sync"` / `"failed_project_state_sync"` → `"project_state_sync_failed"` 修正、`IcProjectSyncGitHubMutationTests` 21 tests 追加)
  - Phase 56: `project_state_sync_failed` operator-facing warning surface (`_detect_project_sync_warning` / `_resolve_project_sync_warning_family` / `_build_project_sync_warning_note` / `bridge_project_sync_warning_suffix` / `format_project_sync_warning_note` helper 追加、`format_operator_stop_note` / `suggested_next_note` に warning 付加、stop summary に `project_sync_warning` 行追加、`ProjectSyncWarningOperatorSurfaceTests` 48 tests 追加)
  - Phase 57: project_state_sync_failed warning を残り operator-facing surface へ伝播 (`bridge_orchestrator` completed path に `format_operator_stop_note` 使用、`start_bridge.print_doctor` に `project_sync_warning:` 行追加、`entry_guidance` completed path に `bridge_project_sync_warning_suffix` 追加、`ProjectSyncWarningSurfaceAlignmentTests` 22 tests 追加)
  - Phase 58: project_state_sync_failed alert signal / payload / dedupe (`_ProjectSyncAlertCandidate` dataclass / `_detect_project_sync_alert_candidate` / `_build_project_sync_alert_candidate` / `_build_project_sync_alert_payload` / `record_project_sync_alert_if_new` / `format_project_sync_alert_status` helper 追加、DEFAULT_STATE に `last_project_sync_alert_status` / `_hash` / `_file` 追加、`start_bridge.print_doctor` に `project_sync_alert:` 行追加、`run_until_stop.finish` で alert 自動記録、`ProjectSyncAlertSignalPayloadTests` 35 tests 追加)
  - Phase 59: project_state_sync_failed generic webhook delivery (`_deliver_project_sync_alert_to_webhook` / `deliver_project_sync_alert_if_pending` / `format_project_sync_alert_delivery_status` helper 追加、DEFAULT_STATE に 5 delivery keys 追加、DEFAULT_PROJECT_CONFIG に `project_sync_alert_webhook_url` 追加、`start_bridge.print_doctor` に `project_sync_alert_delivery:` 行追加、`run_until_stop.finish` で delivery 試行、`bridge/project_config.example.json` に webhook URL key 追加、`ProjectSyncAlertWebhookDeliveryTests` tests 追加)
  - Phase 60: project_state_sync_failed webhook delivery を bounded multi-retry に拡張 (`_WEBHOOK_MAX_ATTEMPTS=3` / `_WEBHOOK_RETRY_DELAYS_SECONDS=(1,3)` 追加、`_deliver_project_sync_alert_with_retry` retry layer 追加、`deliver_project_sync_alert_if_pending` を retry 経由に更新、DEFAULT_STATE に `last_project_sync_alert_delivery_attempt_count` 追加、`format_project_sync_alert_delivery_status` に `attempts=N` suffix 追加、`bridge/project_config.example.json` notes 更新、`ProjectSyncAlertBoundedRetryTests` tests 追加)
  - Phase 61: project_sync_alert webhook config validation / doctor guidance / runbook hardening (`validate_project_sync_alert_webhook_url` / `format_project_sync_alert_webhook_config_note` helper 追加、`start_bridge.print_doctor` に `project_sync_alert_webhook_config:` 行追加、`bridge/project_config.example.json` に config_validation / troubleshooting / timing / signal_boundary notes 追加、`ProjectSyncAlertWebhookConfigValidationTests` tests 追加)
- [ ] 大規模 state machine rewrite / full contract cutover
- [ ] Safari automation 以外のフロントエンド対応 (API / CLI 直結等)
- [ ] issue close / project sync の自動化精度向上

---

## 8. 運用確認コマンド早見表

```bash
# 現在の状況だけ確認
python3 scripts/start_bridge.py --status

# 軽い診断
python3 scripts/start_bridge.py --doctor

# そのまま再開
python3 scripts/start_bridge.py --project-path /path/to/target-repo --max-execution-count 6

# initial_selection_stop 後の再起動
python3 scripts/start_bridge.py --ready-issue-ref "#7" --project-path /path/to/target-repo --max-execution-count 6

# human_review_needed 後の再開
python3 scripts/start_bridge.py --resume --project-path /path/to/target-repo --max-execution-count 6

# error 解除
python3 scripts/start_bridge.py --clear-error --project-path /path/to/target-repo

# 複数手まとめて実行
python3 scripts/run_until_stop.py --max-steps 6 --fetch-timeout-seconds 1800
```
