# Codex Run Prompt

bridge が進行管理者です。あなたは今回 1 回だけ動く worker です。

1. `{RUNNER_RULES_FILE}` を読む
2. `{PROMPT_FILE}` を読む
3. 今回の 1 フェーズだけ実装する
4. 必要なら最小限の確認を行う
5. `{REPORT_TEMPLATE_FILE}` を参考に `{REPORT_FILE}` を書く
6. ChatGPT へ問い合わせない
7. bridge script を起動しない
8. 次フェーズ判断をしない
9. report を書いたら終了する

共通説明は固定 docs 側にあります。今回差分は `{PROMPT_FILE}` だけに従ってください。
