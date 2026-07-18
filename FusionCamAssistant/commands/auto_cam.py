# メインコマンド：ボディ解析 → 確認ダイアログ → セットアップ＋操作の一括生成
#
# ダイアログは分類結果を1行=1操作で一覧表示し、チェックボックスで適用の有無、
# ドロップダウンで同種別の別テンプレート（工具径違い）への変更ができる。

import adsk.cam
import adsk.core

from ..lib import cam_builder, classifier, fusion_utils, report, template_registry

COMMAND_ID = 'fcaAutoCam'
_panel = None
_state = {}  # {'result': ClassifyResult, 'registry': Registry, 'config': dict}


def start(panel):
    global _panel
    _panel = panel
    fusion_utils.add_command(
        panel, COMMAND_ID, '切削データ自動作成',
        'ボディを解析して外郭/内郭/穴/ポケットを自動分類し、テンプレートから切削データを一括生成します。',
        _on_created)


def stop():
    fusion_utils.remove_command(_panel, COMMAND_ID)
    _state.clear()


def _on_created(args):
    ui = fusion_utils.ui()
    command = args.command

    design = fusion_utils.active_design()
    cam = fusion_utils.active_cam()
    if not design or not cam:
        ui.messageBox('デザインと製造（CAM）データのあるドキュメントで実行してください。')
        return

    config = fusion_utils.load_config()
    registry = template_registry.Registry(config['template_dir'], config)
    result = classifier.classify(design, registry, config)
    if not result.items:
        ui.messageBox('加工候補が見つかりませんでした。\n' + '\n'.join(result.warnings))
        return

    _state['result'] = result
    _state['registry'] = registry
    _state['config'] = config

    inputs = command.commandInputs
    header = (f'対象ボディ: {len(result.bodies)} 個　板厚: {result.thickness_mm:.1f} mm\n'
              '内容を確認し、不要な行はチェックを外してください。外郭は必ず最後に加工されます。')
    if result.warnings:
        header += '\n⚠ ' + '\n⚠ '.join(result.warnings)
    header_rows = 4 + 2 * len(result.warnings)
    header_input = inputs.addTextBoxCommandInput('fcaHeader', '', header, header_rows, True)
    header_input.isFullWidth = True

    table = inputs.addTableCommandInput('fcaTable', '加工一覧', 3, '1:5:4')
    table.isFullWidth = True
    table.maximumVisibleRows = 12
    for index, item in enumerate(result.items):
        checkbox = inputs.addBoolValueInput(f'fcaChk{index}', '適用', True, '', item.enabled)
        checkbox.isEnabled = item.template is not None
        label_text = item.label + (f'\n{item.note}' if item.note else '')
        label = inputs.addTextBoxCommandInput(f'fcaLbl{index}', '', label_text,
                                              2 if item.note else 1, True)
        row = table.rowCount
        table.addCommandInput(checkbox, row, 0)
        table.addCommandInput(label, row, 1)
        if item.template is not None:
            dropdown = inputs.addDropDownCommandInput(
                f'fcaTpl{index}', 'テンプレート',
                adsk.core.DropDownStyles.TextListDropDownStyle)
            options = list(_state['registry'].find(item.kind))
            if item.kind == template_registry.KIND_NAIKAKU:
                # 島が残る開口向けに「くり抜き（ポケット/負荷制御）」も選べるようにする
                options += _state['registry'].find(template_registry.KIND_POCKET)
            if item.template not in options:
                options.insert(0, item.template)
            for template in options:
                dropdown.listItems.add(template.name, template.name == item.template.name)
            table.addCommandInput(dropdown, row, 2)

    on_execute = _ExecuteHandler()
    command.execute.add(fusion_utils.keep(on_execute))
    command.okButtonText = '生成'


class _ExecuteHandler(adsk.core.CommandEventHandler):
    def notify(self, args):
        ui = fusion_utils.ui()
        try:
            inputs = args.command.commandInputs
            result = _state['result']
            registry = _state['registry']

            for index, item in enumerate(result.items):
                checkbox = inputs.itemById(f'fcaChk{index}')
                if checkbox is not None:
                    item.enabled = checkbox.value and checkbox.isEnabled
                dropdown = inputs.itemById(f'fcaTpl{index}')
                if dropdown is not None and dropdown.selectedItem is not None:
                    selected = registry.by_name(dropdown.selectedItem.name)
                    if selected is not None:
                        item.template = selected

            cam = fusion_utils.active_cam()
            cam_report = cam_builder.build(cam, result, result.items, _state['config'])
            ui.messageBox(cam_report.summary(), 'Fusion CAM Assistant')
        except Exception:
            report.show_error_report('自動CAM生成')
