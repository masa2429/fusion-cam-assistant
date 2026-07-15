# ボディ（板パーツ、2.5D前提）を解析し、加工候補（PlanItem）の一覧を作る。
#
# 分類ルール（docs/切削データquhp.md と CLAUDE.md 準拠）:
#   - 底面（最下Z・下向き法線の平面）の外側ループ         → 外郭
#   - 底面の内側ループ（円形・穴径ルールに一致）           → 穴あけ
#   - 底面の内側ループ（円形・ルール外・小径）             → 未割り当て（ボール盤/スポットドリル用）
#   - 底面の内側ループ（非円形、または大径円形）           → 内郭
#   - 中間高さ・上向き法線の平面（円形境界）               → ざぐり
#   - 中間高さ・上向き法線の平面（非円形境界）             → 内郭ポケット
#   - Φ3内郭で削り残る隅（尖り・小Rフィレット）がある場合 → 取り残し加工を提案
#
# Fusion API の内部単位は cm。このモジュールの「_mm」付き変数以外は cm。

import math

import adsk.core
import adsk.fusion

from . import template_registry as tr

_Z_TOL = 0.005          # cm（0.05mm）: 同一高さとみなす許容
_NORMAL_TOL = 0.9       # 法線Z成分がこれ以上なら水平面とみなす
_TANGENT_TOL_RAD = 0.05  # 約3°: これ以上向きが変わる頂点は「尖った隅」


class PlanItem:
    """確認ダイアログの1行 ＝ 生成する1オペレーション。"""

    def __init__(self, kind, label, template, note=''):
        self.kind = kind
        self.label = label          # ダイアログに出す説明（例: '穴あけ Φ2.0 ×12'）
        self.template = template    # Template（None なら未割り当て）
        self.note = note
        self.enabled = template is not None
        self.loops = []             # contour系: ループごとの BRepEdge リスト
        self.faces = []             # pocket系: 底面 BRepFace リスト

    @property
    def selection_count(self):
        return len(self.loops) + len(self.faces)


class ClassifyResult:
    def __init__(self):
        self.items = []           # list[PlanItem]（加工順ソート済み）
        self.bodies = []          # 対象ボディ
        self.thickness_mm = 0.0   # 板厚（最大 z 範囲）
        self.z_aligned = True     # 全ボディの上下面が揃っているか
        self.warnings = []        # ユーザーに見せる注意


def collect_bodies(design):
    """表示中のソリッドボディを（アセンブリ文脈のプロキシとして）集める。"""
    bodies = []
    root = design.rootComponent
    for body in root.bRepBodies:
        if body.isSolid and body.isVisible:
            bodies.append(body)
    for occurrence in root.allOccurrences:
        if not occurrence.isLightBulbOn:
            continue
        for body in occurrence.bRepBodies:
            if body.isSolid and body.isVisible:
                bodies.append(body)
    return bodies


def _outward_normal(face):
    ok, normal = face.evaluator.getNormalAtPoint(face.pointOnFace)
    return normal if ok else None


def _horizontal_planar_faces(body):
    """(face, z, is_up) のリスト。水平な平面のみ。"""
    result = []
    for face in body.faces:
        if face.geometry.surfaceType != adsk.core.SurfaceTypes.PlaneSurfaceType:
            continue
        normal = _outward_normal(face)
        if normal is None or abs(normal.z) < _NORMAL_TOL:
            continue
        z = face.boundingBox.minPoint.z
        result.append((face, z, normal.z > 0))
    return result


def _is_full_circle_loop(loop):
    if loop.edges.count != 1:
        return None
    geometry = loop.edges.item(0).geometry
    if geometry.curveType == adsk.core.Curve3DTypes.Circle3DCurveType:
        return geometry  # Circle3D
    return None


def _loop_perimeter_cm(loop):
    return sum(edge.length for edge in loop.edges)


