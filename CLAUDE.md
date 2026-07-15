# QUHP CAM Assistant — Fusion 360 CAM 自動化アドイン

QUHP（サークル）のロボット製作向けに、Fusion 360 の切削データ作成（CNC: KitMill RZ420、板材 280×280）を自動化するアドイン。CAM テンプレート（.f3dhsm-template）はジオメトリ選択を持ち越さないため、セットアップ作成・輪郭/穴/ポケットの自動分類とテンプレ適用・ポスト処理を API で自動化する。

## プロジェクト構造

- `QuhpCamAssistant/` — アドイン本体（Fusion の AddIns フォルダへジャンクションで配置）
  - `commands/` — 各コマンド（auto_cam=解析→確認→一括適用、post_all=NC出力、layout_check=配置チェック）
  - `lib/` — ロジック（template_registry=テンプレ解析、classifier=輪郭分類、cam_builder=CAM生成）
  - `config.json` — テンプレフォルダパス、穴径ルール、ストック既定値、ポスト設定
- `templates/` — サークル標準 CAM テンプレート（Φ1.5/2.0/3.0/5.0）。**読み取り専用データ**
- `templates/inventor/` — Inventor 向けテンプレ（スコープ外の保管。拡張子を .f3dhsm-template にリネームすれば Fusion でも読める）
- `tools/` — Fusion で単発実行する調査・検証スクリプト（dump_parameters.py 等）
- `docs/api-notes.md` — **CAM API の調査結果。実装前に必読**
- `docs/切削データquhp.md` — サークルの切削データ作成手順書（自動化の仕様の源泉）

## 重要ルール

- コードの編集は `QuhpCamAssistant/` と `tools/` 配下でのみ行うこと
- `templates/` 配下のテンプレートファイルを書き換えないこと（読み取り専用）
- CAM API を使うコードを書く前に `docs/api-notes.md` を読むこと
- **廃止 API 使用禁止**: `CAM.postProcess` / `PostProcessInput` / `Setup.createFromCAMTemplate`（無印）。代替は api-notes.md 参照
- CAM のパラメータ名（'contours' 等）を推測で書かないこと。未確認のものは `tools/dump_parameters.py` の実測結果で確定し、api-notes.md を更新してから使う

## 技術スタック（厳守）

- Python（Fusion 360 内蔵ランタイム、外部パッケージ追加不可）
- `adsk.core` / `adsk.fusion` / `adsk.cam` のみ。標準ライブラリは可
- Fusion API の内部単位は **cm**（UI の mm と 10 倍違う）。境界を越える値は必ず単位変換を明示
- 対象 Fusion バージョン: 2023年10月以降（createFromCAMTemplate2 が必要）
- 日本語・特殊文字パス（`[Φ3.0]` 等）があるため、パスは常に `os.path` で結合しリテラル扱い。PowerShell から触るときは `-LiteralPath`

## ビルドと実行

- ビルド工程なし。アドインの配置はジャンクションを1回作る：
  `mklink /J "%APPDATA%\Autodesk\Autodesk Fusion 360\API\AddIns\QuhpCamAssistant" "E:\Projects\fusion-addin\QuhpCamAssistant"`
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

## ミスの記録

<!-- 失敗するたびにここに追記し，同じミスを繰り返さない -->
<!-- 書式： YYYY-MM-DD ／ 症状 ／ 原因 ／ 対策 -->
