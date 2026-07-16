# テンプレフォルダをスキャンし「工具 × 加工種別（× 対象径）」の一覧を作る。
#
# 2つのテンプレセットを扱う:
# - 標準セット: templates/Φ*/ の単一テンプレファイル。命名規約 [Φ<工具径>]<種別>
# - DLC セット: templates/inventor/ の Inventor 由来ファイル（.invhsm-template）。
#   1ファイルに複数の <template> が入った「操作セット」形式のため要素単位で列挙し、
#   説明文（description）を config の dlc_name_rules で種別・対象径にマッピングする。

import os
import re
import xml.etree.ElementTree as ET

# 加工種別（分類器・確認ダイアログと共通の語彙。加工順もこの順序で決まる）
KIND_ZAGURI = 'ざぐり'
KIND_POCKET = '内郭ポケット'
KIND_HOLE = '穴あけ'
KIND_NAIKAKU = '内郭'
KIND_TORINOKOSHI = '取り残し加工'
KIND_CHAMFER = '面取り'
KIND_GAIKAKU = '外郭'

# 加工順の不変条件: ポケット/ざぐり → 穴あけ → 内郭 → 取り残し → 面取り → 外郭（最後）
KIND_ORDER = [KIND_ZAGURI, KIND_POCKET, KIND_HOLE, KIND_NAIKAKU,
              KIND_TORINOKOSHI, KIND_CHAMFER, KIND_GAIKAKU]

SET_STANDARD = '標準'
SET_DLC = 'DLC'

_STEM_RE = re.compile(r'^\[Φ([0-9.]+)\]\s*(.+)$')
_HOLE_DIA_RE = re.compile(r'Φ([0-9.]+)')
_XML_NS = '{http://www.hsmworks.com/namespace/hsmworks/document/template}'

# DLC テンプレの既定マッピング（config.json の dlc_name_rules で上書き可能）。
# 上から順に最初に一致したルールを使う。kind が None のものは登録のみ（自動割当外）。
DEFAULT_DLC_NAME_RULES = [
    {'pattern': '穴1.5', 'kind': KIND_HOLE, 'target_diameter_mm': 2.0},
    {'pattern': 'ボア1 M3', 'kind': KIND_HOLE, 'target_diameter_mm': 2.5},
    {'pattern': 'ボア1 Φ3', 'kind': KIND_HOLE, 'target_diameter_mm': 3.0},
    {'pattern': 'ボア2 Φ4', 'kind': KIND_ZAGURI, 'target_diameter_mm': 4.0},
    {'pattern': 'ボア4 Φ6', 'kind': KIND_ZAGURI, 'target_diameter_mm': 6.0},
    {'pattern': '内輪郭', 'kind': KIND_NAIKAKU},
    {'pattern': '外輪郭', 'kind': KIND_GAIKAKU},
    {'pattern': '取り残し', 'kind': KIND_TORINOKOSHI},
    {'pattern': '負荷制御', 'kind': KIND_POCKET},
    {'pattern': '面取り', 'kind': KIND_CHAMFER},
    {'pattern': 'ポケット仕上げ', 'kind': None},
    {'pattern': 'ドリル', 'kind': None},
]


class Template:
    def __init__(self, path, tool_diameter_mm, kind, hole_diameter_mm, strategy,
                 set_name=SET_STANDARD, sub_index=None, name=None, tool_type=None):
        self.path = path
        self.name = name or os.path.splitext(os.path.basename(path))[0]
        self.tool_diameter_mm = tool_diameter_mm
        self.kind = kind
        self.hole_diameter_mm = hole_diameter_mm  # 対象径（穴あけ=穴径、ざぐり=ざぐり径）
        self.strategy = strategy                  # 'contour2d' / 'pocket2d' / 'bore' 等
        self.set_name = set_name                  # '標準' / 'DLC'
        self.sub_index = sub_index                # 複数テンプレ文書内の位置（単一なら None）
        self.tool_type = tool_type or 'flat end mill'

    @property
    def tool_key(self):
        """所有工具の識別キー（設定ダイアログ・config.local 用）。"""
        return f'{self.set_name}:Φ{self.tool_diameter_mm:g}:{self.tool_type}'

    def __repr__(self):
        return ('Template({0.name!r}, set={0.set_name}, tool={0.tool_diameter_mm}, '
                'kind={0.kind!r}, strategy={0.strategy!r})').format(self)


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
    if KIND_CHAMFER in rest:
        return KIND_CHAMFER
    return None


def _template_elements(path):
    """テンプレ文書内の <template> 要素を列挙する。パース失敗時は空。"""
    try:
        root = ET.parse(path).getroot()
        return root.findall(f'{_XML_NS}template')
    except ET.ParseError:
        return []


def _tool_info(template_element):
    """<template> 要素の埋め込み工具から (径mm, 種類) を読む。"""
    tool = template_element.find(f'{_XML_NS}tool')
    if tool is None:
        return None, None
    tool_type = tool.get('type')
    body = tool.find(f'{_XML_NS}body')
    diameter = None
    if body is not None and body.get('diameter'):
        try:
            diameter = float(body.get('diameter'))
        except ValueError:
            diameter = None
    return diameter, tool_type


