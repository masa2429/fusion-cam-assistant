# Fusion CAM Assistant エントリポイント
# 製造ワークスペースに「CAM アシスタント」パネルを作り、各コマンドを登録する。

import traceback

import adsk.core

from .commands import about, auto_cam, layout_check, post_all, settings
from .lib import fusion_utils, update_check

WORKSPACE_ID = 'CAMEnvironment'  # 製造ワークスペース
PANEL_ID = 'FusionCamPanel'
PANEL_NAME = 'CAM アシスタント'

COMMAND_MODULES = [auto_cam, post_all, layout_check, settings, about]


def run(context):
    ui = None
    try:
        ui = fusion_utils.ui()
        workspace = ui.workspaces.itemById(WORKSPACE_ID)
        if not workspace:
            ui.messageBox('製造ワークスペースが見つかりません。')
            return
        # 表示名の変更を確実に反映するため、残っていたパネル（旧名時代の物も）は作り直す
        for panel_id in (PANEL_ID, 'QuhpCamPanel'):
            leftover = workspace.toolbarPanels.itemById(panel_id)
            if leftover:
                leftover.deleteMe()
        panel = workspace.toolbarPanels.add(PANEL_ID, PANEL_NAME)
        for module in COMMAND_MODULES:
            module.start(panel)
        fusion_utils.log('アドイン起動完了')
        try:
            update_check.notify_if_updated()
        except Exception:
            pass  # 更新チェックは起動を妨げない
    except Exception:
        if ui:
            ui.messageBox('Fusion CAM Assistant の起動に失敗:\n{}'.format(traceback.format_exc()))


def stop(context):
    ui = None
    try:
        ui = fusion_utils.ui()
        for module in COMMAND_MODULES:
            try:
                module.stop()
            except Exception:
                pass
        workspace = ui.workspaces.itemById(WORKSPACE_ID)
        if workspace:
            panel = workspace.toolbarPanels.itemById(PANEL_ID)
            if panel:
                panel.deleteMe()
        fusion_utils.clear_handlers()
    except Exception:
        if ui:
            ui.messageBox('Fusion CAM Assistant の停止に失敗:\n{}'.format(traceback.format_exc()))
