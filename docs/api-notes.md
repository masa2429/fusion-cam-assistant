# Fusion 360 CAM API メモ（2026-07 調査）

このプロジェクトで CAM 自動化に使う API の調査結果。**実装時はこのメモに従い、ここに無い API 名・パラメータ名を推測で書かないこと。**

凡例: ✅ = 公式docs/サンプルで確認済み ／ ⚠️ = フォーラム由来・要実機確認 ／ ❌ = 廃止（使用禁止）

## 使用禁止（廃止済み API）

| 廃止 API | 廃止時期 | 代替 |
|---|---|---|
| ❌ `Setup.createFromCAMTemplate()` | 2024/7 | `Setup.createFromCAMTemplate2()` + `CreateFromCAMTemplateInput` |
| ❌ `CAM.postProcess()` / `CAM.postProcessAll()` | 2025/9 | NCProgram API（`cam.ncPrograms`） |
| ❌ `PostProcessInput` | 2025/9 | `NCProgramPostProcessOptions` |

## 1. テンプレートからのオペレーション生成 ✅

```python
camTemplate = adsk.cam.CAMTemplate.createFromFile(templatePathname)  # 2023/4導入
templateInput = adsk.cam.CreateFromCAMTemplateInput.create()          # 2023/10導入
templateInput.camTemplate = camTemplate
results = setup.createFromCAMTemplate2(templateInput)  # OperationBase[] を返す
```

- ❗ **`createFromFile` は非 ASCII パス（日本語・Φ）で失敗する**（実機確認済み 2026-07-15。内部で ANSI 変換するため）。ASCII 名の一時ファイルへコピーしてから読み込むこと（cam_builder.py `_load_cam_template` 参照）
- `CreateFromCAMTemplateInput.mode` のデフォルトは **Skip Generation**（ツールパスは生成されない）→ 明示的に `generateToolpath`/`generateAllToolpaths` を呼ぶ
- **テンプレートはジオメトリ選択を持ち越さない**（公式サポート記事で明記）→ 生成後に contours/pockets/holeFaces を API で割り当てるのが必須。これがこのアドインの存在理由
- ⚠️ テンプレ内の工具が生成された操作に保持されるかは実機で要確認（保持されない場合は工具ライブラリ経由で割当）
- 公式サンプル: https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/New_Operation_Sample_Sample.htm

## 2. ジオメトリ選択の設定

### 2D輪郭（contour2d）✅（パラメータ名⚠️）

```python
param = op.parameters.itemByName('contours')  # ⚠️ 名前は実機ダンプで確認（公式サンプルでは 'machiningBoundarySel' の例あり）
contoursValue = param.value                    # CadContours2dParameterValue
chains = contoursValue.getCurveSelections()    # CurveSelections
chain = chains.createNewChainSelection()       # ChainSelection
chain.inputGeometry = [brepEdge]               # BRepEdge / SketchCurve のリスト
contoursValue.applyCurveSelections(chains)
```

`CurveSelections` のメソッド（全て2023/4導入）: `createNewChainSelection`, `createNewSilhouetteSelection`, `createNewPocketSelection`, `createNewPocketRecognitionSelection`, `createNewFaceContourSelection`, `createNewSketchSelection`, `clear`, `item`, `remove`

❗ **チェーンの周回方向＝加工サイド**（実機確認済み 2026-07-15）:
エッジ列から作った ChainSelection の周回方向は不定（ボディの押し出し方向・ミラー等に依存）。
テンプレは左補正なので、+Z 視点で **内郭/穴=反時計回り・外郭=時計回り** になるよう
符号付き面積で向きを判定し `chain.isReverted = True` で反転させる（cam_builder.py `_signed_area_from_first_edge`）。
`isReverted` は「先頭エッジの自然方向から始まる連結順トラバース」を基準に反転する挙動で、この方式で16パーツ全て正しいサイドになった。

### ポケット（pocket2d）✅

```python
pocketSelection = op.parameters.itemByName('pockets').value  # CadContours2dParameterValue
chains = pocketSelection.getCurveSelections()
chain = chains.createNewPocketSelection()
chain.inputGeometry = pocketBottomFaces   # 底面 BRepFace のリスト
pocketSelection.applyCurveSelections(chains)
```

### ドリル穴 ✅（このプロジェクトの標準テンプレには drill 戦略なし、将来用）

```python
holeSelection = op.parameters.itemByName('holeFaces').value  # CadObjectParameterValue
holeSelection.value = faces  # list[BRepFace]
```

- 穴認識 API: `RecognizedHole` / `RecognizedHoleGroup` / `RecognizedHoleSegment`（径・深さでグループ化）
- ⚠️ `selectSameDiameter`（bool）: UI の「同じ直径を選択」相当（フォーラム由来）
- 公式サンプル: https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/HoleAndPocketRecognition_Sample.htm

## 3. セットアップ生成 ✅（一部パラメータ名⚠️）

