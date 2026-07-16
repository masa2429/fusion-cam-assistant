# 診断コマンド：バージョン・更新有無・テンプレ/工具・ポスト解決状態などを表示する。
# メンバーから「動かない」と相談されたときの切り分けに使う。

import os
import traceback

import adsk.cam
import adsk.core

from ..lib import fusion_utils, template_registry, update_check

COMMAND_ID = 'fcaAbout'
_panel = None


def start(panel):
    global _panel
    _panel = panel
    fusion_utils.add_command(
        panel, COMMAND_ID, 'バージョン情報',
        'バージョン・更新の有無・テンプレート/工具・ポストの状態を表示します（不具合報告用）。',
        _on_created)


def stop():
    fusion_utils.remove_command(_panel, COMMAND_ID)


def _post_status(config):
    try:
        hint = str(config.get('post_name_hint', 'originalmind')).lower()
        post_library = adsk.cam.CAMManager.get().libraryManager.postLibrary
        query = post_library.createQuery(adsk.cam.LibraryLocations.LocalLibraryLocation)
        for post in query.execute():
            text = ' '.join(str(getattr(post, attr, '') or '')
                            for attr in ('description', 'vendor')).lower()
            if hint in text:
                return f'✓ 解決可（{hint}）'
        return f'✗ ポストライブラリ（ローカル）に {hint} が見つかりません'
    except Exception:
        return '？ 確認失敗（製造ワークスペースで再実行してください）'


def _on_created(args):
    ui = fusion_utils.ui()
    try:
        lines = [f'Fusion CAM Assistant v{update_check.local_version()}']
        remote = update_check.newer_remote_version()
        if remote:
            lines.append(f'⚠ 新バージョン v{remote} があります（README の手順で更新）')
        else:
            lines.append('更新: 確認できないか、最新です')
        lines.append(f'Fusion: {fusion_utils.app().version}')
        lines.append(f'アドイン実体: {fusion_utils.ADDIN_DIR}')
        lines.append(f'個人設定: {"あり" if os.path.isfile(fusion_utils.LOCAL_CONFIG_PATH) else "なし"}'
                     f'（{fusion_utils.LOCAL_CONFIG_PATH}）')

        try:
            config = fusion_utils.load_config()
            registry = template_registry.Registry(config['template_dir'], config)
            standard = sum(1 for t in registry.templates
                           if t.set_name == template_registry.SET_STANDARD)
            dlc = sum(1 for t in registry.templates
                      if t.set_name == template_registry.SET_DLC)
            lines.append(f'テンプレート: 標準 {standard} 件 / DLC {dlc} 件'
                         f'（{config["template_dir"]}）')
            if registry.owned_tools is None:
                lines.append('所有工具: 未設定（全工具を使用）')
            else:
                lines.append(f'所有工具: {len(registry.owned_tools)} 件'
                             f' / 優先セット: {registry.preferred_set}')
            lines.append(f'ポスト: {_post_status(config)}')
        except Exception as error:
            lines.append(f'✗ テンプレート読込エラー: {error}')

        lines.append('')
        lines.append(f'リポジトリ: {update_check.REPO_URL}')
        ui.messageBox('\n'.join(lines), 'Fusion CAM Assistant 診断')
    except Exception:
        ui.messageBox('診断に失敗:\n{}'.format(traceback.format_exc()))
