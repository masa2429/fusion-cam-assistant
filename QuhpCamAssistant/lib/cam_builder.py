# セットアップ生成 → テンプレートから操作生成 → ジオメトリ割当 → ツールパス生成
#
# 使用 API は docs/api-notes.md 準拠（createFromCAMTemplate2 / CurveSelections）。
# ⚠️ 一部のパラメータ名（ストック寸法・WCS原点の boxPoint 等）は実機未確認のため、
#    候補名リストを順に試し、失敗したものはログに残す方針にしている。
#    tools/DumpParameters の結果で確定したら、この先頭の定数を修正すること。

import hashlib
import os
import shutil
import tempfile
import traceback

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
# WCS 原点: 手動セットアップの実測値（ストック点モード＋上面の角 'top 1'）
WCS_ORIGIN_MODE_CANDIDATES = ["'stockPoint'"]
WCS_BOX_POINT_CANDIDATES = ["'top 1'"]
# -------------------------------------------------------------------------------


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

    sequence = 0
    for item in plan_items:
        if not item.enabled or item.template is None or item.selection_count == 0:
            continue
        sequence += 1
        try:
            operation = _create_operation_from_template(setup, item.template)
            _assign_geometry(operation, item)
            operation.name = f'{sequence:02d}_{item.template.name}'
            report.created.append((operation.name, item.selection_count))
        except Exception:
            report.failed.append((item.label, traceback.format_exc(limit=3)))
            fusion_utils.log(f'操作生成失敗 {item.label}:\n{traceback.format_exc()}')

    if report.created:
        try:
            cam.generateAllToolpaths(True)
            report.notes.append('ツールパスを生成中です（完了までしばらくかかります）。')
        except Exception:
            report.notes.append('ツールパスの自動生成に失敗。手動で「生成」してください。')
    return report


SETUP_NAME = 'QUHP 自動セットアップ'


def _create_setup(cam, classify_result, config, report):
    # 再実行で自動セットアップが増殖しないよう、既存の同名セットアップを置き換える
    removed = 0
    for i in reversed(range(cam.setups.count)):
        existing = cam.setups.item(i)
        try:
            if existing.name.startswith(SETUP_NAME):
                existing.deleteMe()
                removed += 1
        except Exception:
            pass
    if removed:
        report.notes.append(f'既存の「{SETUP_NAME}」{removed} 件を置き換えました。')

    setup_input = cam.setups.createInput(adsk.cam.OperationTypes.MillingOperation)
    setup_input.models = list(classify_result.bodies)
    setup = cam.setups.add(setup_input)
    setup.name = SETUP_NAME

    # ストック: 固定ボックス（実機ダンプで確認した手動運用の再現）。
    # X/Y は既定式のまま＝部品範囲を10mm単位に切り上げて中央配置（job_stockFixedX/Y の既定式）。
    # Z のみ板厚ちょうどに固定する（既定式だと10mmに切り上がってしまうため）。
    try:
        setup.stockMode = adsk.cam.SetupStockModes.FixedBoxStock
    except Exception:
        pass
    _try_set(setup.parameters, 'job_stockMode', "'fixedbox'", report)
    if classify_result.thickness_mm > 0:
        _try_set(setup.parameters, 'job_stockFixedZ',
                 f'{classify_result.thickness_mm:g} mm', report)

    # WCS 原点: ストックボックス左下（候補名を順に試す）
    origin_set = False
    for mode in WCS_ORIGIN_MODE_CANDIDATES:
        if _try_set(setup.parameters, 'wcs_origin_mode', mode, None):
            for box_point in WCS_BOX_POINT_CANDIDATES:
                if _try_set(setup.parameters, 'wcs_origin_boxPoint', box_point, None):
                    origin_set = True
                    break
        if origin_set:
            break
    if not origin_set:
        report.notes.append(
            '原点をストック左下に設定できませんでした。セットアップ編集で手動設定してください'
            '（DumpParameters の結果で cam_builder.py の候補名を更新すると自動化されます）。')
    return setup


def _load_cam_template(template):
    """CAMTemplate を読み込む。
    createFromFile は内部で ANSI 変換するため日本語・Φ入りパスで失敗する
    （実機確認済み: 'No mapping for the Unicode character...'）。
    ASCII 名の一時ファイルへコピーしてから読み込む。"""
    digest = hashlib.md5(template.path.encode('utf-8')).hexdigest()[:12]
    temp_path = os.path.join(tempfile.gettempdir(),
                             f'quhpcam_{digest}.f3dhsm-template')
    if not os.path.isfile(temp_path):
        shutil.copyfile(template.path, temp_path)
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


def _assign_geometry(operation, item):
    if item.faces:
        parameter = _find_contours_param(operation, POCKET_PARAM_CANDIDATES)
        if parameter is None:
            raise RuntimeError('ポケット選択パラメータが見つかりません')
        contours_value = adsk.cam.CadContours2dParameterValue.cast(parameter.value)
        selections = contours_value.getCurveSelections()
        selections.clear()
        pocket_selection = selections.createNewPocketSelection()
        pocket_selection.inputGeometry = list(item.faces)
        contours_value.applyCurveSelections(selections)
    if item.loops:
        parameter = _find_contours_param(operation, CONTOUR_PARAM_CANDIDATES)
        if parameter is None:
            raise RuntimeError('輪郭選択パラメータが見つかりません')
        contours_value = adsk.cam.CadContours2dParameterValue.cast(parameter.value)
        selections = contours_value.getCurveSelections()
        selections.clear()
        # 加工サイドはチェーンの周回方向で決まる（テンプレは左補正）。
        # エッジ列から作るチェーンの向きはボディ依存で不定のため、符号付き面積で
        # 現在の向きを求め、外郭=時計回り（外側）・内郭/穴=反時計回り（内側）に揃える。
        desired_ccw = (item.kind != tr.KIND_GAIKAKU)
        reverted_count = 0
        for loop_edges in item.loops:
            chain = selections.createNewChainSelection()
            chain.inputGeometry = list(loop_edges)
            area = _signed_area_from_first_edge(loop_edges)
            if area is not None:
                current_ccw = area > 0
                if current_ccw != desired_ccw:
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
