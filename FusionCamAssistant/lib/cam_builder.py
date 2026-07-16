# セットアップ生成 → テンプレートから操作生成 → ジオメトリ割当 → ツールパス生成
#
# 使用 API は docs/api-notes.md 準拠（createFromCAMTemplate2 / CurveSelections）。
# ⚠️ 一部のパラメータ名は実機未確認のため、候補名リストを順に試し、失敗したものは
#    ログに残す方針にしている。tools/DumpParameters の結果で確定したら定数を修正すること。
#    WCS 原点は 'point' モード＋スケッチ点のリスト代入で実機確定（2026-07-16）。
#    失敗時のみ 'stockPoint' + 'top 1' へフォールバックする。

import hashlib
import math
import os
import shutil
import tempfile
import time
import traceback
import xml.etree.ElementTree as ET

import adsk
import adsk.cam
import adsk.core

from . import fusion_utils
from . import template_registry as tr

# --- パラメータ名（2026-07-15 実機ダンプ arm1.5mm_1 v5 / Fusion 2703.1.20 で確定） ---
# contour2d のジオメトリパラメータは 'contours' で確定。
# 同型の 'stockContours' が別に存在するため、型スキャンより名前一致を優先すること。
CONTOUR_PARAM_CANDIDATES = ['contours']
# pocket2d 系操作のジオメトリパラメータ名の候補（⚠️ pocket 操作は未ダンプ・実機未確認）
POCKET_PARAM_CANDIDATES = ['pockets']
# WCS 原点のフォールバック: 手動セットアップの実測値（ストック点モード＋上面の角 'top 1'）。
# 'top N' の番号と物理角の対応はモデル依存で不定（角がズレる実例あり）のため、
# 本命は 'point' モード＋自前計算のスケッチ点（_create_setup 参照）。失敗時のみ使う。
WCS_ORIGIN_MODE_CANDIDATES = ["'stockPoint'"]
WCS_BOX_POINT_CANDIDATES = ["'top 1'"]
# bore（ヘリカル穴あけ）の円筒面選択パラメータ名の候補（⚠️ 実機未確認。
# 見つからない場合は CadObjectParameterValue の候補名をログに出すので、それで確定する）
BORE_PARAM_CANDIDATES = ['circularFaces', 'holeFaces', 'boreFaces']
# 領域加工（ポケット系）の戦略名
POCKET_STRATEGIES = ('pocket2d', 'adaptive2d')
# ポケット系の進入・リンクの安全化上書き（テンプレ値だと、実部材が仮想ストックより
# 大きい運用で「ストック外で刃を下ろして水平進入」「下がったままの領域間移動」が起こる）。
# 存在しないパラメータは黙ってスキップする。
SAFE_LINKING_OVERRIDES = {
    'adaptive2d': [
        ('pockets_detectOpenPockets', ['false']),   # 開いたポケット扱い＝ストック外進入を禁止
        ('retractionPolicy', ["'full'", "'all'"]),  # 領域間は必ず退避高さへ
        # adaptive2d のランプは helix 系のみ（profile/zigzag は拒否される。実機確認済み）。
        # ヘリカル下限径を絞り、進入点（entryPositions）と併用して細い領域でも成立させる。
        # ※ヘリカルが成立しない細幅の面は classifier 側で輪郭パスへ振替される
        ('minimumRampDiameter', ['tool_diameter * 0.25']),
        ('allowPlunging', ['false']),               # 垂直プランジの明示禁止
    ],
    'pocket2d': [
        ('pockets_detectOpenPockets', ['false']),
        ('keepToolDown', ['false']),                # 下がったままの移動を禁止
        ('allowPlunging', ['false']),
        # rampType は 'helix' のまま（pocket2d は helix 指定時に輪郭ランプへの
        # フォールバックが有効: allowContourRamps）
    ],
}
# -------------------------------------------------------------------------------

_TEMPLATE_NS = 'http://www.hsmworks.com/namespace/hsmworks/document/template'


class BuildReport:
    def __init__(self):
        self.created = []    # (操作名, 選択数)
        self.failed = []     # (ラベル, 理由)
        self.notes = []      # セットアップ設定などの注意

    def summary(self):
        lines = []
        if self.created:
            lines.append('生成した操作:')
            lines += [f'  {name}（選択 {count} 件）' for name, count in self.created]
        if self.failed:
            lines.append('失敗:')
            lines += [f'  {label}: {reason}' for label, reason in self.failed]
        if self.notes:
            lines.append('注意:')
            lines += [f'  {note}' for note in self.notes]
        lines.append('')
        lines.append('必ずシミュレーションで全データを確認してください（ボトム高さ・ピッチ・衝突）。')
        return '\n'.join(lines)


