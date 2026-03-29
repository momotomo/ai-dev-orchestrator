melody-craft-studio で、次の軽量 1 フェーズだけを実装してください。
目的は sample browser 一覧の「今のフレーズで優先中」「いま確認している素材」「選べる候補」の区別を、短い日本語バッジで見分けやすくすること です。
保存形式、resolver、preview/playback/export の挙動は変えず、presenter と UI の軽い可視化改善 に留めてください。

事前確認
まず以下を読んで前提をそろえてください。
docs/PROJECT_CONTEXT.md
docs/WORKING_RULES.md
docs/ROADMAP.md
docs/OPEN_QUESTIONS.md
あわせて、今回の直前フェーズで追加された source summary 関連実装を確認し、責務を崩さない最小差分にしてください。
今回のフェーズ
Phase 81C: sample browser 一覧の状態バッジ統一
ねらい
前フェーズで source 状態の 1 行要約は統一されました。
次は sample browser の一覧そのものを見たときに、初心者でも
これが今のフレーズで優先されているのか
これはただの候補なのか
これはいま確認中のものなのか
を 一覧の中で一目で判別できること を目指してください。
実装条件
やることは 1 つだけ：browser 一覧の状態バッジ整理
source 解決ロジックは変更しない
.mcsong 保存形式は変更しない
preview / arrangement playback / project playback / export / video export の意味変更は禁止
大きなレイアウト変更は禁止
日本語 UX を優先し、短くやさしい文言にする
presenter / 表示 / 小テスト中心で終える
期待する変更
以下を満たす最小実装にしてください。
sample browser presenter に行アイテム用の状態バッジ情報を追加
例:
このフレーズで優先中
いま確認中
選択候補
実際の文言は既存 UI に合わせて自然な日本語に調整してよい
ただし、意味を盛りすぎず、現在の実動作とズレる表現は避ける
sample browser の各行に短い状態バッジを表示
既存の一覧構造は大きく変えない
強い主張になりすぎない軽量表示にする
複数状態が重なる場合は presenter 側で優先順位を整理する
例: このフレーズで優先中 を最優先
その次に いま確認中
それ以外は必要なら無表示、または 選択候補
UI がうるさくなるなら常時全件にバッジを出さず、重要状態だけでもよい
source summary との食い違いを避ける
前フェーズの 1 行要約と矛盾しない表現にする
chooser / browser / current material の説明系統をそろえる
テスト追加/更新
presenter の unit test を追加または拡張
可能なら browser panel の軽い表示 test も追加
文言完全一致に依存しすぎず、意味単位で壊れにくいテストにする
実装の進め方
まず browser 一覧の presenter と panel を確認
状態判定は presenter に寄せる
UI 側は presenter 出力を描画するだけに近づける
既存 summary と矛盾しない文言を使う
差分を広げすぎない
完了条件
sample browser 一覧を見たとき、優先中/確認中/候補の区別が前より分かりやすい
source summary と説明が食い違わない
resolver / 保存 / preview/playback/export の挙動は変わらない
テストが通る
禁止事項
.mcsong 仕様変更
source resolver の意味変更
sample library 永続化
preview/playback/export の仕様変更
大きい UI 再設計
新しい複雑な状態管理の導入
確認コマンド
必要な範囲で実行してください。
pnpm test
pnpm lint
pnpm build
最終報告に含めること
実施概要
変更した主要ファイル
状態バッジ設計の意図
source summary と矛盾していない確認
既存挙動を変えていない確認
テスト/ビルド結果
必要なら作業中に見つけた軽微な文言ゆれは、この目的に沿う範囲で一緒に整えて構いません。
ただし今回は 「sample browser 一覧の状態バッジ統一」からはみ出さない でください。