def _loop_polygon_area_cm2(loop):
    """ループを折れ線近似して XY 平面上の面積を求める（cm²）。失敗時 None。"""
    try:
        points = []
        for i in range(loop.coEdges.count):
            coedge = loop.coEdges.item(i)
            evaluator = coedge.edge.evaluator
            ok, t_start, t_end = evaluator.getParameterExtents()
            ok2, stroke = evaluator.getStrokes(t_start, t_end, 0.01)
            if not (ok and ok2) or not stroke:
                return None
            sequence = list(stroke)
            if coedge.isOpposedToEdge:
                sequence.reverse()
            points.extend(sequence[:-1] if len(sequence) > 1 else sequence)
        if len(points) < 3:
            return None
        area = 0.0
        for i in range(len(points)):
            p1 = points[i]
            p2 = points[(i + 1) % len(points)]
            area += p1.x * p2.y - p2.x * p1.y
        return abs(area) / 2.0
    except Exception:
        return None


def _opening_estimate_mm(loop):
    """工具が入る開口幅の見積もり（mm）。
    細長い溝は外接矩形では判定できないため、幅 ≈ 2×面積/周長（細溝で溝幅に一致、
    コンパクトな穴では実際より小さめ＝安全側）と外接矩形短辺の小さい方を使う。"""
    bbox_min = _loop_bbox_min_dimension_mm(loop)
    perimeter = _loop_perimeter_cm(loop)
    area = _loop_polygon_area_cm2(loop)
    if area is not None and perimeter and perimeter > 1e-6:
        hydraulic = 2.0 * area / perimeter * 10.0
        if bbox_min is not None:
            return min(bbox_min, hydraulic)
        return hydraulic
    return bbox_min


def _loop_bbox_min_dimension_mm(loop):
    bbox = None
    for edge in loop.edges:
        edge_box = edge.boundingBox
        if bbox is None:
            bbox = adsk.core.BoundingBox3D.create(edge_box.minPoint, edge_box.maxPoint)
        else:
            bbox.combine(edge_box)
    if bbox is None:
        return None
    width = (bbox.maxPoint.x - bbox.minPoint.x) * 10.0
    depth = (bbox.maxPoint.y - bbox.minPoint.y) * 10.0
    return min(width, depth)


def _min_feature_radius_mm(loop):
    """ループ中の最小の凹み特徴半径（mm）。尖った隅（非接線頂点）は 0 とみなす。
    工具半径より小さい特徴があると削り残しが出る。判定失敗時は None。"""
    try:
        min_radius = math.inf
        tangents = []
        coedges = loop.coEdges
        n = coedges.count
        for i in range(n):
            coedge = coedges.item(i)
            edge = coedge.edge
            geometry = edge.geometry
            if geometry.curveType == adsk.core.Curve3DTypes.Circle3DCurveType or \
                    geometry.curveType == adsk.core.Curve3DTypes.Arc3DCurveType:
                min_radius = min(min_radius, geometry.radius * 10.0)
            evaluator = edge.evaluator
            ok, t_start, t_end = evaluator.getParameterExtents()
            ok1, tan_start = evaluator.getTangent(t_start)
            ok2, tan_end = evaluator.getTangent(t_end)
            if not (ok and ok1 and ok2):
                return None
            if coedge.isOpposedToEdge:
                tan_start, tan_end = tan_end.copy(), tan_start.copy()
                tan_start.scaleBy(-1.0)
                tan_end.scaleBy(-1.0)
            tangents.append((tan_start, tan_end))
        for i in range(n):
            outgoing = tangents[i][1]
            incoming = tangents[(i + 1) % n][0]
            if outgoing.angleTo(incoming) > _TANGENT_TOL_RAD:
                min_radius = 0.0
        return min_radius
    except Exception:
        return None


