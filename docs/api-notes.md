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

### ボア（bore・ヘリカル穴あけ）✅（実機確認済み 2026-07-16）

```python
parameter = op.parameters.itemByName('circularFaces')  # CadObjectParameterValue
parameter.value.value = faces  # 穴/ざぐりの円筒側面 list[BRepFace]
```

- DLC テンプレ（Inventor 由来）のボアで使用。円形エッジの隣接円筒面を渡す
- ❗ **要更新（無効）状態のツールパスは NCProgram.postProcess が失敗する**。
  `isToolpathValid` での検出はバージョン差で当てにならないため、
  **NC 出力前に無条件で `generateAllToolpaths(True)` を通す**（有効なものはスキップされ高速）

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

- WCS 原点: `wcs_origin_mode`（`'point'` / `'modelPoint'` / `'stockPoint'` 等）
  - ✅ `'point'` モードで確定（2026-07-16 実機）: `wcs_origin_point.value.value = [sketchPoint]` と**リストで代入**する
    （SketchPoint / ConstructionPoint / JointOrigin が使える）。v0.3.2 からこの方式（cam_builder.py `_create_wcs_origin_point`）
  - `'stockPoint'` + `wcs_origin_boxPoint = "'top 1'"` も動くが、**`'top N'` の番号と物理角の対応はモデル依存で不定**
    （角がズレる実例あり）。フォールバック専用とする
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

## 4.5 ポケット系（pocket2d/adaptive2d）のリンク安全化 ✅（実機テンプレXMLで確認 2026-07-16）

テンプレ既定値のままだと、実部材が仮想ストックより大きい KitMill 運用で危険な動きになる：

| パラメータ | テンプレ既定 | 問題 | 上書き値 |
|---|---|---|---|
| `pockets_detectOpenPockets` | true | ストック端に近い領域を「開いたポケット」とみなし**ストック外で刃を下ろして水平進入**（実部材に突っ込む） | `false` |
| `retractionPolicy`（adaptive2d） | 'minimum' | 領域間を**下がったまま**リンク移動（stayDownDistance=5×工具径） | `'full'` |
| `keepToolDown`（pocket2d） | true | 同上（stayDownDistance=50mm） | `false` |

**adaptive2d の進入はヘリカル系のみ**（実機確認 2026-07-16）:
- `rampType` に `'profile'` / `'smooth profile'` / `'zigzag'` を設定しようとすると**拒否される**
- ヘリカルが幅的に成立しない領域（幅 < 工具径×1.25＋余裕）では**垂直プランジにフォールバック**する。
  `minimumRampDiameter` 縮小・`entryPositions` 指定でも、幅が足りない面は救えない
- → **対策は分類側**: ヘリカル不成立幅のポケット面は adaptive を使わず、輪郭パス
  （contour2d・プロファイルランプ、幅≦2×工具径なら1パスで削り切れる）へ振替する（classifier.py）
- `entryPositions`（CadObjectParameterValue、スケッチ点）で進入位置を指定できる。
  ❗ **進入点スケッチはストック上面高さの平面に作ること**。Z=0（底面）の平面に作ると
  点の Z が目標にされ「底まで降りてから横移動」する危険な経路になる（実機確認済み）
- `allowPlunging`（bool）が存在する。false に上書きして明示的にプランジ禁止（保険）
- `predrillPositions` + `rampType='predrill'` という下穴進入もテンプレXML内に存在（未実装・将来の正攻法候補）

pocket2d は helix 指定時に輪郭ランプへのフォールバックが元から有効（`allowContourRamps`）なので
この問題は起きない。

### 開口ポケットの境界拡張スケッチ ✅（実機確認済み 2026-07-16）

部品外形に開いたポケット面は、開口辺をストック余白側へ拡張した境界スケッチで選択する
（cam_builder.py `_build_extended_boundary_sketch`）。実装上の要点：

- **面上にスケッチを作らない**（全エッジが自動投影され境界が二重になる）→ 同じ高さの構築平面に作る
- 連続する開口辺はオフセット線同士を**マイター交点で接続**（各辺ごとに元頂点へ戻すと領域が分断される）
- `SketchSelection` は **`sideType`=内側から開始 を明示設定**（既定だと外側にパスが出る）。
  ループ種別は、床の**穴ループはスケッチに含めず「外側のみ」**（含めると穴の周囲に取り残し）、
  **島（凸）ループは投影して「すべてのループ」**（削り飛ばし防止）。穴/島の判別は
  内側ループの隣接壁が床より上に立つか下に続くかで行う

アドインは操作生成後にこれらを上書きする（cam_builder.py `_apply_safe_linking`、config `force_safe_linking`）。

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

## 7. 整列（Arrange）API ✅（実機確認済み 2026-07-17）

自動配置コマンド（auto_place）用。**2025年1月版で導入**（動作要件に注意）。
確定ソース: tools/DumpArrange の実機ダンプ（Fusion 2704.1.23）＋公式リファレンスの各メソッドページ。