def build(cam, classify_result, plan_items, config):
    """確認済みの PlanItem 群からセットアップと操作を一括生成する。"""
    report = BuildReport()
    setup = _create_setup(cam, classify_result, config, report)
    entry_points_map = _prepare_entry_positions(plan_items, classify_result)

    sequence = 0
    created_operations = []
    for item in plan_items:
        if not item.enabled or item.template is None or item.selection_count == 0:
            continue
        sequence += 1
        try:
            operation = _create_operation_from_template(setup, item.template)
            _assign_geometry(operation, item, config)
            if config.get('force_safe_linking', True):
                _apply_safe_linking(operation, item)
                _apply_entry_positions(operation, entry_points_map.get(id(item)))
            if getattr(item, 'floor_contour', False):
                # ポケット床の輪郭パス: 貫通用テンプレの「輪郭から-0.1mm」を床ちょうどに補正
                _try_set(operation.parameters, 'bottomHeight_offset', '0 mm', None)
            operation.name = f'{sequence:02d}_{item.template.name}'
            report.created.append((operation.name, item.selection_count))
            created_operations.append(operation)
        except Exception:
            report.failed.append((item.label, traceback.format_exc(limit=3)))
            fusion_utils.log(f'操作生成失敗 {item.label}:\n{traceback.format_exc()}')

    if report.created:
        try:
            future = cam.generateAllToolpaths(True)
            if _wait_for_generation(future, timeout_seconds=120):
                _delete_empty_operations(created_operations, report)
            else:
                report.notes.append('ツールパスを生成中です（完了までしばらくかかります）。'
                                    '空のツールパスが残った場合は手動で削除してください。')
        except Exception:
            report.notes.append('ツールパスの自動生成に失敗。手動で「生成」してください。')
    return report


def _wait_for_generation(future, timeout_seconds):
    """ツールパス生成の完了を UI を固めずに待つ。タイムアウトで False。"""
    deadline = time.monotonic() + timeout_seconds
    try:
        while not future.isGenerationCompleted:
            if time.monotonic() > deadline:
                return False
            adsk.doEvents()
            time.sleep(0.1)
        return True
    except Exception:
        return False


def _delete_empty_operations(operations, report):
    """空のツールパス（No passes to link 等）になった操作を削除する。"""
    for operation in operations:
        try:
            if not operation.hasToolpath:
                name = operation.name
                operation.deleteMe()
                report.notes.append(f'空のツールパスだったため削除: {name}')
        except Exception:
            fusion_utils.log('空ツールパス削除に失敗:\n' + traceback.format_exc())


SETUP_NAME = '自動CAM'
ENTRY_SKETCH_NAME = '自動CAM_進入点'
WCS_SKETCH_NAME = '自動CAM_原点'
# 旧名（v0.1 初期）も置き換え・NC出力の対象にして、過去に生成したセットアップとの互換を保つ
LEGACY_SETUP_NAMES = ('QUHP 自動セットアップ',)
SETUP_NAME_PREFIXES = (SETUP_NAME,) + LEGACY_SETUP_NAMES