def classify(design, registry, config):
    result = ClassifyResult()
    result.bodies = collect_bodies(design)
    if not result.bodies:
        result.warnings.append('表示中のソリッドボディがありません。')
        return result

    hole_tolerance = config.get('diameter_tolerance_mm', 0.05)
    tool_margin = config.get('tool_margin_mm', 0.5)
    preferred = config.get('preferred_tools', {})
    spot_max_dia = config.get('spot_drill_max_dia_mm', 6.5)

    z_min = min(b.boundingBox.minPoint.z for b in result.bodies)
    z_max = max(b.boundingBox.maxPoint.z for b in result.bodies)
    result.thickness_mm = (z_max - z_min) * 10.0
    for body in result.bodies:
        if abs(body.boundingBox.minPoint.z - z_min) > _Z_TOL or \
                abs(body.boundingBox.maxPoint.z - z_max) > _Z_TOL:
            result.z_aligned = False
    if not result.z_aligned:
        result.warnings.append('上下面が揃っていないボディがあります（「位置合わせ」を確認）。')

    # 収集バッファ
    outer_loops = []                    # 外郭: ループ（エッジ列）
    hole_groups = {}                    # 穴径 -> [ループ]
    unassigned_circles = {}             # 穴径 -> 個数（スポットドリル対象）
    naikaku_loops = []                  # (ループ, 開口幅mm, 最小特徴半径mm)
    zaguri_faces = []                   # (面, 開口径mm)
    pocket_faces = []                   # (面, 開口幅mm)

    for body in result.bodies:
        body_z_min = body.boundingBox.minPoint.z
        body_z_max = body.boundingBox.maxPoint.z
        faces = _horizontal_planar_faces(body)

        bottom_candidates = [f for f, z, is_up in faces
                             if not is_up and abs(z - body_z_min) < _Z_TOL]
        if not bottom_candidates:
            result.warnings.append(f'ボディ {body.name!r}: 底面が見つからずスキップしました。')
            continue
        bottom = max(bottom_candidates, key=lambda f: f.area)

        for loop in bottom.loops:
            if loop.isOuter:
                outer_loops.append(list(loop.edges))
                continue
            circle = _is_full_circle_loop(loop)
            if circle is not None:
                diameter_mm = circle.radius * 2.0 * 10.0
                template = registry.hole_template(diameter_mm, hole_tolerance)
                if template is not None:
                    hole_groups.setdefault(round(diameter_mm, 2), []).append(list(loop.edges))
                elif diameter_mm <= spot_max_dia:
                    key = round(diameter_mm, 2)
                    unassigned_circles[key] = unassigned_circles.get(key, 0) + 1
                else:
                    naikaku_loops.append((list(loop.edges), diameter_mm,
                                          _min_feature_radius_mm(loop)))
            else:
                naikaku_loops.append((list(loop.edges),
                                      _opening_estimate_mm(loop),
                                      _min_feature_radius_mm(loop)))

        # 中間高さの上向き平面 = ポケット/ざぐりの底
        for face, z, is_up in faces:
            if not is_up or z <= body_z_min + _Z_TOL or z >= body_z_max - _Z_TOL:
                continue
            outer_loop = next((lp for lp in face.loops if lp.isOuter), None)
            circle = _is_full_circle_loop(outer_loop) if outer_loop else None
            if circle is not None:
                zaguri_faces.append((face, circle.radius * 2.0 * 10.0))
            else:
                opening = _opening_estimate_mm(outer_loop) if outer_loop else None
                pocket_faces.append((face, opening))

    items = []

    # ざぐり（工具径ごとにまとめて1操作）
    items += _group_faces_by_tool(registry, tr.KIND_ZAGURI, zaguri_faces,
                                  preferred.get(tr.KIND_ZAGURI, [3.0, 2.0, 1.5]), tool_margin)
    # 内郭ポケット
    items += _group_faces_by_tool(registry, tr.KIND_POCKET, pocket_faces,
                                  preferred.get(tr.KIND_POCKET, [5.0, 3.0, 1.5]), tool_margin)

    # 穴あけ（径ごとに1操作）
    for diameter_mm in sorted(hole_groups):
        loops = hole_groups[diameter_mm]
        template = registry.hole_template(diameter_mm, hole_tolerance)
        item = PlanItem(tr.KIND_HOLE, f'穴あけ Φ{diameter_mm:g} ×{len(loops)}', template)
        item.loops = loops
        items.append(item)

    # 内郭（工具径ごとにまとめる）＋ 取り残し提案
    naikaku_templates = registry.find(tr.KIND_NAIKAKU)
    min_tool_dia = min((t.tool_diameter_mm for t in naikaku_templates), default=1.5)
    naikaku_by_template = {}
    too_narrow_count = 0
    for edges, opening_mm, min_feature_mm in naikaku_loops:
        # 最小工具でも入らない開口は未割り当てにする（設計 or ボール盤対応）
        if opening_mm is not None and opening_mm < min_tool_dia + tool_margin:
            too_narrow_count += 1
            continue
        template = registry.pick(tr.KIND_NAIKAKU, opening_mm,
                                 preferred.get(tr.KIND_NAIKAKU, [3.0, 1.5]), tool_margin)
        if template is None:
            result.warnings.append('内郭テンプレートが見つかりません。')
            continue
        naikaku_by_template.setdefault(template.name, []).append(
            (edges, opening_mm, min_feature_mm))
    rest_loops = []
    for name, entries in naikaku_by_template.items():
        template = registry.by_name(name)
        item = PlanItem(tr.KIND_NAIKAKU,
                        f'内郭 ×{len(entries)}（Φ{template.tool_diameter_mm:g}）', template)
        item.loops = [e for e, _, _ in entries]
        items.append(item)
        tool_radius = template.tool_diameter_mm / 2.0
        for edges, _, min_feature_mm in entries:
            # 工具半径より小さい隅がある → 細い工具での取り残し加工を提案
            if min_feature_mm is not None and min_feature_mm < tool_radius - 0.01 \
                    and template.tool_diameter_mm > 1.5:
                rest_loops.append(edges)
    if too_narrow_count:
        result.warnings.append(
            f'最小工具（Φ{min_tool_dia:g}）でも入らない内郭が {too_narrow_count} 件あります'
            '（未割り当て。設計を確認してください）。')
    if rest_loops:
        rest_template = registry.pick(tr.KIND_TORINOKOSHI, None, [1.5], tool_margin)
        if rest_template is not None:
            item = PlanItem(tr.KIND_TORINOKOSHI,
                            f'取り残し加工 ×{len(rest_loops)}（Φ{rest_template.tool_diameter_mm:g}）',
                            rest_template,
                            note='Φ3で削り残る隅を検出（要確認）')
            item.loops = rest_loops
            items.append(item)

    # 外郭（最後）
    if outer_loops:
        template = registry.pick(tr.KIND_GAIKAKU, None,
                                 preferred.get(tr.KIND_GAIKAKU, [3.0, 2.0]), tool_margin)
        item = PlanItem(tr.KIND_GAIKAKU, f'外郭 ×{len(outer_loops)}', template,
                        note='パーツ間隔が狭い場合は Φ2.0 に変更')
        item.loops = outer_loops
        items.append(item)

    # 未割り当ての円（ボール盤穴）: 情報行として最後に出す
    for diameter_mm in sorted(unassigned_circles):
        count = unassigned_circles[diameter_mm]
        item = PlanItem(tr.KIND_HOLE, f'Φ{diameter_mm:g} ×{count}', None,
                        note='ルール外の穴径。ボール盤（スポットドリル）で対応')
        items.append(item)

    result.items = sorted(items, key=lambda it: tr.KIND_ORDER.index(it.kind))
    return result


def _group_faces_by_tool(registry, kind, face_entries, preferred_diameters, margin_mm):
    by_template = {}
    for face, opening_mm in face_entries:
        template = registry.pick(kind, opening_mm, preferred_diameters, margin_mm)
        if template is None:
            continue
        by_template.setdefault(template.name, []).append(face)
    items = []
    for name, faces in by_template.items():
        template = registry.by_name(name)
        item = PlanItem(kind, f'{kind} ×{len(faces)}（Φ{template.tool_diameter_mm:g}）', template)
        item.faces = faces
        items.append(item)
    return items
