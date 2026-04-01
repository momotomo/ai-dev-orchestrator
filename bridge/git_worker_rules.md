# Git Worker Rules

## 目的

- Codex worker が実装前に Git まわりの共通ルールを短く確認する
- 毎回の phase prompt に Git ルールを重複記載しない

## 基本

- まず worker repo の `git status` と関連差分を確認する
- 既存の dirty worktree は前提として受け入れ、無関係な変更を戻さない
- destructive な git 操作 (`reset --hard`、`checkout --` など) は使わない
- interactive な git 操作より non-interactive なコマンドを優先する

## 変更の扱い

- 今回の 1 フェーズに必要な差分だけを作る
- 他人やユーザーの変更と衝突しそうなら、勝手に上書きせず report に残す
- commit / merge / push は今回 prompt や repo ルールで求められた時だけ行う

## report との関係

- Git 判断で迷う時は危険な推測で進めず、今回の report に残課題として書く
