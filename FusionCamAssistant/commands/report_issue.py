# 手動「問題を報告」コマンド：症状を記入してもらい、環境情報付きの報告テキストを
# 生成してメモ帳で開く。処理は通ったが結果がおかしい（誤分類・加工サイド反転等）
# ときにメンバーが任意のタイミングで押す想定。

import adsk.core

from ..lib import fusion_utils, report

COMMAND_ID = 'fcaReport'
_panel = None


def start(panel):
    global _panel
    _panel = panel
    fusion_utils.add_command(
        panel, COMMAND_ID, '問題を報告',
        '不具合を' + report.REPORT_HINT + 'へ報告するためのテキストを生成します'
        '（結果がおかしいときも使えます）。',
        _on_created)


def stop():
    fusion_utils.remove_command(_panel, COMMAND_ID)


def _on_created(args):
    command = args.command
    inputs = command.commandInputs

    inputs.addTextBoxCommandInput(
        'fcaReportSymptom', '何がおかしいか', '', 6, False)

    note = inputs.addTextBoxCommandInput(
        'fcaReportNote', '',
        '報告前に、対象の f3d を保存してください。'
        '可能なら報告と一緒に f3d も共有してください。',
        2, True)
    note.isFullWidth = True

    command.okButtonText = '報告テキストを作成'
    command.execute.add(fusion_utils.keep(_ExecuteHandler()))


class _ExecuteHandler(adsk.core.CommandEventHandler):
    def notify(self, args):
        ui = fusion_utils.ui()
        try:
            inputs = args.command.commandInputs
            symptom_input = inputs.itemById('fcaReportSymptom')
            symptom = symptom_input.text if symptom_input is not None else ''

            text = report.build_report('（手動報告）', symptom=symptom)
            path = report.write_report(text)
            report.open_report(path)
            fusion_utils.log('不具合レポート（手動）: ' + path)
            ui.messageBox(
                '報告テキストを作成しました。\n'
                'この内容を' + report.REPORT_HINT + 'に貼り付けてください。\n'
                '可能なら対象 f3d も共有してください。\n\n' + path,
                'Fusion CAM Assistant')
        except Exception:
            report.show_error_report('問題を報告')
