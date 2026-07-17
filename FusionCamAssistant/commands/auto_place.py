# 部品の自動配置：Fusion 標準の整列（Arrange）フィーチャを API で作成し、
# 280×280 の板（原点基準・第一象限）にトップレベルコンポーネントを詰め直す。
#
# API は docs/api-notes.md「7. 整列（Arrange）API」準拠（2025年1月版で導入。
# 旧バージョンでは案内して終了する）。
# - envelope は XY 平面の平面エンベロープ（枠サイズ＝config の stock、原点オフセット 0）
# - 部品間隔 = part_gap_mm、枠からの余白 = placement_edge_margin_mm
# - ソルバーは config の arrange_solver:
#     'auto'（既定。トゥルーシェイプ → 失敗したら矩形にフォールバック）
#     'trueshape' / 'rectangular'（固定）
# - 再実行時は前回の自動配置フィーチャ（属性で識別）を削除してから作り直す（増殖しない）
# - 既存の切削データ（WCS 原点・進入点・境界拡張スケッチ）は配置に追従しないため、
#   配置後に「切削データ自動作成」の再実行を促す

import json
import traceback

import adsk
import adsk.core
import adsk.fusion

from ..lib import fusion_utils
from . import layout_check

COMMAND_ID = 'fcaAutoPlace'
DESIGN_WORKSPACE_ID = 'FusionSolidEnvironment'
CAM_WORKSPACE_ID = 'CAMEnvironment'
FEATURE_NAME = '自動配置'
ATTR_GROUP = 'FusionCamAssistant'
ATTR_NAME = 'autoPlace'
_panel = None

_SOLVER_LABELS = {
    'trueshape': 'トゥルーシェイプ（実形状）',
    'rectangular': '矩形（バウンディングボックス）',
}
_SOLVER_OPTIONS = (
    ('auto', '自動（トゥルーシェイプ → 矩形）'),
    ('trueshape', 'トゥルーシェイプ（実形状）'),
    ('rectangular', '矩形（バウンディングボックス）'),
)
_ROTATION_OPTIONS = (
    ('all', '任意'),
    ('quarter', '90°単位'),
    ('half', '180°のみ'),
    ('none', '回転なし'),
)


def _rotation_type(key):
    types = adsk.fusion.ArrangeRotationTypes
    return {
        'all': types.AllRotationsArrangeRotationType,
        'quarter': types.Only90And270ArrangeRotationType,
        'half': types.Only180ArrangeRotationType,
        'none': types.NoneArrangeRotationType,
    }.get(key, types.AllRotationsArrangeRotationType)


def start(panel):
    global _panel
    _panel = panel
    fusion_utils.add_command(
        panel, COMMAND_ID, '自動配置',
        '標準の整列フィーチャを使って、部品を 280×280 の板（原点基準）に自動で詰め直します。',
        _on_created)


def stop():
    fusion_utils.remove_command(_panel, COMMAND_ID)


def _has_solid_body(occurrence):
    try:
        for body in occurrence.bRepBodies:
            if body.isSolid and body.isVisible:
                return True
        for i in range(occurrence.childOccurrences.count):
            child = occurrence.childOccurrences.item(i)
            if child.isLightBulbOn and _has_solid_body(child):
                return True
    except Exception:
        pass
    return False


def _unit_bodies(occurrence):
    """配置単位（トップレベルオカレンス）配下の可視ソリッドボディ（ワールド座標のプロキシ）。"""
    bodies = []
    try:
        for body in occurrence.bRepBodies:
            if body.isSolid and body.isVisible:
                bodies.append(body)
        for i in range(occurrence.childOccurrences.count):
            child = occurrence.childOccurrences.item(i)
            if child.isLightBulbOn:
                bodies.extend(_unit_bodies(child))
    except Exception:
        pass
    return bodies


def _is_flipped(occurrence):
    """オカレンスの変換行列の Z 軸成分から上下反転を判定する（Z軸回転のみなら zz≈+1）。
    判定できない場合は None。"""
    try:
        transform = getattr(occurrence, 'transform2', None) or occurrence.transform
        return transform.getCell(2, 2) < 0.0
    except Exception:
        return None


