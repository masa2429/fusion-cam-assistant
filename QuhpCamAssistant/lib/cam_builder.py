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
# WCS 原点: 手動セットアップの実測値（ストック点モード＋上面の角 'top 1'）
WCS_ORIGIN_MODE_CANDIDATES = ["'stockPoint'"]
WCS_BOX_POINT_CANDIDATES = ["'top 1'"]
# bore（ヘリカル穴あけ）の円筒面選択パラメータ名の候補（⚠️ 実機未確認。
# 見つからない場合は CadObjectParameterValue の候補名をログに出すので、それで確定する）
BORE_PARAM_CANDIDATES = ['circularFaces', 'holeFaces', 'boreFaces']
# 領域加工（ポケット系）の戦略名
POCKET_STRATEGIES = ('pocket2d', 'adaptive2d')
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

    sequence = 0
    created_operations = []
    for item in plan_items:
        if not item.enabled or item.template is None or item.selection_count == 0:
            continue
        sequence += 1
        try:
            operation = _create_operation_from_template(setup, item.template)
            _assign_geometry(operation, item)
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
                             f'quhpcam_{digest}.f3dhsm-template')
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


def _assign_geometry(operation, item):
    strategy = (item.template.strategy or '').lower() if item.template else ''
    if strategy == 'bore':
        _assign_bore_faces(operation, item)
    elif strategy in POCKET_STRATEGIES:
        _assign_pockets(operation, item)
    else:
        _assign_contours(operation, item)


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
    parameter.value.value = list(item.cylinders)


def _assign_pockets(operation, item):
    """ポケット/負荷制御: 底面（faces）または閉ループ（loops=くり抜き）を領域として選択。"""
    parameter = _find_contours_param(operation, POCKET_PARAM_CANDIDATES)
    if parameter is None:
        raise RuntimeError('ポケット選択パラメータが見つかりません')
    contours_value = adsk.cam.CadContours2dParameterValue.cast(parameter.value)
    selections = contours_value.getCurveSelections()
    selections.clear()
    if item.faces:
        pocket_selection = selections.createNewPocketSelection()
        pocket_selection.inputGeometry = list(item.faces)
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
