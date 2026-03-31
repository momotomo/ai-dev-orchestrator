# Codex Launch Plan

## 現段階の方針

- bridge が `launch_codex_once.py` で Codex を 1 回だけ起動する
- `bridge_orchestrator.py` は state を見て次の 1 手だけを進める
- 常駐監視や完全無人化はまだ行わない

## bridge と Codex の分離

- bridge: request、fetch、launch、archive、next request、state 更新
- Codex: 実装と report 記入だけ
- Codex は ChatGPT に問い合わせず、bridge も起動せず、1 フェーズで終了する

## 今回の最小 launcher

- 入力は `bridge/inbox/codex_prompt.md`
- 起動前に bridge が `codex_running` にする
- 実行後に `bridge/outbox/codex_report.md` を確認し、report があれば `codex_done`
- report がなければ `error=true` で停止する

## 将来の拡張候補

- Codex 実行コマンドや model の project config 化
- 停止条件付きの軽い連続実行 runner
- report 品質の自動検査