```python
setupInput = cam.setups.createInput(adsk.cam.OperationTypes.MillingOperation)
setupInput.models = [body]            # 対象ボディ
setup = cam.setups.add(setupInput)
setup.stockMode = adsk.cam.SetupStockModes.RelativeBoxStock  # FixedBoxStock 等も列挙にある
setup.parameters.itemByName('job_stockOffsetMode').expression = "'simple'"
setup.parameters.itemByName('job_stockOffsetSides').expression = '0 mm'
setup.parameters.itemByName('job_stockOffsetTop').expression = '0 mm'
```

- WCS 原点: `wcs_origin_mode`（`'point'` / `'modelPoint'` / `'stockPoint'` 等）+ `wcs_origin_point`（SketchPoint/ConstructionPoint）または `wcs_origin_boxPoint`（`'top 1'` のような文字列）
- ⚠️ 固定ボックスストックの寸法パラメータ名（`job_stockFixedX` 等）と `wcs_origin_boxPoint` の文字列一覧は未確認 → **tools/DumpParameters で実機ダンプして確定し、このメモを更新する**
- 公式サンプル（WCS）: https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/SetViseOriginAsSetupWCSOrigin_Sample.htm

## 4. ツールパス生成・ポスト処理 ✅

```python
future = cam.generateAllToolpaths(True)   # skipValid=True。GenerateToolpathFuture
# future.isGenerationCompleted / numberOfCompleted でポーリング

ncInput = cam.ncPrograms.createInput()
ncInput.displayName = '1_flat3.0'
ncInput.operations = [op1, op2]
ncInput.parameters.itemByName('nc_program_filename').value.value = '1_flat3.0'
ncProgram = cam.ncPrograms.add(ncInput)
ncProgram.postConfiguration = postConfig  # originalmind.cps
postOptions = adsk.cam.NCProgramPostProcessOptions.create()
ncProgram.postProcess(postOptions)
```

- 出力ファイル名は NCProgram 単位で `nc_program_filename` に設定
- ポストプロセッサ: KitMill RZ420 用 originalmind.cps（https://cam.autodesk.com/hsmposts?p=originalmind）

## 5. 輪郭分類に使う設計側 API ✅

- `BRepLoop.isOuter`（bool）: 面の外側ループ判定 → 平板の外形 vs 内穴の区別
- `BRepEdge.geometry` → `Curve3D`。`curve.curveType == adsk.core.Curve3DTypes.Circle3DCurveType` なら `adsk.core.Circle3D` にキャストして `.radius` で径取得
- Fusion API の内部単位は **cm**（径 2.0mm = 0.2）に注意

## 6. その他の事実

- .invhsm-template（Inventor CAM テンプレ）は Fusion では直接読めないが、**拡張子を .f3dhsm-template にリネームすれば読み込める**（内部フォーマット同一、公式サポート記事確認済み）。ジオメトリ参照は持ち越されない
- テンプレファイルは XML（`<template-document>` ルート、`strategy` 属性に contour2d/pocket2d 等、`<tool>` に工具定義が埋め込み）
- 全体リファレンス: Manufacturing Workflow API Sample（Setup→工具→操作→ジオメトリ→生成→NCProgram の一気通貫）
  https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/ManufacturingWorkflowAPISample_Sample.htm
- CAM パラメータ入門: https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/CAMParameters_UM.htm

## 実機確認の結果（2026-07-15、arm1.5mm_1 v5 / Fusion 2703.1.20 のダンプより）

- [x] **contour2d のジオメトリパラメータ名 = `contours`** ✅（`CadContours2dParameterValue`）。
      同型の `stockContours` が別に存在するので型スキャンで拾わないこと
- [x] **固定ボックスストック**: `job_stockMode = "'fixedbox'"`。寸法は `job_stockFixedX/Y/Z`
      （X/Y の既定式 `Math.ceilto(surfaceXHigh - surfaceXLow; job_stockFixedRoundingValue)` が
      「部品範囲を10mm単位で切り上げ・中央配置」を自動でやる。Z のみ板厚に明示設定する。
      丸め幅は `job_stockFixedRoundingValue` = 10mm）
- [x] **WCS 原点**: `wcs_origin_mode = "'stockPoint'"` ＋ `wcs_origin_boxPoint = "'top 1'"`
      （手動セットアップの実測値。ストック上面の角）
- [x] `selectSameDiameter`: contour2d 操作には存在しない（drill 戦略用。v1 では不要と確定）

## 実機確認 TODO（残り）

- [ ] createFromCAMTemplate2 で生成した操作に工具が保持されるか（初回の自動生成で確認）
- [ ] pocket2d のジオメトリパラメータ名（'pockets' 想定。ポケット操作入りのデータをダンプして確定）
- [ ] ローカル .cps を `postConfigurationAtURL` で読み込む際の URL スキーム（file:/// か生パスか）
- [ ] pocket2d テンプレの高さ設定が「選択面基準」になっているか（固定値だと深さがズレる）
- [ ] 面の外向き法線（`evaluator.getNormalAtPoint`）が classifier の想定通りか（底面=-Z、ポケット底=+Z）