```python
arrange_features = root.features.arrangeFeatures   # 無い場合は旧バージョン
arrange_input = arrange_features.createInput(solver_type)   # ArrangeSolverTypes を渡す
envelope = arrange_input.setPlaneEnvelope(plane, length, width)  # ValueInput（文字列 '280 mm' が明示的）
envelope.frameWidth = ...      # 枠と部品の最小距離（ValueInput）
envelope.objectSpacing = ...   # 部品間の最小間隔（ValueInput）
envelope.originXOffset / originYOffset   # 平面原点からのオフセット（既定 0）
envelope.quantity              # エンベロープ数（既定 -1 = 無制限。板1枚なら 1）
envelope.isPartialArrangeAllowed         # True: 入り切らなくても失敗にせず未整列として返す
arrange_input.definition.globalRotation = adsk.fusion.ArrangeRotationTypes...
arrange_input.definition.isCreateCopies = False   # 原本を移動（コピーを作らない）
comp = arrange_input.arrangeComponents.add(occurrence_or_face)  # Occurrence か BRepFace
feature = arrange_features.add(arrange_input)
feature.arrangeStatistics   # JSON 文字列（Components Arranged / Unarranged 等）
feature.unusedComponents    # 入り切らなかった部品
```

- 列挙値（実機ダンプで確定）:
  - `ArrangeSolverTypes`: `Arrange2DTrueShapeSolverType=0`（要 Nesting 拡張。教育ライセンスは可）、
    `Arrange2DRectangularSolverType=1`（無償）、`Arrange3DSolverType=2`
  - `ArrangeRotationTypes`: `Global=0` / `AllRotations=1`（既定）/ `None=2` / `Only180=3` / `Only90And270=4`
- ❗ `ArrangeFeatures` に公式 Web ドキュメントの `create2DInput/create3DInput` は**実機に存在しない**。
  `createInput(solverType)` が正（ダンプと createInput のリファレンスページで確認）
- ❗ **製造ワークスペースがアクティブなまま `arrangeComponents.add` を呼ぶと
  `RuntimeError: 3 : No fusion asset adapter from active asset adapter`**（実機確認 2026-07-16）。
  整列は設計フィーチャのため、作成の間だけ `FusionSolidEnvironment` を activate して戻す
  （auto_place.py 参照）
- ❗ `arrangeComponents.add(occurrence)` の既定の向きは平板を**上下反転させる**（実機確認 2026-07-17）。
  対策は `ArrangeComponent.isDirectionFlipped = True`（既定の向きから反転＝正立に戻る）。
  **BRepFace 渡しは `RuntimeError: 2 : InternalValidationError : arrange2DDefinition` で使えない**
  （2704.1.23。公式ドキュメントには面渡し＋ `isGlobalDirectionFaceUp` とあるが実機で拒否される）
- ✅ 実機確認済み（2704.1.23、2026-07-17）: 平面 envelope の原点＝構築平面の原点（枠は第一象限、
  originX/YOffset=0 で `[0,280]²`）。トゥルーシェイプの `add()` は教育ライセンスで成功。
  `quantity = 1`（int 代入）可。上下反転の検出は `occurrence.transform2` の (2,2) 成分の符号で可能
- ⚠️ 既存フィーチャの `definition` / `arrangeComponents` / `envelopeDefinition` / `unusedComponents` は
  UI 作成のフィーチャに対して RuntimeError を返した（2704.1.23。参照時は try で包むこと）
- トゥルーシェイプで詰めた場合、bbox 距離は 8mm を切ることがある
  → 配置チェック（bbox 目安）の間隔警告は誤検知になり得る

## 実機確認の結果（2026-07-15、arm1.5mm_1 v5 / Fusion 2703.1.20 のダンプより）

- [x] **contour2d のジオメトリパラメータ名 = `contours`** ✅（`CadContours2dParameterValue`）。
      同型の `stockContours` が別に存在するので型スキャンで拾わないこと
- [x] **固定ボックスストック**: `job_stockMode = "'fixedbox'"`。寸法は `job_stockFixedX/Y/Z`
      （X/Y の既定式 `Math.ceilto(surfaceXHigh - surfaceXLow; job_stockFixedRoundingValue)` が
      「部品範囲を10mm単位で切り上げ・中央配置」を自動でやる。Z のみ板厚に明示設定する。
      丸め幅は `job_stockFixedRoundingValue` = 10mm）
      ❗ ただし**ワークが10mmの倍数だと余白ゼロ＝ストックとワークが同寸になり、外郭パスが
      省略される**（実機確認 2026-07-16）。このため v0.3.1 から**相対ボックス**
      （`job_stockMode = "'default'"` ＋ `job_stockOffsetSides` で側面余白、上下 0）に変更した
- [x] **WCS 原点**: `wcs_origin_mode = "'point'"` ＋ `wcs_origin_point` に SketchPoint を
      リスト代入（`.value.value = [sketchPoint]`）で確定（実機確認 2026-07-16）。
      旧方式 `'stockPoint'` ＋ `'top 1'` は 'top N' と物理角の対応が不定なためフォールバック専用
- [x] `selectSameDiameter`: contour2d 操作には存在しない（drill 戦略用。v1 では不要と確定）

## 実機確認 TODO（残り）

- [ ] createFromCAMTemplate2 で生成した操作に工具が保持されるか（初回の自動生成で確認）
- [ ] pocket2d のジオメトリパラメータ名（'pockets' 想定。ポケット操作入りのデータをダンプして確定）
- [ ] ローカル .cps を `postConfigurationAtURL` で読み込む際の URL スキーム（file:/// か生パスか）
- [ ] pocket2d テンプレの高さ設定が「選択面基準」になっているか（固定値だと深さがズレる）
- [ ] 面の外向き法線（`evaluator.getNormalAtPoint`）が classifier の想定通りか（底面=-Z、ポケット底=+Z）
