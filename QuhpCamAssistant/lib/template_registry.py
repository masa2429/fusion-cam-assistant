# テンプレフォルダをスキャンし、ファイル名と中身（XML）から
# 「工具径 × 加工種別（× 対象穴径）」の一覧を作る。
#
# ファイル名の規約: [Φ<工具径>]<種別>
#   例: [Φ3.0]外郭 / [Φ1.5]Φ2.0 穴あけ / [Φ2]ざぐり

import os
import re
import xml.etree.ElementTree as ET

# 加工種別（分類器・確認ダイアログと共通の語彙。加工順もこの順序で決まる）
KIND_ZAGURI = 'ざぐり'
KIND_POCKET = '内郭ポケット'
KIND_HOLE = '穴あけ'
KIND_NAIKAKU = '内郭'
KIND_TORINOKOSHI = '取り残し加工'
KIND_GAIKAKU = '外郭'

# 加工順の不変条件: ポケット/ざぐり → 穴あけ → 内郭 → 取り残し → 外郭（最後）
KIND_ORDER = [KIND_ZAGURI, KIND_POCKET, KIND_HOLE, KIND_NAIKAKU, KIND_TORINOKOSHI, KIND_GAIKAKU]

_STEM_RE = re.compile(r'^\[Φ([0-9.]+)\]\s*(.+)$')
_HOLE_DIA_RE = re.compile(r'Φ([0-9.]+)')
_XML_NS = '{http://www.hsmworks.com/namespace/hsmworks/document/template}'


class Template:
    def __init__(self, path, tool_diameter_mm, kind, hole_diameter_mm, strategy):
        self.path = path
        self.name = os.path.splitext(os.path.basename(path))[0]
        self.tool_diameter_mm = tool_diameter_mm
        self.kind = kind
        self.hole_diameter_mm = hole_diameter_mm  # 穴あけテンプレのみ。他は None
        self.strategy = strategy                  # 'contour2d' / 'pocket2d' 等

    def __repr__(self):
        return 'Template({0.name!r}, tool={0.tool_diameter_mm}, kind={0.kind!r})'.format(self)


def _detect_kind(rest):
    if KIND_HOLE in rest:
        return KIND_HOLE
    if KIND_POCKET in rest:
        return KIND_POCKET
    if KIND_NAIKAKU in rest:
        return KIND_NAIKAKU
    if KIND_GAIKAKU in rest:
        return KIND_GAIKAKU
    if KIND_ZAGURI in rest:
        return KIND_ZAGURI
    if '取り残し' in rest:
        return KIND_TORINOKOSHI
    return None


def _read_strategy(path):
    try:
        root = ET.parse(path).getroot()
        template = root.find(f'{_XML_NS}template')
        return template.get('strategy') if template is not None else None
    except ET.ParseError:
        return None


def scan(template_dir):
    """テンプレフォルダ直下（inventor/ は除外）を再帰スキャンし Template のリストを返す。"""
    templates = []
    for dirpath, dirnames, filenames in os.walk(template_dir):
        dirnames[:] = [d for d in dirnames if d != 'inventor']
        for filename in filenames:
            if not filename.endswith('.f3dhsm-template'):
                continue
            stem = os.path.splitext(filename)[0]
            match = _STEM_RE.match(stem)
            if not match:
                continue
            tool_diameter = float(match.group(1))
            rest = match.group(2)
            kind = _detect_kind(rest)
            if kind is None:
                continue
            hole_diameter = None
            if kind == KIND_HOLE:
                dia_match = _HOLE_DIA_RE.search(rest)
                if dia_match:
                    hole_diameter = float(dia_match.group(1))
            path = os.path.join(dirpath, filename)
            templates.append(Template(path, tool_diameter, kind, hole_diameter,
                                      _read_strategy(path)))
    return templates


class Registry:
    def __init__(self, template_dir):
        self.templates = scan(template_dir)
        if not self.templates:
            raise FileNotFoundError(f'テンプレートが見つかりません: {template_dir}')

    def by_name(self, name):
        for t in self.templates:
            if t.name == name:
                return t
        return None

    def find(self, kind, tool_diameter_mm=None):
        result = [t for t in self.templates if t.kind == kind]
        if tool_diameter_mm is not None:
            result = [t for t in result
                      if abs(t.tool_diameter_mm - tool_diameter_mm) < 0.01]
        return result

    def hole_template(self, hole_diameter_mm, tolerance_mm=0.05):
        """穴径に一致する穴あけテンプレを返す（なければ None）。"""
        for t in self.find(KIND_HOLE):
            if t.hole_diameter_mm is not None and \
                    abs(t.hole_diameter_mm - hole_diameter_mm) <= tolerance_mm:
                return t
        return None

    def pick(self, kind, min_opening_mm, preferred_diameters, margin_mm=0.5):
        """開口幅に入る最大優先の工具のテンプレを選ぶ。入る工具が無ければ None。"""
        for dia in preferred_diameters:
            if min_opening_mm is None or min_opening_mm >= dia + margin_mm:
                candidates = self.find(kind, dia)
                if candidates:
                    return candidates[0]
        # どの優先工具も入らない場合は最小工具のテンプレを候補として返す
        candidates = sorted(self.find(kind), key=lambda t: t.tool_diameter_mm)
        return candidates[0] if candidates else None
