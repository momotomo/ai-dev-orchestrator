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
  - 残: Projects update 全面対応
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
