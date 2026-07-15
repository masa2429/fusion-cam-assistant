# パーツ配置支援：280×280 枠への収まり・部品間 8mm 間隔・高さ揃えをチェックし、
# 必要なら配置ガイドの枠スケッチを作成する。
#
# 間隔チェックはバウンディングボックス間距離（実距離の下限）による目安。
# 斜め隣接では実際より小さく出ることがあるが、安全側の警告になる。

import itertools
import math
import traceback

import adsk.core
import adsk.fusion

from ..lib import classifier, fusion_utils

COMMAND_ID = 'quhpLayoutCheck'
FRAME_SKETCH_NAME = 'QUHP 配置枠 280x280'
_panel = None


def start(panel):
    global _panel
    _panel = panel
    fusion_utils.add_command(
        panel, COMMAND_ID, '配置チェック',
        '280×280 枠への収まり・部品間 8mm 間隔・高さ揃えをチェックします。',
        _on_created)


def stop():
    fusion_utils.remove_command(_panel, COMMAND_ID)


def _bbox_gap_mm(box_a, box_b):
    """XY 平面でのバウンディングボックス間距離（mm）。重なりは 0。"""
    dx = max(box_a.minPoint.x - box_b.maxPoint.x, box_b.minPoint.x - box_a.maxPoint.x, 0.0)
    dy = max(box_a.minPoint.y - box_b.maxPoint.y, box_b.minPoint.y - box_a.maxPoint.y, 0.0)
    return fusion_utils.cm_to_mm(math.hypot(dx, dy))


def _on_created(args):
    ui = fusion_utils.ui()
    try:
        design = fusion_utils.active_design()
        if not design:
            ui.messageBox('デザインのあるドキュメントで実行してください。')
            return
        config = fusion_utils.load_config()
        bodies = classifier.collect_bodies(design)
        if not bodies:
            ui.messageBox('表示中のソリッドボディがありません。')
            return

        width_limit = config.get('stock', {}).get('width_mm', 280)
        depth_limit = config.get('stock', {}).get('depth_mm', 280)
        gap_limit = config.get('part_gap_mm', 8.0)
        lines = []

        # 1. 全体の収まり
        min_x = min(b.boundingBox.minPoint.x for b in bodies)
        max_x = max(b.boundingBox.maxPoint.x for b in bodies)
        min_y = min(b.boundingBox.minPoint.y for b in bodies)
        max_y = max(b.boundingBox.maxPoint.y for b in bodies)
        width = fusion_utils.cm_to_mm(max_x - min_x)
        depth = fusion_utils.cm_to_mm(max_y - min_y)
        fits = width <= width_limit and depth <= depth_limit
        lines.append(f'{"✓" if fits else "✗"} 全体サイズ: {width:.1f} × {depth:.1f} mm'
                     f'（枠 {width_limit:g} × {depth_limit:g} mm）')

        # 2. 部品間の間隔（bbox 距離による目安）
        narrow_pairs = []
        overlap_pairs = []
        for body_a, body_b in itertools.combinations(bodies, 2):
            gap = _bbox_gap_mm(body_a.boundingBox, body_b.boundingBox)
            if gap == 0.0:
                overlap_pairs.append((body_a.name, body_b.name))
            elif gap < gap_limit:
                narrow_pairs.append((body_a.name, body_b.name, gap))
        if overlap_pairs:
            lines.append(f'✗ 重なりの可能性: '
                         + ', '.join(f'{a}↔{b}' for a, b in overlap_pairs))
        if narrow_pairs:
            lines.append(f'✗ 間隔 {gap_limit:g} mm 未満の可能性（bbox距離による目安）:')
            lines += [f'    {a} ↔ {b}: {gap:.1f} mm' for a, b, gap in narrow_pairs]
        if not overlap_pairs and not narrow_pairs:
            lines.append(f'✓ 部品間隔: すべて {gap_limit:g} mm 以上')

        # 3. 高さ揃え
        z_min = min(b.boundingBox.minPoint.z for b in bodies)
        z_max = max(b.boundingBox.maxPoint.z for b in bodies)
        misaligned = [b.name for b in bodies
                      if abs(b.boundingBox.minPoint.z - z_min) > 0.005
                      or abs(b.boundingBox.maxPoint.z - z_max) > 0.005]
        if misaligned:
            lines.append('✗ 高さが揃っていないボディ（「位置合わせ」を使用）: '
                         + ', '.join(misaligned))
        else:
            thickness = fusion_utils.cm_to_mm(z_max - z_min)
            lines.append(f'✓ 高さ揃え: OK（板厚 {thickness:.1f} mm）')

        # 4. 配置枠スケッチ（無ければ作成を提案）
        root = design.rootComponent
        has_frame = any(sketch.name == FRAME_SKETCH_NAME for sketch in root.sketches)
        if not has_frame:
            answer = ui.messageBox(
                '\n'.join(lines) + '\n\n配置ガイドの枠スケッチ（280×280、原点基準）を作成しますか？',
                'QUHP 配置チェック',
                adsk.core.MessageBoxButtonTypes.YesNoButtonType)
            if answer == adsk.core.DialogResults.DialogYes:
                sketch = root.sketches.add(root.xYConstructionPlane)
                sketch.name = FRAME_SKETCH_NAME
                corner_1 = adsk.core.Point3D.create(0, 0, 0)
                corner_2 = adsk.core.Point3D.create(
                    fusion_utils.mm_to_cm(width_limit), fusion_utils.mm_to_cm(depth_limit), 0)
                sketch.sketchCurves.sketchLines.addTwoPointRectangle(corner_1, corner_2)
        else:
            ui.messageBox('\n'.join(lines), 'QUHP 配置チェック')
    except Exception:
        ui.messageBox('配置チェックに失敗:\n{}'.format(traceback.format_exc()))
