# 設定コマンド：所有している工具のチェックリストと優先テンプレセットを
# config.local.json に保存する（ZIP 更新で消えない個人設定）。
#
# 所有していない工具のテンプレは自動割当・ダイアログの選択肢から除外される。

import traceback

import adsk.core

from ..lib import fusion_utils, template_registry

COMMAND_ID = 'quhpSettings'
_panel = None
_state = {}  # {'tool_keys': [key, ...]}

_TOOL_TYPE_LABELS = {
    'flat end mill': 'フラットエンドミル',
    'ball end mill': 'ボールエンドミル',
    'chamfer mill': '面取りミル',
    'spot drill': 'スポットドリル',
    'drill': 'ドリル',
}


def start(panel):
    global _panel
    _panel = panel
    fusion_utils.add_command(
        panel, COMMAND_ID, '設定',
        '所有している工具と優先テンプレセットを設定します（config.local.json に保存）。',
        _on_created)


def stop():
    fusion_utils.remove_command(_panel, COMMAND_ID)
    _state.clear()


def _tool_label(key):
    set_name, diameter, tool_type = key.split(':', 2)
    type_label = _TOOL_TYPE_LABELS.get(tool_type, tool_type)
    return f'{diameter} {type_label}（{set_name}セット）'


def _on_created(args):
    ui = fusion_utils.ui()
    command = args.command
    try:
        config = fusion_utils.load_config()
        registry = template_registry.Registry(config['template_dir'], config)
    except Exception:
        ui.messageBox('設定の読み込みに失敗:\n{}'.format(traceback.format_exc()))
        return

    inputs = command.commandInputs
    header = inputs.addTextBoxCommandInput(
        'quhpSettingsHeader', '',
        '所有している工具にチェックを入れてください。\n'
        'チェックの無い工具のテンプレートは自動割当・選択肢から除外されます。',
        3, True)
    header.isFullWidth = True

    dropdown = inputs.addDropDownCommandInput(
        'quhpPreferredSet', '優先テンプレセット',
        adsk.core.DropDownStyles.TextListDropDownStyle)
    for set_name in (template_registry.SET_STANDARD, template_registry.SET_DLC):
        dropdown.listItems.add(set_name, set_name == registry.preferred_set)

    tool_keys = registry.all_tool_keys()
    _state['tool_keys'] = tool_keys
    for index, key in enumerate(tool_keys):
        owned = registry.owned_tools is None or key in registry.owned_tools
        inputs.addBoolValueInput(f'quhpTool{index}', _tool_label(key), True, '', owned)

    command.okButtonText = '保存'
    command.execute.add(fusion_utils.keep(_ExecuteHandler()))


class _ExecuteHandler(adsk.core.CommandEventHandler):
    def notify(self, args):
        ui = fusion_utils.ui()
        try:
            inputs = args.command.commandInputs
            owned = []
            for index, key in enumerate(_state.get('tool_keys', [])):
                checkbox = inputs.itemById(f'quhpTool{index}')
                if checkbox is not None and checkbox.value:
                    owned.append(key)
            dropdown = inputs.itemById('quhpPreferredSet')
            preferred = dropdown.selectedItem.name if dropdown and dropdown.selectedItem \
                else template_registry.SET_STANDARD
            fusion_utils.save_local_config({
                'owned_tools': owned,
                'preferred_set': preferred,
            })
            fusion_utils.log(f'設定を保存: 優先={preferred} 所有工具={len(owned)}件')
        except Exception:
            ui.messageBox('設定の保存に失敗:\n{}'.format(traceback.format_exc()))
