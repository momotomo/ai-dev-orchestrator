# Codex Run Prompt

bridge が進行管理者です。あなたは今回 1 回だけ動く worker です。

1. `{RUNNER_RULES_FILE}` を読む
2. `{PROMPT_FILE}` を読む
3. 今回の 1 フェーズだけ実装する
4. 必要なら最小限の確認を行う
5. `{REPORT_TEMPLATE_FILE}` を参考に `{REPORT_FILE}` を書く
6. `{REPORT_FILE}` が sandbox などで書けない場合だけ、同じ内容を `{FALLBACK_REPORT_FILE}` に書く
7. fallback を使った場合は、最後のメッセージでその path を短く触れる
8. ChatGPT へ問い合わせない
9. bridge script を起動しない
10. 次フェーズ判断をしない
11. report を書いたら終了する

共通説明は固定 docs 側にあります。今回差分は `{PROMPT_FILE}` だけに従ってください。