def _collect_targets(root):
    targets = []
    for i in range(root.occurrences.count):
        occurrence = root.occurrences.item(i)
        if occurrence.isLightBulbOn and _has_solid_body(occurrence):
            targets.append(occurrence)
    return targets


def _delete_previous_features(arrange_features):
    """前回の自動配置フィーチャを削除する（属性で識別。保険で名前も見る）。"""
    removed = 0
    for i in reversed(range(arrange_features.count)):
        feature = arrange_features.item(i)
        is_ours = False
        try:
            is_ours = feature.attributes.itemByName(ATTR_GROUP, ATTR_NAME) is not None
        except Exception:
            pass
        if not is_ours:
            try:
                is_ours = feature.name.startswith(FEATURE_NAME)
            except Exception:
                pass
        if is_ours:
            try:
                feature.deleteMe()
                removed += 1
            except Exception:
                fusion_utils.log('旧自動配置フィーチャの削除に失敗:\n' + traceback.format_exc())
    return removed


def _mm(value_mm):
    """単位を明示した ValueInput（フィーチャのパラメータ式にも mm で残る）。"""
    return adsk.core.ValueInput.createByString(f'{value_mm:g} mm')


def _create_arrange(root, arrange_features, occurrences, width_mm, depth_mm,
                    gap_mm, edge_mm, solver_mode, rotation_key, notes):
    """整列フィーチャを作成する。auto はトゥルーシェイプ→矩形の順に試す。"""
    if solver_mode == 'trueshape':
        order = [('trueshape', adsk.fusion.ArrangeSolverTypes.Arrange2DTrueShapeSolverType)]
    elif solver_mode == 'rectangular':
        order = [('rectangular', adsk.fusion.ArrangeSolverTypes.Arrange2DRectangularSolverType)]
    else:
        order = [('trueshape', adsk.fusion.ArrangeSolverTypes.Arrange2DTrueShapeSolverType),
                 ('rectangular', adsk.fusion.ArrangeSolverTypes.Arrange2DRectangularSolverType)]

    for solver_name, solver_type in order:
        try:
            arrange_input = arrange_features.createInput(solver_type)
            envelope = arrange_input.setPlaneEnvelope(
                root.xYConstructionPlane, _mm(width_mm), _mm(depth_mm))
            envelope.frameWidth = _mm(edge_mm)
            envelope.objectSpacing = _mm(gap_mm)
            for prop in ('originXOffset', 'originYOffset'):
                try:
                    setattr(envelope, prop, _mm(0))
                except Exception:
                    fusion_utils.log(f'envelope.{prop} を設定できませんでした')
            try:
                envelope.isPartialArrangeAllowed = True  # 入り切らなくても失敗にせず未配置として報告
            except Exception:
                fusion_utils.log('isPartialArrangeAllowed を設定できませんでした')
            try:
                envelope.quantity = 1  # 板は1枚（既定 -1 だとエンベロープが複数作られる）
            except Exception:
                try:
                    envelope.quantity = adsk.core.ValueInput.createByReal(1)
                except Exception:
                    fusion_utils.log('envelope.quantity を設定できませんでした（板が複数枚になる可能性）')

            definition = arrange_input.definition
            try:
                definition.isCreateCopies = False  # 原本を移動する（コピーを作らない）
            except Exception:
                fusion_utils.log('isCreateCopies を設定できませんでした')
            try:
                definition.globalRotation = _rotation_type(rotation_key)
            except Exception:
                fusion_utils.log('globalRotation を設定できませんでした')
            # 上下反転防止: オカレンス渡しの既定の向きは平板を裏返す（実機確認済み）。
            # BRepFace 渡しは InternalValidationError で使えなかったため、
            # isDirectionFlipped で既定の向きから反転させて打ち消す。
            # 実際に正しい向きになったかは配置後に変換行列で検証・表示する。
            for occurrence in occurrences:
                component = arrange_input.arrangeComponents.add(occurrence)
                try:
                    component.isDirectionFlipped = True
                except Exception:
                    fusion_utils.log(f'isDirectionFlipped を設定できませんでした: {occurrence.name}')

            feature = arrange_features.add(arrange_input)
            return feature, solver_name
        except Exception:
            fusion_utils.log(f'整列フィーチャの作成に失敗（{solver_name}）:\n'
                             + traceback.format_exc())
            if solver_mode == 'auto' and solver_name == 'trueshape':
                notes.append('トゥルーシェイプでの作成に失敗したため矩形にフォールバックしました'
                             '（Nesting 拡張が無い環境では正常）。')
    return None, None


