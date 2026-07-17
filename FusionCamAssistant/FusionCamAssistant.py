# Fusion CAM Assistant エントリポイント
# 製造ワークスペースに「CAM アシスタント」パネルを作り、各コマンドを登録する。

import traceback

import adsk.core

from .commands import about, auto_cam, auto_place, layout_check, post_all, settings
from .lib import fusion_utils, update_check

WORKSPACE_ID = 'CAMEnvironment'  # 製造ワークスペース
PANEL_ID = 'FusionCamPanel'
PANEL_NAME = 'CAM アシスタント'
# 配置作業はデザイン側で行うため、配置系コマンドはデザインワークスペースにも出す
DESIGN_WORKSPACE_ID = 'FusionSolidEnvironment'
DESIGN_PANEL_ID = 'FusionCamDesignPanel'
DESIGN_COMMAND_IDS = (auto_place.COMMAND_ID, layout_check.COMMAND_ID)

COMMAND_MODULES = [auto_cam, post_all, auto_place, layout_check, settings, about]


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

        # デザインワークスペース側のパネル（配置系コマンドの複製ボタン）
        design_workspace = ui.workspaces.itemById(DESIGN_WORKSPACE_ID)
        if design_workspace:
            leftover = design_workspace.toolbarPanels.itemById(DESIGN_PANEL_ID)
            if leftover:
                leftover.deleteMe()
            design_panel = design_workspace.toolbarPanels.add(DESIGN_PANEL_ID, PANEL_NAME)
            for command_id in DESIGN_COMMAND_IDS:
                definition = ui.commandDefinitions.itemById(command_id)
                if definition:
                    fusion_utils.add_control(design_panel, definition)
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
        # デザイン側パネルを先に消す（コマンド定義の削除より前にボタンを片付ける）
        design_workspace = ui.workspaces.itemById(DESIGN_WORKSPACE_ID)
        if design_workspace:
            design_panel = design_workspace.toolbarPanels.itemById(DESIGN_PANEL_ID)
            if design_panel:
                design_panel.deleteMe()
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
