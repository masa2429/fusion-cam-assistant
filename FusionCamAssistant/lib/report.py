# 不具合レポート生成：環境情報＋症状＋traceback＋直近ログを .txt に書き出し、
# メモ帳で開いて開発者へ送ってもらう（クリップボード API が不確実なため）。
#
# 自動トリガー（例外時の show_error_report）と手動トリガー（「問題を報告」コマンド）の
# 両方から使う。報告経路は必ずオフラインで速いこと（newer_remote_version は呼ばない）。

import datetime
import os
import tempfile
import traceback

import adsk.core

from . import fusion_utils
from . import update_check

# 「〜へ送って報告」の文言に使う。将来宛先を変えやすいよう定数化。
REPORT_HINT = '開発者'


def _log_tail(max_lines=80):
    """ログファイルの末尾 max_lines 行を返す。無ければ ''。"""
    try:
        if not os.path.isfile(fusion_utils.LOG_FILE):
            return ''
        with open(fusion_utils.LOG_FILE, encoding='utf-8') as f:
            lines = f.readlines()
        return ''.join(lines[-max_lines:])
    except OSError:
        return ''


def _environment_lines(command_name):
    """環境情報の各行。1行が失敗しても他は出せるよう個別に try で囲む。"""
    lines = []

    try:
        lines.append('アドイン version: {}'.format(update_check.local_version()))
    except Exception:
        lines.append('アドイン version: (取得失敗)')

    try:
        lines.append('Fusion: {}'.format(fusion_utils.app().version))
    except Exception:
        lines.append('Fusion: (取得失敗)')

    try:
        lines.append('アドイン実体: {}'.format(fusion_utils.ADDIN_DIR))
    except Exception:
        lines.append('アドイン実体: (取得失敗)')

    try:
        lines.append('アクティブドキュメント名: {}'.format(fusion_utils.app().activeDocument.name))
    except Exception:
        lines.append('アクティブドキュメント名: (不明)')

    try:
        lines.append('アクティブワークスペース: {}'.format(fusion_utils.ui().activeWorkspace.id))
    except Exception:
        lines.append('アクティブワークスペース: (取得失敗)')

    try:
        lines.append('個人設定: {}'.format(
            'あり' if os.path.isfile(fusion_utils.LOCAL_CONFIG_PATH) else 'なし'))
    except Exception:
        lines.append('個人設定: (取得失敗)')

    try:
        lines.append('コマンド: {}'.format(command_name))
    except Exception:
        lines.append('コマンド: (取得失敗)')

    try:
        lines.append('発生日時: {:%Y-%m-%d %H:%M:%S}'.format(datetime.datetime.now()))
    except Exception:
        lines.append('発生日時: (取得失敗)')

    return lines


def build_report(command_name, symptom=None, exc_text=None):
    """報告テキストを組み立てて返す。"""
    parts = ['Fusion CAM Assistant 不具合レポート']
    parts.extend(_environment_lines(command_name))

    if symptom:
        parts.append('')
        parts.append('--- 症状（利用者記入）---')
        parts.append(symptom)

    if exc_text:
        parts.append('')
        parts.append('--- traceback ---')
        parts.append(exc_text)

    parts.append('')
    parts.append('--- 直近のログ ---')
    parts.append(_log_tail())

    return '\n'.join(parts)


def write_report(text):
    """報告テキストを %TEMP% に書き、絶対パスを返す。
    メモ帳で日本語が化けないよう utf-8-sig（BOM 付き）で書く。"""
    name = 'fusioncam_report_{:%Y%m%d_%H%M%S}.txt'.format(datetime.datetime.now())
    path = os.path.join(tempfile.gettempdir(), name)
    with open(path, 'w', encoding='utf-8-sig') as f:
        f.write(text)
    return path


def open_report(path):
    """レポートをメモ帳（既定のテキストアプリ）で開く。失敗はログに残して握る。"""
    try:
        os.startfile(path)
    except Exception:
        fusion_utils.log('レポートを開けませんでした: ' + path)


def show_error_report(command_name):
    """例外の except 節から呼ぶ。環境＋traceback＋ログの報告を作って開く。
    ❗ この関数自体は絶対に例外を投げない（最後の手段の messageBox まで try で囲む）。"""
    try:
        exc_text = traceback.format_exc()
        text = build_report(command_name, exc_text=exc_text)
        path = write_report(text)
        open_report(path)
        fusion_utils.log('不具合レポート: ' + path)
        fusion_utils.ui().messageBox(
            'エラーが発生しました。不具合レポートを作成しました。\n'
            '開いたテキストの内容を' + REPORT_HINT + 'に送って報告してください。\n'
            '可能なら対象の f3d も共有してください。\n\n' + path,
            'Fusion CAM Assistant')
    except Exception:
        try:
            fusion_utils.ui().messageBox('エラー:\n' + traceback.format_exc())
        except Exception:
            pass
