# NC一括出力：セットアップ内の操作を順序を保ったまま「同一工具の連続区間」で
# グルーピングし、1_flat3.0 / 2_flat1.5 … の名前で NCProgram を作成してポストする。
#
# 順序を保った連続区間で区切るのは、切削順の不変条件（外郭が最後）を
# ファイル分割で壊さないため。ファイル名がそのまま実行順になる。
#
# API は NCProgram 系のみ使用（CAM.postProcess は廃止済み・使用禁止。docs/api-notes.md 参照）。

import os
import traceback

import adsk.cam
import adsk.core

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


def _tool_diameter_mm(operation):
    try:
        parameter = operation.tool.parameters.itemByName('tool_diameter')
        return round(fusion_utils.cm_to_mm(parameter.value.value), 2)
    except Exception:
        return None


def _group_by_tool(operations):
    """順序を保ったまま、工具径が変わるところでグループを切る。"""
    groups = []
    current = []
    current_dia = None
    for operation in operations:
        diameter = _tool_diameter_mm(operation)
        if current and diameter != current_dia:
            groups.append((current_dia, current))
            current = []
        current.append(operation)
        current_dia = diameter
    if current:
        groups.append((current_dia, current))
    return groups


def _resolve_post_configuration(config, ui):
    """config.json の post_processor（.cps）をポストライブラリから解決する。"""
    path = config.get('post_processor', '')
    if not path or not os.path.isfile(path):
        dialog = ui.createFileDialog()
        dialog.title = 'ポストプロセッサ（originalmind.cps）を選択'
        dialog.filter = 'ポストプロセッサ (*.cps)'
        if dialog.showOpen() != adsk.core.DialogResults.DialogOK:
            return None
        path = dialog.filename
    post_library = adsk.cam.CAMManager.get().libraryManager.postLibrary
    # ローカル .cps の URL スキームは環境差があるため候補を順に試す（実機確認対象）
    for url_text in ('file:///' + path.replace('\\', '/'),
                     path,
                     'file://' + path.replace('\\', '/')):
        try:
            return post_library.postConfigurationAtURL(adsk.core.URL.create(url_text))
        except Exception:
            continue
    ui.messageBox('ポストプロセッサを読み込めませんでした。\n'
                  'Fusion のポストライブラリ（ローカル）に originalmind.cps を登録してから、'
                  '再度お試しください。\n対象: ' + path)
    return None


def _on_created(args):
    ui = fusion_utils.ui()
    try:
        cam = fusion_utils.active_cam()
        if not cam or cam.setups.count == 0:
            ui.messageBox('セットアップがありません。先に切削データを作成してください。')
            return

        operations = []
        for si in range(cam.setups.count):
            setup = cam.setups.item(si)
            for oi in range(setup.allOperations.count):
                operation = setup.allOperations.item(oi)
                if operation.isSuppressed:
                    continue
                operations.append(operation)
        if not operations:
            ui.messageBox('操作がありません。')
            return

        without_toolpath = [op.name for op in operations if not op.hasToolpath]
        if without_toolpath:
            ui.messageBox('ツールパス未生成の操作があります。先に生成してください:\n  '
                          + '\n  '.join(without_toolpath))
            return

        config = fusion_utils.load_config()
        post_configuration = _resolve_post_configuration(config, ui)
        if post_configuration is None:
            return

        folder_dialog = ui.createFolderDialog()
        folder_dialog.title = 'NC ファイルの出力先フォルダ（USB メモリ等）'
        if folder_dialog.showDialog() != adsk.core.DialogResults.DialogOK:
            return
        output_folder = folder_dialog.folder

        groups = _group_by_tool(operations)
        created_names = []
        for index, (diameter, group_operations) in enumerate(groups, start=1):
            dia_text = f'{diameter:g}' if diameter is not None else 'unknown'
            name = f'{index}_flat{dia_text}'
            nc_input = cam.ncPrograms.createInput()
            nc_input.displayName = name
            nc_input.operations = group_operations
            _set_nc_parameter(nc_input, 'nc_program_filename', name)
            _set_nc_parameter(nc_input, 'nc_program_output_folder', output_folder)
            _set_nc_parameter(nc_input, 'nc_program_openInEditor', False)
            nc_program = cam.ncPrograms.add(nc_input)
            nc_program.postConfiguration = post_configuration
            options = adsk.cam.NCProgramPostProcessOptions.create()
            nc_program.postProcess(options)
            created_names.append(f'{name}（{len(group_operations)} 操作）')

        ui.messageBox('ポスト完了:\n  ' + '\n  '.join(created_names)
                      + f'\n\n出力先: {output_folder}'
                      + '\n\nファイル名の番号順（= 切削順）に実行してください。')
    except Exception:
        ui.messageBox('NC出力に失敗:\n{}'.format(traceback.format_exc()))


def _set_nc_parameter(nc_input, name, value):
    try:
        nc_input.parameters.itemByName(name).value.value = value
    except Exception:
        fusion_utils.log(f'NCProgram パラメータ設定失敗: {name}')
