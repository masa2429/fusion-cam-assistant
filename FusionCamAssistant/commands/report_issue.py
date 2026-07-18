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

    can_submit = report.can_submit()
    can_upload = report.can_upload()

    inputs.addStringValueInput('fcaReportReporter', '報告者名（任意）', '')

    inputs.addTextBoxCommandInput(
        'fcaReportSymptom', '何がおかしいか', '', 6, False)

    if can_submit:
        # 何が送られるかを押す前に把握できるようにする
        if can_upload:
            privacy_text = ('送信内容：報告者名・症状・環境情報・ログ全文・'
                            '対象デザイン（f3d を含む zip、チェック時）。'
                            '送信前にローカルにも保存されます。')
        else:
            privacy_text = ('送信内容：報告者名・症状・アドイン version・Fusion バージョン・'
                            'ドキュメント名・直近のログ。送信前にローカルにも保存されます。')
        privacy = inputs.addTextBoxCommandInput(
            'fcaReportPrivacy', '', privacy_text, 3, True)
        privacy.isFullWidth = True

    # 大きなデザインで書き出しに時間がかかる場合に外せるようにする
    inputs.addBoolValueInput(
        'fcaReportExportF3d', 'f3d も同梱する（推奨）', True, '', True)

    if can_upload:
        note_text = ('zip も自動で送信します。失敗した場合はエクスプローラーで表示するので、'
                     '手動で開発者に送ってください。')
    else:
        note_text = ('自動で送れるのはテキストのみです。レポート・ログ全文・f3d をまとめた zip を'
                     'エクスプローラーで表示するので、それを手動で添えて送ってください。')
    note = inputs.addTextBoxCommandInput('fcaReportNote', '', note_text, 2, True)
    note.isFullWidth = True

    command.okButtonText = '送信' if can_submit else '報告テキストを作成'
    command.execute.add(fusion_utils.keep(_ExecuteHandler()))


class _ExecuteHandler(adsk.core.CommandEventHandler):
    def notify(self, args):
        ui = fusion_utils.ui()
        try:
            inputs = args.command.commandInputs
            symptom_input = inputs.itemById('fcaReportSymptom')
            symptom = symptom_input.text if symptom_input is not None else ''
            reporter_input = inputs.itemById('fcaReportReporter')
            reporter = reporter_input.value if reporter_input is not None else ''
            export_input = inputs.itemById('fcaReportExportF3d')
            want_f3d = export_input.value if export_input is not None else False

            # 送信可否に関わらず、まずローカルに必ず残す
            text = report.build_report('（手動報告）', symptom=symptom)
            path = report.write_report(text)
            fusion_utils.log('不具合レポート（手動）: ' + path)

            # .txt と対になる .f3d を書き出し、本文にも場所を載せて上書き保存する
            f3d_path = report.export_f3d(path) if want_f3d else None
            if f3d_path:
                text = report.build_report('（手動報告）', symptom=symptom,
                                           f3d_path=f3d_path)
                report.rewrite_report(path, text)
                fusion_utils.log('対象 f3d: ' + f3d_path)

            # レポート本文・ログ全文・f3d を1つの zip にまとめる（送るものを1個に絞る）
            bundle_path = report.finalize_report(path, text, f3d_path)
            reveal_path = bundle_path or (f3d_path if f3d_path else None)

            if not report.can_submit():
                report.open_report(path)
                ui.messageBox(
                    '報告テキストを作成しました。\n'
                    'この内容を' + report.REPORT_HINT + 'に送ってください。\n\n' + path
                    + report.bundle_message(bundle_path),
                    'Fusion CAM Assistant')
                if reveal_path:
                    report.reveal_in_explorer(reveal_path)
                return

            # フォーム送信と zip 自動アップロードを両方試み、結果でメッセージ・reveal を出し分ける
            report.deliver_report(text, path, bundle_path, reveal_path,
                                  '（手動報告）', symptom=symptom, reporter=reporter)
        except Exception:
            report.show_error_report('問題を報告')
