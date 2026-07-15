# セットアップ生成 → テンプレートから操作生成 → ジオメトリ割当 → ツールパス生成
#
# 使用 API は docs/api-notes.md 準拠（createFromCAMTemplate2 / CurveSelections）。
# ⚠️ 一部のパラメータ名（ストック寸法・WCS原点の boxPoint 等）は実機未確認のため、
#    候補名リストを順に試し、失敗したものはログに残す方針にしている。
#    tools/dump_parameters.py の結果で確定したら、この先頭の定数を修正すること。

import traceback

import adsk.cam
import adsk.core

from . import fusion_utils
from . import template_registry as tr

# --- 実機確認対象のパラメータ名（api-notes.md「実機確認 TODO」） ---------------
# contour2d 系操作のジオメトリパラメータ名の候補（先頭から順に試す）
CONTOUR_PARAM_CANDIDATES = ['contours']
# pocket2d 系操作のジオメトリパラメータ名の候補
POCKET_PARAM_CANDIDATES = ['pockets']
# WCS 原点をストックボックスの点にするモード値と点名の候補
WCS_ORIGIN_MODE_CANDIDATES = ["'stockBoxPoint'", "'stockPoint'"]
WCS_BOX_POINT_CANDIDATES = ["'bottom lower left'", "'lower left'", "'bottom 1'"]
# -----------------------------------------------------------------------------


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


def _create_setup(cam, classify_result, config, report):
    setup_input = cam.setups.createInput(adsk.cam.OperationTypes.MillingOperation)
    setup_input.models = list(classify_result.bodies)
    setup = cam.setups.add(setup_input)
    setup.name = 'QUHP 自動セットアップ'

    # ストック: モデル相対・オフセット0（=パーツ群の外接箱、高さ=板厚）を既定にする。
    # 固定 280×280 ボックスのパラメータ名は実機未確認のため v1 では相対を採用
    # （切り上げ0・板厚一致という手順書の要件は相対＋オフセット0で満たされる）。
    try:
        setup.stockMode = adsk.cam.SetupStockModes.RelativeBoxStock
        _try_set(setup.parameters, 'job_stockOffsetMode', "'simple'", report)
        _try_set(setup.parameters, 'job_stockOffsetSides', '0 mm', report)
        _try_set(setup.parameters, 'job_stockOffsetTop', '0 mm', report)
    except Exception:
        report.notes.append('ストック設定に失敗。手動で確認してください。')

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
            '（dump_parameters.py の結果で cam_builder.py の候補名を更新すると自動化されます）。')
    return setup


def _create_operation_from_template(setup, template):
    cam_template = adsk.cam.CAMTemplate.createFromFile(template.path)
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
    for i in range(operation.parameters.count):
        parameter = operation.parameters.item(i)
        try:
            if adsk.cam.CadContours2dParameterValue.cast(parameter.value):
                fusion_utils.log(
                    f'ジオメトリパラメータを型スキャンで発見: {parameter.name!r}'
                    '（cam_builder.py の候補名に追加してください）')
                return parameter
        except Exception:
            continue
    return None


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
        for loop_edges in item.loops:
            chain = selections.createNewChainSelection()
            chain.inputGeometry = list(loop_edges)
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
