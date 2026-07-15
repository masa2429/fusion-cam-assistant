# Fusion 360 スクリプト：CAM セットアップ・オペレーションの全パラメータをダンプする
#
# 使い方:
#   1. UI で手動作成した切削データ（セットアップ＋テンプレ由来の操作）入りのドキュメントを開く
#   2. Fusion の「ユーティリティ → アドイン → スクリプト」でこのファイルを追加して実行
#   3. 保存先を聞かれるのでテキストファイルを指定（例: tools/output/dump.txt）
#
# 目的（docs/api-notes.md の「実機確認 TODO」を潰す）:
#   - contour2d のジオメトリパラメータ名
#   - 固定ボックスストックの寸法パラメータ名
#   - wcs_origin_boxPoint の文字列値
#   - selectSameDiameter の有無

import traceback

import adsk.cam
import adsk.core
import adsk.fusion


def _dump_parameters(lines, indent, parameters):
    for i in range(parameters.count):
        p = parameters.item(i)
        try:
            expression = p.expression
        except Exception:
            expression = '<式なし>'
        try:
            value_obj = p.value
            value_type = value_obj.objectType.split('::')[-1] if value_obj else '<None>'
            try:
                raw_value = value_obj.value
            except Exception:
                raw_value = '<value属性なし>'
        except Exception:
            value_type = '<取得失敗>'
            raw_value = ''
        lines.append(f'{indent}{p.name} = {expression!r}  [{value_type}] raw={raw_value!r}')


def run(context):
    ui = None
    try:
        app = adsk.core.Application.get()
        ui = app.userInterface

        cam_product = app.activeDocument.products.itemByProductType('CAMProductType')
        if not cam_product:
            ui.messageBox('このドキュメントに製造（CAM）データがありません。')
            return
        cam = adsk.cam.CAM.cast(cam_product)

        lines = []
        lines.append(f'Fusion version: {app.version}')
        lines.append(f'Document: {app.activeDocument.name}')
        lines.append('=' * 80)

        for si in range(cam.setups.count):
            setup = cam.setups.item(si)
            lines.append(f'\n[Setup {si}] name={setup.name!r} stockMode={setup.stockMode}')
            lines.append('-' * 80)
            _dump_parameters(lines, '  ', setup.parameters)

            for oi in range(setup.allOperations.count):
                op = setup.allOperations.item(oi)
                strategy = op.strategy if hasattr(op, 'strategy') else '<不明>'
                tool_desc = ''
                try:
                    tool = op.tool
                    if tool:
                        dia = tool.parameters.itemByName('tool_diameter')
                        tool_desc = f' tool_diameter={dia.expression if dia else "<不明>"}'
                except Exception:
                    tool_desc = ' tool=<取得失敗>'
                lines.append(f'\n  [Operation {oi}] name={op.name!r} strategy={strategy!r}'
                             f' isValid={op.hasToolpath}{tool_desc}')
                lines.append('  ' + '-' * 76)
                _dump_parameters(lines, '    ', op.parameters)

        dialog = ui.createFileDialog()
        dialog.title = 'ダンプの保存先'
        dialog.filter = 'テキスト (*.txt)'
        dialog.initialFilename = 'dump.txt'
        if dialog.showSave() != adsk.core.DialogResults.DialogOK:
            return
        with open(dialog.filename, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
        ui.messageBox(f'ダンプ完了:\n{dialog.filename}\n\n'
                      f'結果を docs/api-notes.md の「実機確認 TODO」に反映してください。')
    except Exception:
        if ui:
            ui.messageBox('失敗:\n{}'.format(traceback.format_exc()))
