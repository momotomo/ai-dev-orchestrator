from __future__ import annotations


COPILOT_STABILITY_PREAMBLE = """\
## Stability-first instruction

The user is **not** asking you to optimize for speed or efficiency.
The user wants you to work **stably**, avoid freezing VSCode, and **finish the task reliably**.

Stability is more important than speed.
Completion is more important than optimization.

All forbidden rules are **hard constraints**.
You must follow them **strictly and without exception**.

Do not take shortcuts.
Do not optimize by combining steps.
Do not reinterpret forbidden rules loosely.

VSCode may freeze if you ignore these constraints.
To avoid that, you must obey every forbidden rule strictly.
When in doubt, choose the safer and slower path.

## Mandatory execution rules

These are hard constraints. Follow them strictly.

- 並列作業、同時実行は禁止。
- 一度に行う作業は1つのみ。
- 使用するターミナルは1つのみ。
- コンテキストウィンドウは1つのみで作業を進めること。
- リソースを使うような作業は極力避けること。
- ターミナルでは1回につき1コマンドのみ実行すること。
- 各コマンド実行後は必ず停止し、結果を確認してから次へ進むこと。
- `&&` を使わないこと。
- `;` を使わないこと。
- パイプを使わないこと。
- subshell を使わないこと。
- 複数行の shell command block を使わないこと。
- terminal grep-style inspection を使わないこと。
- ファイル確認は built-in file tools を使い、1ファイルずつ順に確認すること。
- 検索が必要な場合は `grep_search` / `file_search` のみを使うこと。
- `semantic_search` は使わないこと。
- `gh` コマンドは最小限の引数のみ使うこと。
- `gh` コマンドの前後では必ず停止し、結果を確認すること。
- もし作業が複数コマンド連結を必要としそうなら、実行せず停止して報告すること。
- 環境が不安定になったら、その場で停止して最後に成功した作業を報告すること。\
"""