def _statistics_lines(feature):
    """arrangeStatistics（JSON）から配置結果の要約行を作る。"""
    lines = []
    unarranged = None
    try:
        stats = json.loads(feature.arrangeStatistics)
        top = stats.get('statistics', {})

        def value(key):
            entry = top.get(key) or {}
            return entry.get('value')

        arranged = value('Components Arranged')
        unarranged = value('Components Unarranged')
        if arranged is not None:
            lines.append(f'✓ 配置した部品: {arranged:g} 個')
        if unarranged:
            lines.append(f'✗ 入り切らなかった部品: {unarranged:g} 個（元の位置に残っています）')
    except Exception:
        fusion_utils.log('arrangeStatistics の解析に失敗:\n' + traceback.format_exc())
    if unarranged:
        # 未配置の名前が取れる場合は列挙する（UI 作成のフィーチャで RuntimeError の実績あり）
        try:
            names = []
            for comp in feature.unusedComponents:
                try:
                    names.append(comp.occurrence.name)
                except Exception:
                    pass
            if names:
                lines.append('    未配置: ' + ', '.join(names))
        except Exception:
            pass
    return lines


def _flip_check_lines(occurrences):
    """配置後の上下反転チェック。反転部品があれば鏡像加工事故になるため明示する。"""
    flipped = []
    unknown = []
    for occurrence in occurrences:
        result = _is_flipped(occurrence)
        if result is True:
            flipped.append(occurrence.name)
        elif result is None:
            unknown.append(occurrence.name)
    lines = []
    if flipped:
        lines.append('✗ 上下が反転している部品（このまま削ると鏡像になります！）: '
                     + ', '.join(flipped))
        lines.append('    ※Ctrl+Z で戻し、開発者に報告してください')
    elif not unknown:
        lines.append('✓ 上下の向き: 全部品正常')
    if unknown:
        lines.append('⚠ 上下の向きを判定できなかった部品（目視確認）: ' + ', '.join(unknown))
    return lines


def _frame_check_lines(occurrences, width_mm, depth_mm, edge_mm):
    """配置後、各単位の bbox が枠の余白内に収まっているか検証する。
    平面エンベロープの原点位置は実機仕様が未確定のため、この検証を必ず行う。"""
    tolerance = 0.1  # mm
    outside = []
    for occurrence in occurrences:
        boxes = [b.boundingBox for b in _unit_bodies(occurrence)]
        if not boxes:
            continue
        min_x = fusion_utils.cm_to_mm(min(box.minPoint.x for box in boxes))
        min_y = fusion_utils.cm_to_mm(min(box.minPoint.y for box in boxes))
        max_x = fusion_utils.cm_to_mm(max(box.maxPoint.x for box in boxes))
        max_y = fusion_utils.cm_to_mm(max(box.maxPoint.y for box in boxes))
        if (min_x < edge_mm - tolerance or min_y < edge_mm - tolerance
                or max_x > width_mm - edge_mm + tolerance
                or max_y > depth_mm - edge_mm + tolerance):
            outside.append(occurrence.name)
    if outside:
        return [f'✗ 枠（余白 {edge_mm:g} mm）の外にある部品: ' + ', '.join(outside),
                '    ※入り切らなかった部品か、エンベロープが原点基準になっていない可能性。'
                '配置チェックと目視で確認してください']
    return [f'✓ 全部品が枠内（余白 {edge_mm:g} mm）に収まっています']