def _create_setup(cam, classify_result, config, report):
    # 再実行で自動セットアップが増殖しないよう、既存の同名セットアップを置き換える
    removed = 0
    for i in reversed(range(cam.setups.count)):
        existing = cam.setups.item(i)
        try:
            if existing.name.startswith(SETUP_NAME_PREFIXES):
                existing.deleteMe()
                removed += 1
        except Exception:
            pass
    if removed:
        report.notes.append(f'既存の「{SETUP_NAME}」{removed} 件を置き換えました。')

    # 旧い原点スケッチ・構築平面を掃除（参照元のセットアップは上で削除済み）
    try:
        design = fusion_utils.active_design()
        if design is not None:
            root = design.rootComponent
            for i in reversed(range(root.sketches.count)):
                if root.sketches.item(i).name == WCS_SKETCH_NAME:
                    root.sketches.item(i).deleteMe()
            for i in reversed(range(root.constructionPlanes.count)):
                if root.constructionPlanes.item(i).name == WCS_SKETCH_NAME:
                    root.constructionPlanes.item(i).deleteMe()
    except Exception:
        fusion_utils.log('旧原点スケッチの削除に失敗:\n' + traceback.format_exc())

    setup_input = cam.setups.createInput(adsk.cam.OperationTypes.MillingOperation)
    setup_input.models = list(classify_result.bodies)
    setup = cam.setups.add(setup_input)
    setup.name = SETUP_NAME

    # ストック: モデル相対ボックス。側面に余白を付けて、ワーク＝ストックになって
    # 外郭パスが省略される問題を防ぐ（固定ボックスの10mm切り上げだと、ワークが
    # ちょうど10mmの倍数のとき余白ゼロになる）。上下は板厚ちょうど（切り上げ0）。
    side_margin = config.get('stock_side_margin_mm', 5.0)
    try:
        setup.stockMode = adsk.cam.SetupStockModes.RelativeBoxStock
    except Exception:
        pass
    _try_set(setup.parameters, 'job_stockMode', "'default'", report)  # 'default'=相対ボックス
    _try_set(setup.parameters, 'job_stockOffsetMode', "'simple'", report)
    _try_set(setup.parameters, 'job_stockOffsetSides', f'{side_margin:g} mm', report)
    _try_set(setup.parameters, 'job_stockOffsetTop', '0 mm', report)
    _try_set(setup.parameters, 'job_stockOffsetBottom', '0 mm', report)

    # WCS 原点: ストック上面・左下手前（-X/-Y側）の角＝機械側のゼロ合わせ位置。
    # 'stockPoint' の 'top N' は番号と物理角の対応がモデル依存で不定なため、
    # 角の座標を自前計算したスケッチ点を 'point' モードで明示指定する。
    origin_set = False
    try:
        origin_point = _create_wcs_origin_point(classify_result, config)
        if origin_point is not None and \
                _try_set(setup.parameters, 'wcs_origin_mode', "'point'", None):
            setup.parameters.itemByName('wcs_origin_point').value.value = [origin_point]
            origin_set = True
            report.notes.append('原点: ストック上面・左下手前の角（自動設定）')
    except Exception:
        fusion_utils.log('WCS原点のスケッチ点指定に失敗:\n' + traceback.format_exc())
    if not origin_set:
        # フォールバック: 従来のストック点モード（角がズレる場合がある）
        for mode in WCS_ORIGIN_MODE_CANDIDATES:
            if _try_set(setup.parameters, 'wcs_origin_mode', mode, None):
                for box_point in WCS_BOX_POINT_CANDIDATES:
                    if _try_set(setup.parameters, 'wcs_origin_boxPoint', box_point, None):
                        origin_set = True
                        break
            if origin_set:
                break
        if origin_set:
            report.notes.append(
                '原点をスケッチ点で指定できず、ストック点（top 1）にフォールバックしました。'
                '原点の角が「左下手前」になっているか必ず確認してください。')
    if not origin_set:
        report.notes.append(
            '原点をストック左下に設定できませんでした。セットアップ編集で手動設定してください'
            '（DumpParameters の結果で cam_builder.py の候補名を更新すると自動化されます）。')
    return setup


def _create_wcs_origin_point(classify_result, config):
    """ストック上面・左下手前（-X/-Y側）の角にスケッチ点を作って返す。失敗時は None。
    ストックは相対ボックス（側面余白 stock_side_margin_mm・上下 0）なので、
    ボディの bbox から角の座標（内部単位 cm）を厳密に計算できる。
    ※進入点スケッチと同じく、スケッチは必ず「ストック上面の高さ」の構築平面に作る。
    平面作成に失敗した場合は Z がズレた原点になり危険なので、黙って XY 平面に
    落とさず例外のまま呼び出し元へ返す（フォールバック経路に入る）。"""
    design = fusion_utils.active_design()
    if design is None:
        return None
    boxes = [body.boundingBox for body in classify_result.bodies]
    if not boxes:
        return None
    margin_cm = fusion_utils.mm_to_cm(config.get('stock_side_margin_mm', 5.0))
    x_cm = min(box.minPoint.x for box in boxes) - margin_cm
    y_cm = min(box.minPoint.y for box in boxes) - margin_cm
    top_z_cm = max(box.maxPoint.z for box in boxes)

    root = design.rootComponent
    sketch_plane = root.xYConstructionPlane
    if abs(top_z_cm) > 1e-6:
        plane_input = root.constructionPlanes.createInput()
        plane_input.setByOffset(root.xYConstructionPlane,
                                adsk.core.ValueInput.createByReal(top_z_cm))
        plane = root.constructionPlanes.add(plane_input)
        plane.name = WCS_SKETCH_NAME
        plane.isLightBulbOn = False
        sketch_plane = plane
    sketch = root.sketches.add(sketch_plane)
    sketch.name = WCS_SKETCH_NAME
    local = sketch.modelToSketchSpace(
        adsk.core.Point3D.create(x_cm, y_cm, top_z_cm))
    point = sketch.sketchPoints.add(local)
    try:
        sketch.isLightBulbOn = False
    except Exception:
        pass
    return point


def _extract_sub_template(source_path, sub_index, output_path):
    """複数テンプレ文書（Inventor 由来の操作セット）から指定の <template> だけを
    含む単一テンプレ文書を書き出す。"""
    ET.register_namespace('', _TEMPLATE_NS)
    root = ET.parse(source_path).getroot()
    ns = '{%s}' % _TEMPLATE_NS
    elements = root.findall(f'{ns}template')
    if sub_index >= len(elements):
        raise IndexError(f'テンプレート番号 {sub_index} が見つかりません: {source_path}')
    new_root = ET.Element(root.tag, dict(root.attrib))
    description = root.find(f'{ns}user-description')
    if description is not None:
        new_root.append(description)
    new_root.append(elements[sub_index])
    ET.ElementTree(new_root).write(output_path, encoding='UTF-8', xml_declaration=True)