def _scan_standard(template_dir):
    """標準セット: templates/Φ*/ の単一テンプレファイル（命名規約でマッピング）。"""
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
            elements = _template_elements(path)
            strategy = elements[0].get('strategy') if elements else None
            tool_dia, tool_type = _tool_info(elements[0]) if elements else (None, None)
            templates.append(Template(
                path, tool_dia or float(match.group(1)), kind, hole_diameter, strategy,
                set_name=SET_STANDARD, tool_type=tool_type))
    return templates


def _match_dlc_rule(description, rules):
    for rule in rules:
        if rule.get('pattern') and rule['pattern'] in description:
            return rule
    return None


def _scan_dlc(inventor_dir, name_rules):
    """DLC セット: inventor/ の複数テンプレ文書を要素単位で列挙・マッピング。"""
    templates = []
    if not os.path.isdir(inventor_dir):
        return templates
    for filename in sorted(os.listdir(inventor_dir)):
        if not filename.endswith(('.invhsm-template', '.f3dhsm-template')):
            continue
        path = os.path.join(inventor_dir, filename)
        # 「標準操作 (2)」→「標準操作」のように短縮してセット内の出所を表示に使う
        file_short = re.sub(r'\s*\(\d+\)\s*$', '', os.path.splitext(filename)[0])
        for index, element in enumerate(_template_elements(path)):
            description = element.get('description') or f'template{index}'
            rule = _match_dlc_rule(description, name_rules)
            if rule is None:
                continue
            tool_dia, tool_type = _tool_info(element)
            templates.append(Template(
                path, tool_dia, rule.get('kind'), rule.get('target_diameter_mm'),
                element.get('strategy'),
                set_name=SET_DLC, sub_index=index,
                name=f'[{file_short}] {description}', tool_type=tool_type))
    return templates


class Registry:
    def __init__(self, template_dir, config=None):
        config = config or {}
        name_rules = config.get('dlc_name_rules') or DEFAULT_DLC_NAME_RULES
        self.templates = _scan_standard(template_dir)
        self.templates += _scan_dlc(os.path.join(template_dir, 'inventor'), name_rules)
        if not self.templates:
            raise FileNotFoundError(f'テンプレートが見つかりません: {template_dir}')
        # 所有工具フィルタ（未設定なら全部所有扱い）と優先セット
        owned = config.get('owned_tools')
        self.owned_tools = set(owned) if owned else None
        self.preferred_set = config.get('preferred_set', SET_STANDARD)

    def is_owned(self, template):
        return self.owned_tools is None or template.tool_key in self.owned_tools

    def all_tool_keys(self):
        """登録テンプレの工具一覧（設定ダイアログ用、キー順）。"""
        return sorted({t.tool_key for t in self.templates if t.tool_diameter_mm})

    def by_name(self, name):
        for t in self.templates:
            if t.name == name:
                return t
        return None

    def _set_order(self, template):
        return 0 if template.set_name == self.preferred_set else 1

    def find(self, kind, tool_diameter_mm=None, owned_only=True):
        result = [t for t in self.templates if t.kind == kind]
        if tool_diameter_mm is not None:
            result = [t for t in result if t.tool_diameter_mm is not None
                      and abs(t.tool_diameter_mm - tool_diameter_mm) < 0.01]
        if owned_only:
            result = [t for t in result if self.is_owned(t)]
        return sorted(result, key=self._set_order)

    def hole_template(self, hole_diameter_mm, tolerance_mm=0.05):
        """穴径に一致する穴あけテンプレ（優先セット順）。なければ None。"""
        for t in self.find(KIND_HOLE):
            if t.hole_diameter_mm is not None and \
                    abs(t.hole_diameter_mm - hole_diameter_mm) <= tolerance_mm:
                return t
        return None

    def zaguri_template(self, diameter_mm, tolerance_mm=0.05):
        """ざぐり径に一致するボア系ざぐりテンプレ（対象径付きのもの）。なければ None。"""
        for t in self.find(KIND_ZAGURI):
            if t.hole_diameter_mm is not None and \
                    abs(t.hole_diameter_mm - diameter_mm) <= tolerance_mm:
                return t
        return None

    def pick(self, kind, min_opening_mm, preferred_diameters, margin_mm=0.5):
        """開口幅に入る工具のテンプレを選ぶ。
        優先セットの中を優先径リスト順→入る最大工具の順で探し、無ければもう一方の
        セットへ。どの工具も入らなければ最小工具を候補として返す（要確認扱い）。"""
        def fits(diameter):
            return min_opening_mm is None or min_opening_mm >= diameter + margin_mm

        set_order = [self.preferred_set] + \
            [s for s in (SET_STANDARD, SET_DLC) if s != self.preferred_set]
        for set_name in set_order:
            in_set = [t for t in self.find(kind)
                      if t.set_name == set_name and t.tool_diameter_mm is not None]
            for dia in preferred_diameters:
                if fits(dia):
                    for t in in_set:
                        if abs(t.tool_diameter_mm - dia) < 0.01:
                            return t
            fitting = [t for t in in_set if fits(t.tool_diameter_mm)]
            if fitting:
                return sorted(fitting, key=lambda t: -t.tool_diameter_mm)[0]
        # どの工具も入らない場合は最小工具のテンプレを候補として返す
        fallback = sorted(self.find(kind), key=lambda t: (t.tool_diameter_mm or 999))
        return fallback[0] if fallback else None