def _on_created(args):
    """配置ダイアログ（枠サイズ・間隔・余白・ソルバー・回転を編集して「配置」）。"""
    ui = fusion_utils.ui()
    try:
        command = args.command
        design = fusion_utils.active_design()
        if not design:
            ui.messageBox('デザインのあるドキュメントで実行してください。')
            return
        root = design.rootComponent
        arrange_features = getattr(root.features, 'arrangeFeatures', None)
        if arrange_features is None or not hasattr(arrange_features, 'createInput'):
            ui.messageBox('この Fusion バージョンは整列 API に対応していません'
                          '（2025年1月以降のバージョンが必要）。\n'
                          'Fusion を更新するか、「修正 → 整列」を手動で使ってください。',
                          '自動配置')
            return
        occurrences = _collect_targets(root)
        if not occurrences:
            ui.messageBox('配置対象のコンポーネントがありません'
                          '（トップレベルの表示中コンポーネントが対象です）。', '自動配置')
            return

        config = fusion_utils.load_config()
        warnings = []
        root_bodies = [b for b in root.bRepBodies if b.isSolid and b.isVisible]
        if root_bodies:
            warnings.append(f'ルート直下のボディ {len(root_bodies)} 個は移動できません'
                            '（右クリック →「ボディからコンポーネントを作成」で対象になります）')
        cam = fusion_utils.active_cam()
        if cam is not None and cam.setups.count > 0:
            warnings.append('既存の切削データは配置に追従しません。'
                            '配置後に「切削データ自動作成」を再実行してください')

        inputs = command.commandInputs
        header = (f'対象: トップレベルコンポーネント {len(occurrences)} 個\n'
                  '（元に戻す場合は Ctrl+Z）')
        if warnings:
            header += '\n⚠ ' + '\n⚠ '.join(warnings)
        header_input = inputs.addTextBoxCommandInput(
            'fcaApHeader', '', header, 3 + 2 * len(warnings), True)
        header_input.isFullWidth = True

        def add_value(input_id, label, value_mm):
            inputs.addValueInput(
                input_id, label, 'mm',
                adsk.core.ValueInput.createByReal(fusion_utils.mm_to_cm(value_mm)))

        add_value('fcaApWidth', '枠の幅（X）', config.get('stock', {}).get('width_mm', 280))
        add_value('fcaApDepth', '枠の奥行（Y）', config.get('stock', {}).get('depth_mm', 280))
        add_value('fcaApGap', '部品間隔', config.get('part_gap_mm', 8.0))
        add_value('fcaApEdge', '枠からの余白', config.get('placement_edge_margin_mm', 8.0))

        solver_default = str(config.get('arrange_solver', 'auto')).lower()
        solver_input = inputs.addDropDownCommandInput(
            'fcaApSolver', 'ソルバー', adsk.core.DropDownStyles.TextListDropDownStyle)
        for key, label in _SOLVER_OPTIONS:
            solver_input.listItems.add(label, key == solver_default)
        rotation_input = inputs.addDropDownCommandInput(
            'fcaApRotation', '回転', adsk.core.DropDownStyles.TextListDropDownStyle)
        for key, label in _ROTATION_OPTIONS:
            rotation_input.listItems.add(label, key == 'all')
        inputs.addBoolValueInput('fcaApGotoCam', '完了後に製造ワークスペースへ切替',
                                 True, '', True)

        command.okButtonText = '配置'
        command.execute.add(fusion_utils.keep(_ExecuteHandler()))
    except Exception:
        ui.messageBox('自動配置に失敗:\n{}'.format(traceback.format_exc()))


def _selected_key(inputs, input_id, options, default_key):
    dropdown = inputs.itemById(input_id)
    if dropdown is not None and dropdown.selectedItem is not None:
        for key, label in options:
            if label == dropdown.selectedItem.name:
                return key
    return default_key


def _value_mm(inputs, input_id, default_mm):
    item = inputs.itemById(input_id)
    if item is None:
        return default_mm
    return fusion_utils.cm_to_mm(item.value)  # ValueInput の内部値は cm


class _ExecuteHandler(adsk.core.CommandEventHandler):
    def notify(self, args):
        ui = fusion_utils.ui()
        try:
            inputs = args.command.commandInputs
            width_mm = _value_mm(inputs, 'fcaApWidth', 280)
            depth_mm = _value_mm(inputs, 'fcaApDepth', 280)
            gap_mm = _value_mm(inputs, 'fcaApGap', 8.0)
            edge_mm = _value_mm(inputs, 'fcaApEdge', 8.0)
            solver_mode = _selected_key(inputs, 'fcaApSolver', _SOLVER_OPTIONS, 'auto')
            rotation_key = _selected_key(inputs, 'fcaApRotation', _ROTATION_OPTIONS, 'all')
            goto_input = inputs.itemById('fcaApGotoCam')
            goto_cam = bool(goto_input.value) if goto_input is not None else False
            _run_placement(ui, width_mm, depth_mm, gap_mm, edge_mm,
                           solver_mode, rotation_key, goto_cam)
        except Exception:
            ui.messageBox('自動配置に失敗:\n{}'.format(traceback.format_exc()))


