# ボディ（板パーツ、2.5D前提）を解析し、加工候補（PlanItem）の一覧を作る。
#
# 分類ルール（部内 wiki「切削データ作成のススメ」と CLAUDE.md 準拠）:
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

from . import fusion_utils
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
        self.cylinders = []         # bore系: 穴/ざぐりの円筒側面 BRepFace リスト
        # ポケット床を輪郭パスで加工する場合 True（ボトム高さを床ちょうどに補正する）
        self.floor_contour = False
        # ポケット面の開口辺（部品外形に開いている辺）: face.tempId -> [(edge, 外向き法線)]
        # 開口辺を持つ面は cam_builder が境界スケッチを拡張して渡す
        self.open_edge_map = {}
        # 加工サイド（True=反時計回り=内側）。外郭由来の取り残しでは False に上書きする
        self.side_ccw = kind != tr.KIND_GAIKAKU
        # ループごとの加工サイド上書き（面取りのように外側/内側ループが混在する場合）
        self.loop_sides = None      # None or loops と同じ長さの bool リスト

    @property
    def selection_count(self):
        return max(len(self.loops), len(self.faces), len(self.cylinders))


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
    """面の外向き法線（ワールド座標）。プロキシ面はネイティブ面で評価してから
    オカレンス変換で明示的にワールドへ直す（fusion_utils.proxy_world_transform 参照。
    回転配置の部品で境界拡張が内側を向く不具合の原因だった）。"""
    transform = fusion_utils.proxy_world_transform(face)
    native = face.nativeObject if transform is not None else face
    ok, normal = native.evaluator.getNormalAtPoint(native.pointOnFace)
    if not ok:
        return None
    if transform is not None:
        normal.transformBy(transform)  # Vector3D には回転成分のみ適用される
    return normal


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


