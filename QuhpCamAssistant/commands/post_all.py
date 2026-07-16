# NC一括出力：セットアップ内の操作を順序を保ったまま「同一工具の連続区間」で
# グルーピングし、1_flat3.0 / 2_flat1.5 … の名前で NCProgram を作成してポストする。
#
# 順序を保った連続区間で区切るのは、切削順の不変条件（外郭が最後）を
# ファイル分割で壊さないため。ファイル名がそのまま実行順になる。
#
# API は NCProgram 系のみ使用（CAM.postProcess は廃止済み・使用禁止。docs/api-notes.md 参照）。

import re
import traceback

import adsk.cam
import adsk.core

from ..lib import cam_builder, fusion_utils

_NC_NAME_RE = re.compile(r'^\d+_flat')

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


def _post_text(post):
    """PostConfiguration の識別に使えるテキストを寄せ集める（属性は環境差に備え防御的に）。"""
    text = []
    for attr in ('description', 'vendor', 'name'):
        try:
            value = getattr(post, attr, None)
            if value:
                text.append(str(value))
        except Exception:
            pass
    return ' '.join(text).lower()


def _resolve_post_configuration(config, ui):
    """ポストライブラリ（ローカル）から名前ヒントで検索して解決する。
    ファイルパス直接読み（postConfigurationAtURL + file URL）は実機で失敗したため
    ライブラリ検索を第一手段にする（2026-07-15 実機確認）。"""
    hint = str(config.get('post_name_hint', 'originalmind')).lower()
    post_library = adsk.cam.CAMManager.get().libraryManager.postLibrary
    found = []
    all_posts = []
    try:
        query = post_library.createQuery(adsk.cam.LibraryLocations.LocalLibraryLocation)
        for post in query.execute():
            text = _post_text(post)
            all_posts.append(text)
            if hint in text:
                found.append(post)
    except Exception:
        fusion_utils.log('ポストライブラリの検索に失敗:\n' + traceback.format_exc())
    if found:
        if len(found) > 1:
            fusion_utils.log(f'ヒント {hint!r} に複数のポストが一致。先頭を使用します。')
        return found[0]
    ui.messageBox(
        f'ポストライブラリ（ローカル）に「{hint}」に一致するポストが見つかりませんでした。\n'
        'config.json の post_name_hint を確認してください。\n\n'
        '見つかったポスト:\n  ' + ('\n  '.join(all_posts) if all_posts else '(なし)'))
    return None


def _on_created(args):
    ui = fusion_utils.ui()
    try:
        cam = fusion_utils.active_cam()
        if not cam or cam.setups.count == 0:
            ui.messageBox('セットアップがありません。先に切削データを作成してください。')
            return

        # 自動CAMセットアップがあればそれだけを対象、無ければ全セットアップを対象にする
        all_setups = [cam.setups.item(i) for i in range(cam.setups.count)]
        target_setups = [s for s in all_setups
                         if s.name.startswith(cam_builder.SETUP_NAME_PREFIXES)]
        if not target_setups:
            target_setups = all_setups
        operations = []
        for setup in target_setups:
            for oi in range(setup.allOperations.count):
                operation = setup.allOperations.item(oi)
                if operation.isSuppressed:
                    continue
                operations.append(operation)
        if not operations:
            ui.messageBox('操作がありません。')
            return

        # ツールパスの無い操作（未生成・空の取り残し等）は自動でスキップして続行する
        skipped_names = [op.name for op in operations if not op.hasToolpath]
        operations = [op for op in operations if op.hasToolpath]
        if not operations:
            ui.messageBox('有効なツールパスを持つ操作がありません。先に生成してください。')
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

        # 再実行で NC プログラムが増殖しないよう、以前作った n_flat 系を置き換える
        for i in reversed(range(cam.ncPrograms.count)):
            nc_program = cam.ncPrograms.item(i)
            try:
                label = getattr(nc_program, 'name', None) or \
                    getattr(nc_program, 'displayName', None) or ''
                if _NC_NAME_RE.match(label):
                    nc_program.deleteMe()
            except Exception:
                pass

        groups = _group_by_tool(operations)
        created_names = []
        failed_names = []
        for index, (diameter, group_operations) in enumerate(groups, start=1):
            dia_text = f'{diameter:g}' if diameter is not None else 'unknown'
            name = f'{index}_flat{dia_text}'
            try:
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
            except Exception:
                failed_names.append(name)
                fusion_utils.log(f'NCポスト失敗 {name}:\n{traceback.format_exc()}')

        message = 'ポスト完了:\n  ' + '\n  '.join(created_names) \
                  + f'\n\n出力先: {output_folder}' \
                  + '\n\nファイル名の番号順（= 切削順）に実行してください。'
        if skipped_names:
            message += '\n\nツールパスが無いためスキップした操作:\n  ' + '\n  '.join(skipped_names)
        if failed_names:
            message += '\n\n⚠ ポストに失敗（ログ参照）:\n  ' + '\n  '.join(failed_names)
        ui.messageBox(message)
    except Exception:
        ui.messageBox('NC出力に失敗:\n{}'.format(traceback.format_exc()))


def _set_nc_parameter(nc_input, name, value):
    try:
        nc_input.parameters.itemByName(name).value.value = value
    except Exception:
        fusion_utils.log(f'NCProgram パラメータ設定失敗: {name}')
