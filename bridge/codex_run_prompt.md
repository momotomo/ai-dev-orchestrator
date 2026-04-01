# Codex Run Prompt

bridge が進行管理者です。あなたは今回 1 回だけ動く worker です。

1. `{RUNNER_RULES_FILE}` を読む
2. `{GIT_RULES_FILE}` を読む
3. `{PROMPT_COMPACTION_RULES_FILE}` を読む
4. launch prompt に書かれた path はすべて絶対 path として扱う
5. `{PROMPT_FILE}` を読む
6. prompt file に `追加確認 docs` があり、必要な path が書かれている時だけ追加で読む
7. 今回の 1 フェーズだけ実装する
8. 必要なら最小限の確認を行う
9. `{REPORT_TEMPLATE_FILE}` を参考に `{REPORT_FILE}` を書く
10. `{REPORT_FILE}` が sandbox などで書けない場合だけ、同じ内容を `{FALLBACK_REPORT_FILE}` に書く
11. fallback を使った場合は、最後のメッセージでその path を短く触れる
12. ChatGPT へ問い合わせない
13. bridge script を起動しない
14. 次フェーズ判断をしない
15. report を書いたら終了する

固定ルールは上の docs にあります。今回差分は `{PROMPT_FILE}` と、そこに明記された追加 docs だけに従ってください。
