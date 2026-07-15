# ポスト処理コマンド：工具ごとにグルーピングし n_flatX.X 命名で NC 一括出力
# （Step 4 で実装）

from ..lib import fusion_utils

COMMAND_ID = 'quhpPostAll'
_panel = None


def start(panel):
    global _panel
    _panel = panel
    fusion_utils.add_command(
        panel, COMMAND_ID, 'NC一括出力',
        '工具ごとに操作をまとめ、切削順の名前（1_flat3.0 等）で NC ファイルを一括ポストします。',
        _on_created)


def stop():
    fusion_utils.remove_command(_panel, COMMAND_ID)


def _on_created(args):
    fusion_utils.ui().messageBox('未実装（Step 4 で実装予定）')
