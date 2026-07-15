# QUHP CAM Assistant

Fusion 360 の切削データ作成（KitMill RZ420 向け）を自動化するアドイン。
セットアップ作成・輪郭/穴/ポケットの自動分類とテンプレート適用・NC 一括出力・配置チェックを行う。

## インストール

1. ジャンクションを作成（管理者不要、初回のみ）:

   ```bat
   mklink /J "%APPDATA%\Autodesk\Autodesk Fusion 360\API\AddIns\QuhpCamAssistant" "E:\Projects\fusion-addin\QuhpCamAssistant"
   ```

2. Fusion を起動 → ユーティリティ → アドイン →「QuhpCamAssistant」を実行（「起動時に実行」推奨）
3. 製造ワークスペースのツールバーに「QUHP CAM」パネルが出る

## 使い方

1. **配置チェック** — パーツ配置後に実行。280×280 枠・8mm 間隔・高さ揃えを確認
2. **切削データ自動作成** — ボディを解析して外郭/内郭/穴/ざぐり/ポケットを自動分類。
   確認ダイアログで内容をチェックして「生成」。**生成後は必ずシミュレーションで全データを確認する**
3. **NC一括出力** — 工具ごとにまとめ、切削順の名前（`1_flat3.0` 等）で NC ファイルを出力。
   番号順に CNC で実行する

設定（テンプレフォルダ・穴径ルール・ポストプロセッサのパス等）は
[QuhpCamAssistant/config.json](QuhpCamAssistant/config.json) で変更できる。

## 初回セットアップ（実機確認）

CAM API の一部パラメータ名は Fusion のバージョンにより異なるため、初回に確認が必要:

1. UI で手動作成した切削データ入りのドキュメントを開く
2. スクリプトとして [tools/dump_parameters.py](tools/dump_parameters.py) を実行し、ダンプを保存
3. ダンプ結果で [docs/api-notes.md](docs/api-notes.md) の「実機確認 TODO」を解消し、
   `cam_builder.py` 先頭の候補名定数を更新する

## テンプレート

- `templates/Φ*/` — サークル標準の CAM テンプレート（このアドインが使用）
- `templates/inventor/` — Inventor 向け（保管のみ。拡張子を `.f3dhsm-template` に
  リネームすれば Fusion でも読み込める）

テンプレの命名規約: `[Φ<工具径>]<種別>`（例: `[Φ1.5]Φ2.0 穴あけ`）。
新しい工具径・種別のテンプレを追加すれば自動で認識される。