def _load_cam_template(template):
    """CAMTemplate を読み込む。
    - createFromFile は内部で ANSI 変換するため日本語・Φ入りパスで失敗する
      （実機確認済み）→ ASCII 名の一時ファイル経由で読み込む
    - 複数テンプレ文書（sub_index あり）は該当テンプレのみ抽出してから読み込む"""
    digest_source = f'{template.path}#{template.sub_index}'
    digest = hashlib.md5(digest_source.encode('utf-8')).hexdigest()[:12]
    temp_path = os.path.join(tempfile.gettempdir(),
                             f'fusioncam_{digest}.f3dhsm-template')
    if not os.path.isfile(temp_path):
        if template.sub_index is None:
            shutil.copyfile(template.path, temp_path)
        else:
            _extract_sub_template(template.path, template.sub_index, temp_path)
    return adsk.cam.CAMTemplate.createFromFile(temp_path)


def _create_operation_from_template(setup, template):
    cam_template = _load_cam_template(template)
    template_input = adsk.cam.CreateFromCAMTemplateInput.create()
    template_input.camTemplate = cam_template
    results = setup.createFromCAMTemplate2(template_input)
    if not results or len(results) == 0:
        raise RuntimeError(f'テンプレートから操作が生成されませんでした: {template.name}')
    for created in results:
        operation = adsk.cam.Operation.cast(created)
        if operation:
            return operation
    raise RuntimeError(f'生成結果に操作が含まれていません: {template.name}')


def _find_contours_param(operation, preferred_names):
    """ジオメトリ選択パラメータ（CadContours2dParameterValue）を探す。"""
    for name in preferred_names:
        parameter = operation.parameters.itemByName(name)
        if parameter and adsk.cam.CadContours2dParameterValue.cast(parameter.value):
            return parameter
    # 名前候補で見つからなければ型でスキャン（見つけた名前はログに残す）
    # 'stockContours' は同型の別パラメータ（ストック輪郭）なので除外する
    for i in range(operation.parameters.count):
        parameter = operation.parameters.item(i)
        if parameter.name == 'stockContours':
            continue
        try:
            if adsk.cam.CadContours2dParameterValue.cast(parameter.value):
                fusion_utils.log(
                    f'ジオメトリパラメータを型スキャンで発見: {parameter.name!r}'
                    '（cam_builder.py の候補名に追加してください）')
                return parameter
        except Exception:
            continue
    return None


def _edge_points(edge):
    evaluator = edge.evaluator
    ok, t_start, t_end = evaluator.getParameterExtents()
    ok2, points = evaluator.getStrokes(t_start, t_end, 0.01)
    if not (ok and ok2) or len(points) < 2:
        return None
    return list(points)


def _signed_area_from_first_edge(loop_edges):
    """先頭エッジの自然方向から始めてループを連結順にたどり、
    +Z から見た符号付き面積を返す（正=反時計回り）。失敗時 None。"""
    points = _edge_points(loop_edges[0])
    if points is None:
        return None
    points = list(points)
    remaining = list(loop_edges[1:])
    tolerance = 1e-3  # cm
    while remaining:
        found = False
        for i, edge in enumerate(remaining):
            edge_pts = _edge_points(edge)
            if edge_pts is None:
                return None
            if points[-1].distanceTo(edge_pts[0]) < tolerance:
                points.extend(edge_pts[1:])
            elif points[-1].distanceTo(edge_pts[-1]) < tolerance:
                points.extend(list(reversed(edge_pts))[1:])
            else:
                continue
            remaining.pop(i)
            found = True
            break
        if not found:
            return None
    area = 0.0
    count = len(points)
    for i in range(count):
        p1 = points[i]
        p2 = points[(i + 1) % count]
        area += p1.x * p2.y - p2.x * p1.y
    return area / 2.0


def _face_boundary_points(face):
    """面の全ループの折れ線近似点（XY判定用）。"""
    points = []
    for loop in face.loops:
        for edge in loop.edges:
            stroke = _edge_points(edge)
            if stroke:
                points.extend(stroke)
    return points


def _interior_point(face):
    """面上で境界から最も遠い点（ヘリカル進入に最適な場所）をサンプリングで求める。"""
    try:
        evaluator = face.evaluator
        parametric_range = evaluator.parametricRange()  # BoundingBox2D を直接返す
        if parametric_range is None:
            return None
        boundary = _face_boundary_points(face)
        if not boundary:
            return None
        best_point = None
        best_distance = -1.0
        grid = 15
        for i in range(1, grid):
            for j in range(1, grid):
                u = parametric_range.minPoint.x + \
                    (parametric_range.maxPoint.x - parametric_range.minPoint.x) * i / grid
                v = parametric_range.minPoint.y + \
                    (parametric_range.maxPoint.y - parametric_range.minPoint.y) * j / grid
                parameter = adsk.core.Point2D.create(u, v)
                if not evaluator.isParameterOnFace(parameter):
                    continue
                ok2, world = evaluator.getPointAtParameter(parameter)
                if not ok2:
                    continue
                distance = min((world.x - b.x) ** 2 + (world.y - b.y) ** 2
                               for b in boundary)
                if distance > best_distance:
                    best_distance = distance
                    best_point = world
        return best_point
    except Exception:
        return None


