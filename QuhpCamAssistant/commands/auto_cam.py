# メインコマンド：ボディ解析 → 確認ダイアログ → セットアップ＋操作の一括生成
# （Step 2-3 で実装）

from ..lib import fusion_utils

COMMAND_ID = 'quhpAutoCam'
_panel = None


def start(panel):
    global _panel
    _panel = panel
    fusion_utils.add_command(
        panel, COMMAND_ID, '切削データ自動作成',
        'ボディを解析して外郭/内郭/穴/ポケットを自動分類し、テンプレートから切削データを一括生成します。',
        _on_created)


def stop():
    fusion_utils.remove_command(_panel, COMMAND_ID)


def _on_created(args):
    fusion_utils.ui().messageBox('未実装（Step 2-3 で実装予定）')
