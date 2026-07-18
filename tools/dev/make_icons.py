# 開発用（PC側で実行、Fusion スクリプトではない）:
# パネルボタン用のアイコン PNG を生成して FusionCamAssistant/resources/ に書き出す。
# 実行: py -3 tools/dev/make_icons.py   （要 Pillow。生成物はリポジトリにコミットする）
#
# Fusion のボタン定義はリソースフォルダ内の 16x16.png / 32x32.png / 64x64.png を拾う。
# フォルダ名はコマンド ID と同じにする（fusion_utils.add_command が自動で解決する）。
#
# ダークテーマ対応: Fusion はリソースフォルダ内の「-dark」サフィックス付きファイル
# （16x16-dark.png 等）をダークテーマ時に自動で拾う（Fusion 本体の実物で確認済み）。
# そのため各アイコンについてライト版・ダーク版の両方をここで生成する。
# 配色はテーマごとのパレット（PALETTES）にまとめ、各 draw 関数は色をパレット引数
# 経由で受け取る（モジュール定数のベタ書き参照はしない）。

import math
import os

from PIL import Image, ImageDraw

# テーマごとの配色パレット。キーはファイル名サフィックス（''=ライト, '-dark'=ダーク）。
# gray  : グリフの標準色
# blue  : アクセント色
# notch : グリフに空ける「くぼみ」の色（背景寄りの色。draw_auto_cam の刃部ハイライトで使用）
PALETTES = {
    '': {
        'gray': (74, 74, 74, 255),      # Fusion ライトテーマの標準グリフ色に近いグレー
        'blue': (6, 150, 215, 255),     # Fusion ブルー（アクセント）
        'notch': (255, 255, 255, 255),  # ライト版の刃部ハイライトは白のまま
    },
    '-dark': {
        'gray': (208, 212, 216, 255),   # #D0D4D8: ダーク背景(#2B2F33)でも視認できる明るいグレー
        'blue': (79, 195, 247, 255),    # #4FC3F7: ダーク向けに明るく振ったブルー
        'notch': (43, 47, 51, 255),     # #2B2F33: Fusion ダークテーマの背景色寄りのくぼみ色
    },
}

SS = 8                          # スーパーサンプリング倍率
SIZES = (16, 32, 64)

OUT_ROOT = os.path.join(os.path.dirname(__file__), '..', '..',
                        'FusionCamAssistant', 'resources')


def _canvas(base):
    size = base * SS
    image = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    return image, ImageDraw.Draw(image), size


def _save(image, base, folder, suffix=''):
    os.makedirs(folder, exist_ok=True)
    image.resize((base, base), Image.LANCZOS).save(
        os.path.join(folder, f'{base}x{base}{suffix}.png'))


def _pts(size, points):
    return [(x * size, y * size) for x, y in points]


def draw_auto_cam(d, s, pal):
    """切削データ自動作成: エンドミル＋ツールパス波線"""
    gray, blue, notch = pal['gray'], pal['blue'], pal['notch']
    w = s * 0.07
    # エンドミル本体（シャンク＋刃部）
    d.rounded_rectangle(_pts(s, [(0.40, 0.06), (0.60, 0.34)]), radius=s * 0.04, fill=gray)
    d.polygon(_pts(s, [(0.37, 0.34), (0.63, 0.34), (0.60, 0.58), (0.40, 0.58)]), fill=gray)
    d.line(_pts(s, [(0.44, 0.38), (0.56, 0.52)]), fill=notch, width=int(s * 0.035))
    # ツールパス（波線）
    path = [(0.08, 0.86), (0.26, 0.70), (0.44, 0.86), (0.62, 0.70), (0.80, 0.86), (0.92, 0.76)]
    d.line(_pts(s, path), fill=blue, width=int(w), joint='curve')


def draw_post_all(d, s, pal):
    """NC一括出力: 書類＋書き出し矢印"""
    gray, blue = pal['gray'], pal['blue']
    w = int(s * 0.06)
    d.rounded_rectangle(_pts(s, [(0.18, 0.06), (0.70, 0.70)]), radius=s * 0.05,
                        outline=gray, width=w)
    for y in (0.24, 0.38, 0.52):
        d.line(_pts(s, [(0.28, y), (0.60, y)]), fill=gray, width=int(s * 0.045))
    # 矢印
    aw = int(s * 0.075)
    d.line(_pts(s, [(0.30, 0.86), (0.82, 0.86)]), fill=blue, width=aw)
    d.polygon(_pts(s, [(0.78, 0.74), (0.96, 0.86), (0.78, 0.98)]), fill=blue)