ENTRY_PLANE_NAME = '自動CAM_進入点平面'
BOUNDARY_SKETCH_PREFIX = '自動CAM_境界'
_boundary_sketch_counter = 0


def _prepare_entry_positions(items, classify_result):
    """負荷制御（adaptive2d）の各ポケット面に対し、境界から最も遠い内部点の
    進入点スケッチを作る。Fusion 任せだと端の細い場所で垂直プランジに落ちるため、
    ヘリカルが確実に成立する場所から進入させる。
    ※スケッチは必ず「ストック上面の高さ」の平面に作ること。Z=0（底面）の平面に
    作ると進入点の Z が目標にされ、底の深さまで降りてから横移動する危険な動きになる。"""
    adaptive_items = [
        item for item in items
        if item.enabled and item.template is not None and item.faces
        and (item.template.strategy or '').lower() == 'adaptive2d']
    design = fusion_utils.active_design()
    if design is None:
        return {}
    global _boundary_sketch_counter
    _boundary_sketch_counter = 0
    root = design.rootComponent
    for i in reversed(range(root.sketches.count)):
        sketch = root.sketches.item(i)
        if sketch.name == ENTRY_SKETCH_NAME or \
                sketch.name.startswith(BOUNDARY_SKETCH_PREFIX):
            sketch.deleteMe()
    for i in reversed(range(root.constructionPlanes.count)):
        plane = root.constructionPlanes.item(i)
        if plane.name == ENTRY_PLANE_NAME or \
                plane.name.startswith(BOUNDARY_SKETCH_PREFIX):
            plane.deleteMe()
    if not adaptive_items:
        return {}

    top_z = max((body.boundingBox.maxPoint.z for body in classify_result.bodies),
                default=0.0)
    sketch_plane = root.xYConstructionPlane
    if abs(top_z) > 1e-6:
        try:
            plane_input = root.constructionPlanes.createInput()
            plane_input.setByOffset(root.xYConstructionPlane,
                                    adsk.core.ValueInput.createByReal(top_z))
            plane = root.constructionPlanes.add(plane_input)
            plane.name = ENTRY_PLANE_NAME
            plane.isLightBulbOn = False
            sketch_plane = plane
        except Exception:
            fusion_utils.log('進入点平面の作成に失敗（XY平面を使用）:\n'
                             + traceback.format_exc())

    sketch = root.sketches.add(sketch_plane)
    sketch.name = ENTRY_SKETCH_NAME
    points_by_item = {}
    for item in adaptive_items:
        sketch_points = []
        for face in item.faces:
            world = _interior_point(face)
            if world is None:
                fusion_utils.log(f'{item.label}: 進入点を計算できない面があります')
                continue
            local = sketch.modelToSketchSpace(
                adsk.core.Point3D.create(world.x, world.y, top_z))
            sketch_points.append(sketch.sketchPoints.add(local))
        if sketch_points:
            points_by_item[id(item)] = sketch_points
    try:
        sketch.isLightBulbOn = False
    except Exception:
        pass
    return points_by_item


def _apply_entry_positions(operation, sketch_points):
    """adaptive2d の entryPositions に進入点を設定する。"""
    if not sketch_points:
        return
    parameter = operation.parameters.itemByName('entryPositions')
    if parameter is None:
        return
    value = adsk.cam.CadObjectParameterValue.cast(parameter.value)
    if value is None:
        return
    try:
        value.value = list(sketch_points)
    except Exception:
        fusion_utils.log('entryPositions の設定に失敗:\n' + traceback.format_exc())


def _apply_safe_linking(operation, item):
    """ポケット系（負荷制御/ポケット）の進入・リンクを安全側に上書きする。
    実部材は仮想ストックより大きいことが多く、ストック外は空気という前提の
    進入・移動は実材料への突っ込みになるため。"""
    strategy = (item.template.strategy or '').lower() if item.template else ''
    overrides = SAFE_LINKING_OVERRIDES.get(strategy)
    if not overrides:
        return
    for name, expressions in overrides:
        parameter = operation.parameters.itemByName(name)
        if parameter is None:
            continue
        for expression in expressions:
            try:
                parameter.expression = expression
                break
            except Exception:
                continue
        else:
            fusion_utils.log(f'{item.label}: 安全化パラメータ {name} を設定できませんでした')


