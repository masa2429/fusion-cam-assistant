# パーツ配置支援コマンド：280×280 枠・8mm 間隔・高さ揃えのチェック
# （Step 5 で実装）

from ..lib import fusion_utils

COMMAND_ID = 'quhpLayoutCheck'
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


def _on_created(args):
    fusion_utils.ui().messageBox('未実装（Step 5 で実装予定）')