def _run_placement(ui, width_mm, depth_mm, gap_mm, edge_mm,
                   solver_mode, rotation_key, goto_cam):
    design = fusion_utils.active_design()
    if design is None:
        return
    root = design.rootComponent
    arrange_features = getattr(root.features, 'arrangeFeatures', None)
    if arrange_features is None:
        return
    occurrences = _collect_targets(root)
    if not occurrences:
        ui.messageBox('配置対象のコンポーネントがありません。', '自動配置')
        return

    # 整列は設計フィーチャのため、製造ワークスペースがアクティブなままだと
    # arrangeComponents.add が「No fusion asset adapter」で失敗する。
    # 作成の間だけデザインワークスペースへ切り替える。
    notes = []
    previous_workspace = None
    try:
        active_workspace = ui.activeWorkspace
        if active_workspace and active_workspace.id != DESIGN_WORKSPACE_ID:
            design_workspace = ui.workspaces.itemById(DESIGN_WORKSPACE_ID)
            if design_workspace:
                previous_workspace = active_workspace
                design_workspace.activate()
                adsk.doEvents()
    except Exception:
        fusion_utils.log('デザインワークスペースへの切替に失敗:\n' + traceback.format_exc())

    success = False
    lines = []
    solver_used = None
    try:
        removed = _delete_previous_features(arrange_features)
        if removed:
            notes.append(f'前回の自動配置フィーチャ {removed} 件を置き換えました。')

        feature, solver_used = _create_arrange(
            root, arrange_features, occurrences, width_mm, depth_mm,
            gap_mm, edge_mm, solver_mode, rotation_key, notes)
        if feature is None:
            ui.messageBox('整列フィーチャを作成できませんでした。\n'
                          '詳細はテキストコマンドウィンドウ / %TEMP%\\fusioncam.log を確認してください。',
                          '自動配置')
            return
        success = True

        try:
            feature.name = FEATURE_NAME
        except Exception:
            pass
        try:
            feature.attributes.add(ATTR_GROUP, ATTR_NAME, '1')
        except Exception:
            fusion_utils.log('自動配置フィーチャへの属性付与に失敗:\n' + traceback.format_exc())

        lines = [f'ソルバー: {_SOLVER_LABELS.get(solver_used, solver_used)}']
        lines += _statistics_lines(feature)
        lines += _flip_check_lines(occurrences)
        lines += _frame_check_lines(occurrences, width_mm, depth_mm, edge_mm)
        lines += notes
    finally:
        # 成功して製造へ移る場合は復帰しない（このあと製造をアクティブにする）
        if previous_workspace is not None and not (goto_cam and success):
            try:
                previous_workspace.activate()
                adsk.doEvents()
            except Exception:
                fusion_utils.log('ワークスペースの復帰に失敗:\n' + traceback.format_exc())

    if solver_used == 'trueshape':
        lines.append('ℹ トゥルーシェイプ配置では「配置チェック」の間隔警告（bbox 目安）が'
                     '出ることがあります（実形状で 8mm 確保済みなら問題なし）')
    lines.append('')
    lines.append('このあと「切削データ自動作成」を実行してください。')

    if not layout_check.has_frame_sketch(root):
        answer = ui.messageBox(
            '\n'.join(lines) + '\n\n配置ガイドの枠スケッチ'
            f'（{width_mm:g}×{depth_mm:g}、原点基準）を作成しますか？',
            '自動配置', adsk.core.MessageBoxButtonTypes.YesNoButtonType)
        if answer == adsk.core.DialogResults.DialogYes:
            layout_check.create_frame_sketch(root, width_mm, depth_mm)
    else:
        ui.messageBox('\n'.join(lines), '自動配置')

    if goto_cam and success:
        try:
            cam_workspace = ui.workspaces.itemById(CAM_WORKSPACE_ID)
            if cam_workspace:
                cam_workspace.activate()
                adsk.doEvents()
        except Exception:
            fusion_utils.log('製造ワークスペースへの切替に失敗:\n' + traceback.format_exc())