def _assign_geometry(operation, item, config):
    strategy = (item.template.strategy or '').lower() if item.template else ''
    if strategy == 'bore':
        _assign_bore_faces(operation, item)
    elif strategy in POCKET_STRATEGIES:
        _assign_pockets(operation, item, config)
    else:
        _assign_contours(operation, item)


def _boundary_sketch_plane(root, z_cm):
    """境界スケッチ用の構築平面（面の高さ）。面の上に直接スケッチを作ると
    Fusion が面の全エッジ（開口辺含む）を自動投影してしまい境界が二重になるため、
    同じ高さの構築平面に作る。"""
    plane_input = root.constructionPlanes.createInput()
    plane_input.setByOffset(root.xYConstructionPlane,
                            adsk.core.ValueInput.createByReal(z_cm))
    plane = root.constructionPlanes.add(plane_input)
    plane.name = f'{BOUNDARY_SKETCH_PREFIX}平面{_boundary_sketch_counter}'
    plane.isLightBulbOn = False
    return plane


def _ordered_coedge_entries(loop, outward_by_edge, margin_cm):
    """外周ループをコエッジ順にたどり、辺ごとの情報を返す。
    entry = (edge, is_open, start(x,y), end(x,y), offset(dx,dy))"""
    entries = []
    for i in range(loop.coEdges.count):
        coedge = loop.coEdges.item(i)
        edge = coedge.edge
        start = edge.startVertex.geometry
        end = edge.endVertex.geometry
        if coedge.isOpposedToEdge:
            start, end = end, start
        normal = outward_by_edge.get(edge.tempId)
        is_open = (normal is not None and
                   edge.geometry.curveType == adsk.core.Curve3DTypes.Line3DCurveType)
        offset = None
        if is_open:
            length = math.hypot(normal.x, normal.y)
            if length < 1e-9:
                is_open = False
            else:
                offset = (normal.x / length * margin_cm, normal.y / length * margin_cm)
        entries.append((edge, is_open, (start.x, start.y), (end.x, end.y), offset))
    return entries


def _offset_line_intersection(entry_a, entry_b):
    """隣接する開口辺のオフセット線同士の交点（マイター接続）。平行なら None。"""
    _, _, a_start, a_end, a_off = entry_a
    _, _, b_start, b_end, b_off = entry_b
    p1 = (a_start[0] + a_off[0], a_start[1] + a_off[1])
    u1 = (a_end[0] - a_start[0], a_end[1] - a_start[1])
    p2 = (b_start[0] + b_off[0], b_start[1] + b_off[1])
    u2 = (b_end[0] - b_start[0], b_end[1] - b_start[1])
    denom = u1[0] * u2[1] - u1[1] * u2[0]
    norm = math.hypot(*u1) * math.hypot(*u2)
    if norm < 1e-12 or abs(denom) / norm < 1e-6:
        return None
    t = ((p2[0] - p1[0]) * u2[1] - (p2[1] - p1[1]) * u2[0]) / denom
    return (p1[0] + u1[0] * t, p1[1] + u1[1] * t)


def _inner_loop_is_boss(face, loop, z_cm):
    """床面の内側ループが「島（床より上に立つ凸）」か「穴（床より下）」かを判別。
    判別できない場合は島扱い（残す＝安全側）。"""
    try:
        edge = loop.edges.item(0)
        for adjacent in edge.faces:
            if adjacent != face:
                return adjacent.boundingBox.maxPoint.z > z_cm + 0.005
    except Exception:
        pass
    return True


def _configure_sketch_selection(selection, has_islands, label):
    """スケッチ境界選択のループ/サイドを設定する。
    - サイド「内側から開始」: 設定しないとスケッチの外側にパスが生成される（実機報告）
    - ループ: 島があるときは全ループ（島を残す）、無ければ外側のみ
      （床の穴ループを境界にすると穴の周囲に取り残しが出るため）"""
    loop_candidates = ('AllLoops',) if has_islands else ('OnlyOutsideLoops', 'OutsideLoops')
    loop_set = False
    for name in loop_candidates:
        value = getattr(adsk.cam.LoopTypes, name, None)
        if value is None:
            continue
        try:
            selection.loopTypes = value
            loop_set = True
            break
        except Exception:
            continue
    if not loop_set:
        fusion_utils.log(f'{label}: スケッチ選択のループ設定に失敗'
                         '（ダイアログで「外側のループ」を手動設定してください）')
    side_set = False
    for name in ('StartInsideSideType', 'AlwaysInsideSideType', 'InsideSideType'):
        value = getattr(adsk.cam.SideTypes, name, None)
        if value is None:
            continue
        for attribute in ('sideType', 'sideTypes'):
            try:
                setattr(selection, attribute, value)
                side_set = True
                break
            except Exception:
                continue
        if side_set:
            break
    if not side_set:
        fusion_utils.log(f'{label}: スケッチ選択のサイド設定に失敗'
                         '（ダイアログで「内側から開始」を手動設定してください）')