def _cylinder_face_of(circle_edge):
    """円形エッジに隣接する円筒側面（ボア加工の選択対象）を返す。無ければ None。"""
    try:
        for face in circle_edge.faces:
            if face.geometry.surfaceType == adsk.core.SurfaceTypes.CylinderSurfaceType:
                return face
    except Exception:
        pass
    return None


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
    曲がった細溝は外接矩形では判定できないため、面積 A と周長 P から
    「等価長方形の短辺」 w = (P/2 - sqrt((P/2)^2 - 4A)) / 2 を使う
    （長方形・正方形・直線/曲線スロットで厳密。円形など判別式が負の形状は
    外接矩形の短辺にフォールバック）。"""
    bbox_min = _loop_bbox_min_dimension_mm(loop)
    perimeter = _loop_perimeter_cm(loop)
    area = _loop_polygon_area_cm2(loop)
    if area is not None and perimeter and perimeter > 1e-6:
        half_perimeter = perimeter / 2.0
        discriminant = half_perimeter * half_perimeter - 4.0 * area
        if discriminant >= 0.0:
            width = (half_perimeter - math.sqrt(discriminant)) / 2.0 * 10.0
            if bbox_min is not None:
                return min(bbox_min, width)
            return width
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


def _concave_feature_radius_mm(loop, machining_ccw):
    """加工方向（+Z視点で 内郭=反時計回り・外郭=時計回り）でループをたどったとき、
    工具側へ凹む特徴の最小半径（mm）を返す。
    左曲がりの尖った頂点 = 0、左曲がりの円弧 = その半径。凸側（右曲がり）は
    工具が回り込めるので無視する。凹み特徴が無ければ math.inf、判定失敗は None。"""
    try:
        coedges = list(loop.coEdges)
        if not coedges:
            return None
        tangents = []   # コエッジ順トラバース方向の (始点接線, 終点接線)
        arc_infos = []  # (半径mm, トラバース方向で中心が左なら正の外積)
        points = []
        for coedge in coedges:
            edge = coedge.edge
            evaluator = edge.evaluator
            ok, t_start, t_end = evaluator.getParameterExtents()
            ok2, stroke = evaluator.getStrokes(t_start, t_end, 0.01)
            ok3, tan_start = evaluator.getTangent(t_start)
            ok4, tan_end = evaluator.getTangent(t_end)
            if not (ok and ok2 and ok3 and ok4) or len(stroke) < 2:
                return None
            sequence = list(stroke)
            if coedge.isOpposedToEdge:
                sequence.reverse()
                tan_start, tan_end = tan_end.copy(), tan_start.copy()
                tan_start.scaleBy(-1.0)
                tan_end.scaleBy(-1.0)
            points.extend(sequence[:-1])
            tangents.append((tan_start, tan_end))
            geometry = edge.geometry
            if geometry.curveType in (adsk.core.Curve3DTypes.Circle3DCurveType,
                                      adsk.core.Curve3DTypes.Arc3DCurveType):
                mid_index = len(sequence) // 2
                before = sequence[max(0, mid_index - 1)]
                after = sequence[min(len(sequence) - 1, mid_index + 1)]
                mid = sequence[mid_index]
                center = geometry.center
                cross = ((after.x - before.x) * (center.y - mid.y)
                         - (after.y - before.y) * (center.x - mid.x))
                arc_infos.append((geometry.radius * 10.0, cross))
        if len(points) < 3:
            return None
        # 現在のトラバース方向を符号付き面積で判定し、加工方向に合わせて符号を反転
        area2 = 0.0
        for i in range(len(points)):
            p1 = points[i]
            p2 = points[(i + 1) % len(points)]
            area2 += p1.x * p2.y - p2.x * p1.y
        if abs(area2) < 1e-12:
            return None
        flip = 1.0 if (area2 > 0) == machining_ccw else -1.0

        min_radius = math.inf
        count = len(tangents)
        for i in range(count):
            outgoing = tangents[i][1]
            incoming = tangents[(i + 1) % count][0]
            if outgoing.angleTo(incoming) > _TANGENT_TOL_RAD:
                cross = (outgoing.x * incoming.y - outgoing.y * incoming.x) * flip
                if cross > 0:  # 左曲がり = 工具側に凹む尖った隅
                    min_radius = 0.0
        for radius_mm, cross in arc_infos:
            if cross * flip > 0:  # 中心が工具側 = 凹円弧
                min_radius = min(min_radius, radius_mm)
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
    outer_loops = []                    # 外郭: (ループ, 凹み特徴半径mm)
    hole_groups = {}                    # 穴径 -> [(ループ, 円筒側面)]
    unassigned_circles = {}             # 穴径 -> 個数（スポットドリル対象）
    naikaku_loops = []                  # (ループ, 開口幅mm, 最小特徴半径mm)
    zaguri_faces = []                   # (底面, 径mm, 円筒側面)
    pocket_faces = []                   # (面, 開口幅mm, 開口辺リスト)
    chamfer_loops = []                  # 上面の (ループ, 内側ならTrue=CCW)

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

        # 部品外形の側面（底面の外側ループに接する壁）。ポケット開口辺の判定に使う
        silhouette_wall_ids = set()
        for loop in bottom.loops:
            if loop.isOuter:
                for edge in loop.edges:
                    for adjacent in edge.faces:
                        if adjacent != bottom:
                            silhouette_wall_ids.add(adjacent.tempId)

        for loop in bottom.loops:
            if loop.isOuter:
                feature = _concave_feature_radius_mm(loop, machining_ccw=False)
                fusion_utils.log(f'外郭ループ {body.name!r}: 凹み特徴半径 = '
                                 f'{"判定失敗" if feature is None else f"{feature:.2f}mm"}')
                outer_loops.append((list(loop.edges), feature))
                continue
            circle = _is_full_circle_loop(loop)
            if circle is not None:
                diameter_mm = circle.radius * 2.0 * 10.0
                template = registry.hole_template(diameter_mm, hole_tolerance)
                if template is not None:
                    hole_groups.setdefault(round(diameter_mm, 2), []).append(
                        (list(loop.edges), _cylinder_face_of(loop.edges.item(0))))
                elif diameter_mm <= spot_max_dia:
                    key = round(diameter_mm, 2)
                    unassigned_circles[key] = unassigned_circles.get(key, 0) + 1
                else:
                    naikaku_loops.append((list(loop.edges), diameter_mm,
                                          _concave_feature_radius_mm(loop, machining_ccw=True)))
            else:
                naikaku_loops.append((list(loop.edges),
                                      _opening_estimate_mm(loop),
                                      _concave_feature_radius_mm(loop, machining_ccw=True)))

        # 中間高さの上向き平面 = ポケット/ざぐりの底
        for face, z, is_up in faces:
            if not is_up or z <= body_z_min + _Z_TOL or z >= body_z_max - _Z_TOL:
                continue
            outer_loop = next((lp for lp in face.loops if lp.isOuter), None)
            circle = _is_full_circle_loop(outer_loop) if outer_loop else None
            if circle is not None:
                zaguri_faces.append((face, circle.radius * 2.0 * 10.0,
                                     _cylinder_face_of(outer_loop.edges.item(0))))
            else:
                opening = _opening_estimate_mm(outer_loop) if outer_loop else None
                # 部品外形に開いている辺（開口辺）を検出。壁面の外向き法線＝拡張方向
                open_edges = []
                if outer_loop is not None:
                    for edge in outer_loop.edges:
                        for adjacent in edge.faces:
                            if adjacent != face and adjacent.tempId in silhouette_wall_ids:
                                normal = _outward_normal(adjacent)
                                if normal is not None:
                                    open_edges.append((edge, normal))
                                break
                pocket_faces.append((face, opening, open_edges))

        # 上面のループ = 面取りの候補（内側ループ=内側を削る、外側ループ=外側を削る）
        top_candidates = [f for f, z, is_up in faces
                          if is_up and abs(z - body_z_max) < _Z_TOL]
        if top_candidates:
            top = max(top_candidates, key=lambda f: f.area)
            for loop in top.loops:
                chamfer_loops.append((list(loop.edges), not loop.isOuter))

    items = []

    # ざぐり（テンプレごとにまとめて1操作。径一致のボア系があれば優先セット順で選ぶ）
    zaguri_by_template = {}
    for face, diameter_mm, wall in zaguri_faces:
        bore_template = registry.zaguri_template(diameter_mm, hole_tolerance)
        pocket_template = registry.pick(tr.KIND_ZAGURI, diameter_mm,
                                        preferred.get(tr.KIND_ZAGURI, [3.0, 2.0, 1.5]),
                                        tool_margin)
        candidates = [t for t in (bore_template, pocket_template) if t is not None]
        if not candidates:
            result.warnings.append(f'ざぐり Φ{diameter_mm:g} に使えるテンプレがありません。')
            continue
        template = sorted(candidates, key=registry._set_order)[0]
        zaguri_by_template.setdefault(template.name, []).append((face, wall))
    for name, entries in zaguri_by_template.items():
        template = registry.by_name(name)
        item = PlanItem(tr.KIND_ZAGURI,
                        f'ざぐり ×{len(entries)}（Φ{template.tool_diameter_mm:g}）', template)
        item.faces = [face for face, _ in entries]
        item.cylinders = [wall for _, wall in entries if wall is not None]
        items.append(item)

    # 内郭ポケット（開口面は境界拡張で余白側にヘリカルの余地ができる前提で判定。
    # 閉じた細幅の面だけ輪郭パス（プロファイルランプ）へ自動振替する）
    items += _group_pocket_faces(registry, pocket_faces,
                                 preferred.get(tr.KIND_POCKET, [5.0, 4.0, 3.0, 1.5]),
                                 tool_margin,
                                 config.get('stock_side_margin_mm', 5.0), result)

    # 穴あけ（径ごとに1操作）
    for diameter_mm in sorted(hole_groups):
        entries = hole_groups[diameter_mm]
        template = registry.hole_template(diameter_mm, hole_tolerance)
        item = PlanItem(tr.KIND_HOLE, f'穴あけ Φ{diameter_mm:g} ×{len(entries)}', template)
        item.loops = [edges for edges, _ in entries]
        item.cylinders = [wall for _, wall in entries if wall is not None]
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
        note = ''
        wide = [o for _, o, _ in entries
                if o is not None and o > template.tool_diameter_mm * 3]
        if wide:
            note = f'開口の広いループが {len(wide)} 件。島が残って飛ぶ場合は「くり抜き」系テンプレに変更'
        item = PlanItem(tr.KIND_NAIKAKU,
                        f'内郭 ×{len(entries)}（Φ{template.tool_diameter_mm:g}）', template,
                        note=note)
        item.loops = [e for e, _, _ in entries]
        items.append(item)
        tool_radius = template.tool_diameter_mm / 2.0
        for edges, _, min_feature_mm in entries:
            # 工具半径より小さい隅がある → 細い工具での取り残し加工を提案
            if min_feature_mm is not None and min_feature_mm < tool_radius - 0.01 \
                    and template.tool_diameter_mm > 1.5:
                rest_loops.append(edges)
    if too_narrow_count:
        items.append(PlanItem(
            tr.KIND_NAIKAKU, f'内郭（開口幅が狭い） ×{too_narrow_count}', None,
            note=f'最小工具 Φ{min_tool_dia:g} でも入らない開口。設計を確認してください'))
    if rest_loops:
        rest_template = registry.pick(tr.KIND_TORINOKOSHI, None, [1.5], tool_margin)
        if rest_template is not None:
            item = PlanItem(tr.KIND_TORINOKOSHI,
                            f'取り残し加工（内郭） ×{len(rest_loops)}'
                            f'（Φ{rest_template.tool_diameter_mm:g}）',
                            rest_template,
                            note='Φ3で削り残る隅を検出（要確認）')
            item.loops = rest_loops
            items.append(item)

    # 外郭（最後）＋ 外郭の凹隅の取り残し提案
    if outer_loops:
        template = registry.pick(tr.KIND_GAIKAKU, None,
                                 preferred.get(tr.KIND_GAIKAKU, [3.0, 2.0]), tool_margin)
        item = PlanItem(tr.KIND_GAIKAKU, f'外郭 ×{len(outer_loops)}', template,
                        note='パーツ間隔が狭い場合は Φ2.0 に変更')
        item.loops = [edges for edges, _ in outer_loops]
        items.append(item)
        if template is not None and template.tool_diameter_mm > 1.5:
            tool_radius = template.tool_diameter_mm / 2.0
            outer_rest = [edges for edges, feature in outer_loops
                          if feature is not None and feature < tool_radius - 0.01]
            if outer_rest:
                rest_template = registry.pick(tr.KIND_TORINOKOSHI, None, [1.5], tool_margin)
                if rest_template is not None:
                    rest_item = PlanItem(
                        tr.KIND_TORINOKOSHI,
                        f'取り残し加工（外郭） ×{len(outer_rest)}'
                        f'（Φ{rest_template.tool_diameter_mm:g}）',
                        rest_template,
                        note='外郭の凹んだ隅を検出。外郭の切り離しより先に加工される')
                    rest_item.loops = outer_rest
                    rest_item.side_ccw = False  # 外郭と同じく輪郭の外側を削る
                    items.append(rest_item)

    # 面取り（DLCセット。既定OFF＝必要なときだけチェックを入れる）
    chamfer_templates = registry.find(tr.KIND_CHAMFER)
    if chamfer_loops and chamfer_templates:
        template = chamfer_templates[0]
        item = PlanItem(tr.KIND_CHAMFER, f'面取り ×{len(chamfer_loops)}', template,
                        note='上端エッジ全周を面取り。必要なときだけチェックを入れる')
        item.enabled = False
        item.loops = [edges for edges, _ in chamfer_loops]
        item.loop_sides = [inner_ccw for _, inner_ccw in chamfer_loops]
        items.append(item)

    # 未割り当ての円（ボール盤穴）: 情報行として最後に出す
    for diameter_mm in sorted(unassigned_circles):
        count = unassigned_circles[diameter_mm]
        item = PlanItem(tr.KIND_HOLE, f'Φ{diameter_mm:g} ×{count}', None,
                        note='ルール外の穴径。ボール盤（スポットドリル）で対応')
        items.append(item)

    result.items = sorted(items, key=lambda it: tr.KIND_ORDER.index(it.kind))
    return result


# cam_builder の minimumRampDiameter 上書き（工具径×0.25）と揃えること
_HELIX_MIN_RATIO = 0.25


def _pocket_template_feasible(template, opening_mm, margin_mm):
    """その開口幅で安全に進入できるテンプレか（垂直プランジに落ちないか）。
    adaptive2d はヘリカル進入のみなので「工具径＋最小ヘリカル径」の幅が必要。"""
    if opening_mm is None or template.tool_diameter_mm is None:
        return True
    need = template.tool_diameter_mm + margin_mm
    if (template.strategy or '').lower() == 'adaptive2d':
        need = template.tool_diameter_mm * (1.0 + _HELIX_MIN_RATIO) + margin_mm
    return opening_mm >= need


def _group_pocket_faces(registry, face_entries, preferred_diameters, margin_mm,
                        stock_margin_mm, result):
    """ポケット面をテンプレに割り当てる。
    - 開口面（部品外形に開いている面）は境界スケッチをストック余白側へ拡張して
      加工するため、実効幅 = 開口幅 + 余白 で進入成立を判定する
    - 閉じた面で進入が成立しない細幅は、輪郭パス（プロファイルランプ、
      幅≦2×工具径なら1パスで削り切れる）へ振り替える"""
    def sort_key(template):
        set_rank = 0 if template.set_name == registry.preferred_set else 1
        diameter = template.tool_diameter_mm or 0
        try:
            pref_rank = preferred_diameters.index(diameter)
        except ValueError:
            pref_rank = len(preferred_diameters)
        return (set_rank, pref_rank, -diameter)

    pocket_by_template = {}   # name -> [(face, open_edges)]
    contour_by_template = {}
    for face, opening_mm, open_edges in face_entries:
        effective_mm = opening_mm
        if open_edges and opening_mm is not None:
            effective_mm = opening_mm + stock_margin_mm
        candidates = [t for t in registry.find(tr.KIND_POCKET)
                      if _pocket_template_feasible(t, effective_mm, margin_mm)]
        if candidates:
            template = sorted(candidates, key=sort_key)[0]
            pocket_by_template.setdefault(template.name, []).append((face, open_edges))
            continue
        # 閉じた細幅: 輪郭テンプレ（contour2d・プロファイルランプ）で床の輪郭を1パス加工
        contour_template = None
        for t in sorted(registry.find(tr.KIND_NAIKAKU), key=sort_key):
            diameter = t.tool_diameter_mm or 0
            if opening_mm is not None and \
                    opening_mm >= diameter + margin_mm and opening_mm <= diameter * 2:
                contour_template = t
                break
        if contour_template is not None:
            contour_by_template.setdefault(contour_template.name, []).append(face)
        else:
            width_text = f'{opening_mm:.1f}mm' if opening_mm is not None else '不明'
            result.warnings.append(
                f'幅 {width_text} のポケット面に安全に進入できる工具がありません（未割り当て）。')

    items = []
    for name, entries in pocket_by_template.items():
        template = registry.by_name(name)
        open_count = sum(1 for _, open_edges in entries if open_edges)
        note = f'開口面 {open_count} 件は境界をストック余白側へ拡張して加工' if open_count else ''
        item = PlanItem(tr.KIND_POCKET,
                        f'内郭ポケット ×{len(entries)}（Φ{template.tool_diameter_mm:g}）',
                        template, note=note)
        item.faces = [face for face, _ in entries]
        item.open_edge_map = {face.tempId: open_edges
                              for face, open_edges in entries if open_edges}
        items.append(item)
    for name, faces in contour_by_template.items():
        template = registry.by_name(name)
        item = PlanItem(tr.KIND_POCKET,
                        f'内郭ポケット ×{len(faces)}（Φ{template.tool_diameter_mm:g} 輪郭パス）',
                        template,
                        note='細幅のため輪郭パスで加工（負荷制御はヘリカル進入が成立しない）')
        for face in faces:
            outer = next((lp for lp in face.loops if lp.isOuter), None)
            if outer is not None:
                item.loops.append(list(outer.edges))
        item.floor_contour = True
        if item.loops:
            items.append(item)
    return items
