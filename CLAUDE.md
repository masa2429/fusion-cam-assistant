# Fusion CAM Assistant — Fusion 360 CAM 自動化アドイン

サークルのロボット製作向けに、Fusion 360 の切削データ作成（CNC: KitMill RZ420、板材 280×280）を自動化するアドイン。CAM テンプレート（.f3dhsm-template）はジオメトリ選択を持ち越さないため、セットアップ作成・輪郭/穴/ポケットの自動分類とテンプレ適用・ポスト処理を API で自動化する。

## プロジェクト構造

- `FusionCamAssistant/` — アドイン本体（Fusion の AddIns フォルダへジャンクションで配置）
  - `commands/` — 各コマンド（auto_cam=解析→確認→一括適用、post_all=NC出力、layout_check=配置チェック）
  - `lib/` — ロジック（template_registry=テンプレ解析、classifier=輪郭分類、cam_builder=CAM生成）
  - `config.json` — テンプレフォルダパス、穴径ルール、ストック既定値、ポスト設定
- `templates/` — サークル標準 CAM テンプレート（Φ1.5/2.0/3.0/5.0）。**読み取り専用データ**
- `templates/inventor/` — Inventor 向けテンプレ（スコープ外の保管。拡張子を .f3dhsm-template にリネームすれば Fusion でも読める）
- `tools/` — Fusion で単発実行する調査・検証スクリプト（DumpParameters 等）
- `docs/api-notes.md` — **CAM API の調査結果。実装前に必読**
- 自動化の仕様の源泉はサークルの手順書「切削データ作成のススメ」（部内 wiki。他メンバーの著作物のためリポジトリには含めない）

## 重要ルール

- コードの編集は `FusionCamAssistant/` と `tools/` 配下でのみ行うこと
- `templates/` 配下のテンプレートファイルを書き換えないこと（読み取り専用）
- CAM API を使うコードを書く前に `docs/api-notes.md` を読むこと
- **廃止 API 使用禁止**: `CAM.postProcess` / `PostProcessInput` / `Setup.createFromCAMTemplate`（無印）。代替は api-notes.md 参照
- CAM のパラメータ名（'contours' 等）を推測で書かないこと。未確認のものは `tools/DumpParameters` の実測結果で確定し、api-notes.md を更新してから使う

## 技術スタック（厳守）

- Python（Fusion 360 内蔵ランタイム、外部パッケージ追加不可）
- `adsk.core` / `adsk.fusion` / `adsk.cam` のみ。標準ライブラリは可
- Fusion API の内部単位は **cm**（UI の mm と 10 倍違う）。境界を越える値は必ず単位変換を明示
- 対象 Fusion バージョン: 2023年10月以降（createFromCAMTemplate2 が必要）
- 日本語・特殊文字パス（`[Φ3.0]` 等）があるため、パスは常に `os.path` で結合しリテラル扱い。PowerShell から触るときは `-LiteralPath`

## ビルドと実行

- ビルド工程なし。アドインの配置はジャンクションを1回作る（作成済み）：
  `New-Item -ItemType Junction -Path "$env:APPDATA\Autodesk\Autodesk Fusion 360\API\AddIns\FusionCamAssistant" -Target "E:\Projects\fusion-addin\FusionCamAssistant"`
  （`mklink` は cmd.exe 専用のため PowerShell では使わない）
- コード変更後は Fusion の「ユーティリティ → アドイン」で停止→実行（Fusion 再起動不要）
- ログ・デバッグはテキストコマンドウィンドウ（`adsk.core.Application.log()`）とメッセージボックス
- `tools/` のスクリプトは「ユーティリティ → アドイン → スクリプト」から単発実行

## 実装ルール

- 「構文エラーがない」は完了ではない。**必ず Fusion 実機で動作確認**してから次へ進む
- 実機確認にはユーザーの協力が必要（Fusion 起動・検証用 f3d）。コードを書く前に準備状況を確認する
- CAM 生成結果の検証手順: UI で手動作成した正解データと `parameters` を突き合わせる → シミュレーションで衝突なしを確認（シミュレーション確認はユーザーが行う。安全文化として省略しない）
- 加工順の不変条件: **外郭は必ず最後**。ポケット/ざぐり → 穴あけ → 内郭 → 取り残し → 外郭
- 穴径ルール: Φ2.0穴→[Φ1.5]Φ2.0穴あけ、Φ2.6穴→[Φ2.0]Φ2.6穴あけ。ルール外の円形穴はスポットドリル用として未割り当て表示（v1 では自動化しない）

## Git

- コミットメッセージは Conventional Commits 形式、説明は日本語
- **Co-Authored-By 等の AI 共著者トレーラーを付けないこと**（コミット者は人間のみ。GitHub の contributor 表示に AI を出さない）
- 形式: `type(scope)：日本語の説明`（例: `feat(addin)：セットアップ自動作成を追加`）
- 以下の単位でこまめにコミットすること：
  - 実装ステップ（プランの Step）1つごと
  - バグ修正1件ごと
- 複数機能をまとめて1コミットにしないこと

## セッション運用（トークン節約）