def _build_extended_boundary_sketch(face, open_edges, margin_cm):
    """開口辺をストック余白側へ margin_cm 平行移動した「閉じた」境界スケッチを作る。
    - 閉じた辺・島はエッジをそのまま投影（形状は正確に維持）
    - 連続する開口辺はオフセット線同士を交点（マイター）で接続し、
      多角形の開口でも一続きの拡張領域になるようにする
    ユーザーが手作業でやっていた「スケッチで輪郭を描いて境界を広げる」の自動化。"""
    global _boundary_sketch_counter
    design = fusion_utils.active_design()
    if design is None:
        return None
    root = design.rootComponent
    _boundary_sketch_counter += 1
    try:
        z_cm = face.pointOnFace.z
        plane = _boundary_sketch_plane(root, z_cm)
        sketch = root.sketches.add(plane)
    except Exception:
        fusion_utils.log('境界スケッチの作成に失敗:\n' + traceback.format_exc())
        return None
    sketch.name = f'{BOUNDARY_SKETCH_PREFIX}{_boundary_sketch_counter}'
    outward_by_edge = {edge.tempId: normal for edge, normal in open_edges}
    lines = sketch.sketchCurves.sketchLines

    def add_polyline(points_xy):
        for (x1, y1), (x2, y2) in zip(points_xy, points_xy[1:]):
            if math.hypot(x2 - x1, y2 - y1) > 1e-6:
                p = sketch.modelToSketchSpace(adsk.core.Point3D.create(x1, y1, z_cm))
                q = sketch.modelToSketchSpace(adsk.core.Point3D.create(x2, y2, z_cm))
                lines.addByTwoPoints(p, q)

    has_islands = False
    try:
        for loop in face.loops:
            if not loop.isOuter:
                # 島（凸）だけ投影して残す。穴ループは含めない
                # （境界にすると穴の周囲に取り残しが出るため。穴は後工程で加工される）
                if _inner_loop_is_boss(face, loop, z_cm):
                    has_islands = True
                    for edge in loop.edges:
                        sketch.project(edge)
                continue
            entries = _ordered_coedge_entries(loop, outward_by_edge, margin_cm)
            count = len(entries)
            if count == 0:
                continue
            has_open = any(e[1] for e in entries)
            if not has_open:
                for edge, *_ in entries:
                    sketch.project(edge)
                continue
            # 開口辺の「連続区間」ごとに、閉じた辺は投影・開口区間はオフセット折れ線を描く。
            # 区間の先頭が閉じた辺になるよう起点を回転（全部開口なら回転不要）
            start_index = next((i for i, e in enumerate(entries) if not e[1]), 0)
            ordered = entries[start_index:] + entries[:start_index]
            all_open = all(e[1] for e in ordered)
            i = 0
            while i < len(ordered):
                entry = ordered[i]
                if not entry[1]:
                    sketch.project(entry[0])
                    i += 1
                    continue
                # 連続する開口辺の区間を集める
                run = [entry]
                while i + len(run) < len(ordered) and ordered[i + len(run)][1]:
                    run.append(ordered[i + len(run)])
                points = []
                if not all_open:
                    points.append(run[0][2])  # 区間開始の元頂点から張り出しへ
                points.append((run[0][2][0] + run[0][4][0], run[0][2][1] + run[0][4][1]))
                for a, b in zip(run, run[1:]):
                    joint = _offset_line_intersection(a, b)
                    if joint is None:  # 平行（同一直線上）: それぞれの端点を直結
                        points.append((a[3][0] + a[4][0], a[3][1] + a[4][1]))
                        points.append((b[2][0] + b[4][0], b[2][1] + b[4][1]))
                    else:
                        points.append(joint)
                points.append((run[-1][3][0] + run[-1][4][0], run[-1][3][1] + run[-1][4][1]))
                if not all_open:
                    points.append(run[-1][3])  # 張り出しから区間終了の元頂点へ戻る
                else:
                    points.append(points[0])  # 全周開口: オフセット多角形を閉じる
                add_polyline(points)
                i += len(run)
    except Exception:
        fusion_utils.log('境界スケッチの構築に失敗:\n' + traceback.format_exc())
        try:
            sketch.deleteMe()
        except Exception:
            pass
        return None
    try:
        sketch.isLightBulbOn = False
    except Exception:
        pass
    return sketch, has_islands