def draw_auto_place(d, s, pal):
    """自動配置: 枠＋詰められた部品"""
    gray, blue = pal['gray'], pal['blue']
    w = int(s * 0.055)
    d.rounded_rectangle(_pts(s, [(0.06, 0.06), (0.94, 0.94)]), radius=s * 0.06,
                        outline=gray, width=w)
    r = s * 0.03
    d.rounded_rectangle(_pts(s, [(0.17, 0.17), (0.51, 0.51)]), radius=r, fill=gray)
    d.rounded_rectangle(_pts(s, [(0.60, 0.17), (0.83, 0.62)]), radius=r, fill=gray)
    d.rounded_rectangle(_pts(s, [(0.17, 0.60), (0.45, 0.83)]), radius=r, fill=gray)
    d.rounded_rectangle(_pts(s, [(0.54, 0.71), (0.83, 0.83)]), radius=r, fill=blue)


def draw_layout_check(d, s, pal):
    """配置チェック: 枠＋チェックマーク"""
    gray, blue = pal['gray'], pal['blue']
    w = int(s * 0.055)
    d.rounded_rectangle(_pts(s, [(0.06, 0.06), (0.94, 0.94)]), radius=s * 0.06,
                        outline=gray, width=w)
    d.line(_pts(s, [(0.24, 0.54), (0.44, 0.74), (0.78, 0.30)]), fill=blue,
           width=int(s * 0.1), joint='curve')


def draw_settings(d, s, pal):
    """設定: 歯車"""
    gray = pal['gray']
    cx = cy = s * 0.5
    r_out = s * 0.30
    r_tooth = s * 0.42
    tooth_half = math.pi / 14
    for i in range(7):
        angle = 2 * math.pi * i / 7 - math.pi / 2
        d.polygon([
            (cx + r_out * 0.9 * math.cos(angle - tooth_half * 1.6),
             cy + r_out * 0.9 * math.sin(angle - tooth_half * 1.6)),
            (cx + r_tooth * math.cos(angle - tooth_half),
             cy + r_tooth * math.sin(angle - tooth_half)),
            (cx + r_tooth * math.cos(angle + tooth_half),
             cy + r_tooth * math.sin(angle + tooth_half)),
            (cx + r_out * 0.9 * math.cos(angle + tooth_half * 1.6),
             cy + r_out * 0.9 * math.sin(angle + tooth_half * 1.6)),
        ], fill=gray)
    d.ellipse([cx - r_out, cy - r_out, cx + r_out, cy + r_out], fill=gray)
    hole = s * 0.13
    d.ellipse([cx - hole, cy - hole, cx + hole, cy + hole], fill=(0, 0, 0, 0))


def draw_about(d, s, pal):
    """バージョン情報: ○の中に i"""
    gray, blue = pal['gray'], pal['blue']
    w = int(s * 0.06)
    d.ellipse(_pts(s, [(0.08, 0.08), (0.92, 0.92)]), outline=gray, width=w)
    dot = s * 0.065
    cx = s * 0.5
    d.ellipse([cx - dot, s * 0.24 - dot, cx + dot, s * 0.24 + dot], fill=blue)
    d.rounded_rectangle([cx - s * 0.055, s * 0.38, cx + s * 0.055, s * 0.74],
                        radius=s * 0.05, fill=blue)


ICONS = {
    'fcaAutoCam': draw_auto_cam,
    'fcaPostAll': draw_post_all,
    'fcaAutoPlace': draw_auto_place,
    'fcaLayoutCheck': draw_layout_check,
    'fcaSettings': draw_settings,
    'fcaAbout': draw_about,
}


def main():
    for command_id, painter in ICONS.items():
        folder = os.path.normpath(os.path.join(OUT_ROOT, command_id))
        for suffix, pal in PALETTES.items():
            for base in SIZES:
                image, d, s = _canvas(base)
                painter(d, s, pal)
                _save(image, base, folder, suffix)
        print(f'{command_id}: {folder}')


if __name__ == '__main__':
    main()