- メインセッション（Fable 5）は設計・監査・レビューに専念する
- 実装（コード書き・検証スクリプト作成・定型的なデバッグ）は Agent ツールで Opus/Sonnet のサブエージェントに切り出す
  - 定型的・機械的な実装 → Sonnet、込み入った実装 → Opus を `model` パラメータで指定
  - サブエージェントには api-notes.md の該当箇所・対象ファイル・検証方法を明示した自己完結のプロンプトを渡す
- CAM API の解釈が絡む中核実装（classifier / cam_builder）はメインセッションで直接実装してよい
- サブエージェントの成果物はメインセッションで必ずレビューしてからコミットする

## 外部AIの活用（/codex・claudex）

セカンドオピニオン用に2つの外部AIが使える。以下の場面で積極的に使うこと：

- `/codex` スキル — Codex（GPT-5.6）へ質問を転送する（第一候補）
- `claudex` CLI — CLIProxyAPI 経由の別 Claude。PowerShell で以下のように使う：
  ```powershell
  . "$env:LOCALAPPDATA\Programs\CLIProxyAPI\claudex.ps1"
  claudex -p "（質問内容）"
  ```
  /codex が使えないときや、2つ目の独立した意見が欲しいとき（設計判断で /codex と意見が割れた場合など）に使う

- **CAM API の不明点のセカンドオピニオン**: api-notes.md で ⚠️（未確認）の項目や、公式ドキュメントで裏取りできない API の挙動について、実機確認の前に別モデルの知見を求める
- **行き詰まったデバッグ**: 同じエラーへの修正を2回試して直らないとき、症状・コード・試したことをまとめて /codex に渡し、別視点の見立てを得る
- **設計判断のレビュー**: 分類ロジックなど判断が割れる設計は、採用案と代替案を説明して反論をもらってから決める

注意: /codex の回答も「未確認情報」として扱う。パラメータ名等は必ず DumpParameters の実測で確定してから採用し、api-notes.md に確認状態を記録する。

## ミスの記録

<!-- 失敗するたびにここに追記し，同じミスを繰り返さない -->
<!-- 書式： YYYY-MM-DD ／ 症状 ／ 原因 ／ 対策 -->

- 2026-07-15 ／ `CAMTemplate.createFromFile` が「テンプレート ファイルにアクセスできませんでした」「No mapping for the Unicode character...」で失敗 ／ API が内部で ANSI（cp932）変換するため日本語・Φ入りのテンプレパスを扱えない ／ ASCII 名の一時ファイルにコピーしてから読み込む（cam_builder.py `_load_cam_template`）。**Fusion API にパスを渡すときは常に非 ASCII を疑うこと**
- 2026-07-15 ／ `mklink /J` が PowerShell で「認識されません」 ／ mklink は cmd.exe 内蔵コマンド ／ PowerShell では `New-Item -ItemType Junction` を使う
- 2026-07-15 ／ Φ3内郭で「Some contours were not machined」（曲がった細溝にパスが出ない） ／ 開口幅を外接矩形の短辺で推定していたため曲がった細溝を過大評価 ／ 面積A・周長Pから等価長方形の短辺 w=(P/2-√((P/2)²-4A))/2 で推定（classifier.py `_opening_estimate_mm`）。**2A/P（水力直径系）はコンパクト形状を半分に過小評価するので使わない**
- 2026-07-15 ／ 取り残し加工が「空のツールパス（No passes to link）」 ／ 尖った隅の検出が安全側の誤提案（Φ3が既に削り切っていた） ／ 実害なし。確認ダイアログでチェックを外せる。空パス時は自動削除する改善余地あり
- 2026-07-16 ／ 日本語入り .ps1 が素の PowerShell 5.1 でパースエラー（メンバー環境で install.bat が動かない） ／ BOM 無し UTF-8 を PS5.1 が cp932 として読むため ／ **.ps1 は必ず BOM 付き UTF-8 で保存する**。また `Remove-Item` はジャンクションで確認プロンプトを出して固まるため、リンク削除は `(Get-Item -Force).Delete()` を使う
- 2026-07-16 ／ NC一括出力で特定グループだけ「ポストに失敗」 ／ ツールパスが「要更新」状態のまま postProcess を呼ぶと失敗する。`isToolpathValid` での事前検出はバージョン差で機能しない ／ NC出力前に無条件で `generateAllToolpaths(True)` を実行してからポストする（post_all.py）。**実機で修正効果を確認済み**
- 2026-07-16 ／ 取り残し加工が提案されないケース（leg_thigh_4mm_2：外郭タブ切り欠きの R0.75 フィレット） ／ 取り残し判定が内郭ループしか見ておらず、凹み/凸みも区別していなかった ／ 加工方向（内郭=CCW・外郭=CW）でたどり「工具側へ凹む特徴のみ」（左曲がり頂点=0・凹円弧=半径）を数える判定にし、外郭にも適用（classifier.py `_concave_feature_radius_mm`）。検出値はテキストコマンドウィンドウにログされる。**実機で修正効果を確認済み**
- 2026-07-15 ／ 一部パーツで内郭が外側・外郭が内側を削る（加工サイド反転） ／ エッジ列から作った ChainSelection の周回方向はボディの作成方法依存で不定。左補正では周回方向＝加工サイドになる ／ 符号付き面積で現在の向きを判定し `ChainSelection.isReverted` で「内郭/穴=反時計回り・外郭=時計回り（+Z視点）」に統一（cam_builder.py）。**実機で修正効果を確認済み**