def _assign_bore_faces(operation, item):
    """ボア（ヘリカル穴あけ）: 穴/ざぐりの円筒側面を選択する。"""
    if not item.cylinders:
        raise RuntimeError('円筒側面が取得できていません（穴の側面が円筒でない可能性）')
    parameter = None
    for name in BORE_PARAM_CANDIDATES:
        candidate = operation.parameters.itemByName(name)
        if candidate and adsk.cam.CadObjectParameterValue.cast(candidate.value):
            parameter = candidate
            break
    if parameter is None:
        found = []
        for i in range(operation.parameters.count):
            candidate = operation.parameters.item(i)
            try:
                if adsk.cam.CadObjectParameterValue.cast(candidate.value):
                    found.append(candidate.name)
            except Exception:
                continue
        fusion_utils.log(f'ボアの図形パラメータ候補（実機ダンプ）: {found}')
        raise RuntimeError('ボアの図形選択パラメータが見つかりません'
                           '（テキストコマンドの候補名を cam_builder.py に追加してください）')
    fusion_utils.log(f'ボアの図形パラメータ: {parameter.name!r} を使用')
    parameter.value.value = list(item.cylinders)


def _assign_pockets(operation, item, config):
    """ポケット/負荷制御: 底面（faces）または閉ループ（loops=くり抜き）を領域として選択。
    部品外形に開いている面は、開口辺を余白側へ拡張した境界スケッチで選択する
    （ストック端付近での進入の逃げ場を作り、実材料への突っ込みを防ぐ）。"""
    parameter = _find_contours_param(operation, POCKET_PARAM_CANDIDATES)
    if parameter is None:
        raise RuntimeError('ポケット選択パラメータが見つかりません')
    contours_value = adsk.cam.CadContours2dParameterValue.cast(parameter.value)
    selections = contours_value.getCurveSelections()
    selections.clear()
    if item.faces:
        open_map = getattr(item, 'open_edge_map', {}) or {}
        margin_cm = fusion_utils.mm_to_cm(config.get('stock_side_margin_mm', 5.0))
        plain_faces = []
        for face in item.faces:
            open_edges = open_map.get(face.tempId)
            if open_edges:
                built = _build_extended_boundary_sketch(face, open_edges, margin_cm)
                if built is not None:
                    sketch, has_islands = built
                    sketch_selection = selections.createNewSketchSelection()
                    sketch_selection.inputGeometry = [sketch]
                    _configure_sketch_selection(sketch_selection, has_islands, item.label)
                    continue
                fusion_utils.log(f'{item.label}: 境界拡張に失敗（面選択にフォールバック）')
            plain_faces.append(face)
        if plain_faces:
            pocket_selection = selections.createNewPocketSelection()
            pocket_selection.inputGeometry = plain_faces
    else:
        # 貫通開口のくり抜き: 閉チェーンを領域境界として選択（領域加工なので向きは不問）
        for loop_edges in item.loops:
            chain = selections.createNewChainSelection()
            chain.inputGeometry = list(loop_edges)
    contours_value.applyCurveSelections(selections)


def _assign_contours(operation, item):
    """輪郭系（contour2d / chamfer2d）: ループをチェーン選択し加工サイドを揃える。"""
    parameter = _find_contours_param(operation, CONTOUR_PARAM_CANDIDATES)
    if parameter is None:
        raise RuntimeError('輪郭選択パラメータが見つかりません')
    contours_value = adsk.cam.CadContours2dParameterValue.cast(parameter.value)
    selections = contours_value.getCurveSelections()
    selections.clear()
    # 加工サイドはチェーンの周回方向で決まる（テンプレは左補正）。
    # エッジ列から作るチェーンの向きはボディ依存で不定のため、符号付き面積で
    # 現在の向きを求め、外郭=時計回り（外側）・内郭/穴=反時計回り（内側）に揃える。
    default_ccw = getattr(item, 'side_ccw', item.kind != tr.KIND_GAIKAKU)
    loop_sides = getattr(item, 'loop_sides', None)
    reverted_count = 0
    for index, loop_edges in enumerate(item.loops):
        chain = selections.createNewChainSelection()
        chain.inputGeometry = list(loop_edges)
        desired_ccw = loop_sides[index] if loop_sides else default_ccw
        area = _signed_area_from_first_edge(loop_edges)
        if area is not None:
            if (area > 0) != desired_ccw:
                try:
                    chain.isReverted = True
                    reverted_count += 1
                except Exception:
                    fusion_utils.log('ChainSelection.isReverted を設定できませんでした')
        else:
            fusion_utils.log(f'{item.label}: ループ方向を判定できないチェーンがあります')
    if reverted_count:
        fusion_utils.log(f'{item.label}: {reverted_count} 本のチェーンの向きを反転')
    contours_value.applyCurveSelections(selections)


def _try_set(parameters, name, expression, report):
    try:
        parameter = parameters.itemByName(name)
        if parameter is None:
            raise KeyError(name)
        parameter.expression = expression
        return True
    except Exception:
        if report is not None:
            report.notes.append(f'パラメータ {name} を設定できませんでした（要手動確認）。')
        fusion_utils.log(f'パラメータ設定失敗: {name} = {expression}')
        return False
