# GitHub 上の最新バージョンを確認する（リポジトリ公開後に機能する。
# 非公開・オフライン・タイムアウト時は無音で None を返し、起動を妨げない）。

import json
import os
import urllib.request

from . import fusion_utils

REPO_URL = 'https://github.com/masa2429/quhp-cam-assistant'
_RAW_MANIFEST_URL = ('https://raw.githubusercontent.com/masa2429/'
                     'quhp-cam-assistant/main/QuhpCamAssistant/QuhpCamAssistant.manifest')


def local_version():
    try:
        manifest_path = os.path.join(fusion_utils.ADDIN_DIR, 'QuhpCamAssistant.manifest')
        with open(manifest_path, encoding='utf-8') as f:
            return json.load(f).get('version', '0.0.0')
    except Exception:
        return '0.0.0'


def _version_tuple(text):
    parts = []
    for token in str(text).split('.'):
        digits = ''.join(c for c in token if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def newer_remote_version(timeout_seconds=3):
    """リモートの方が新しければそのバージョン文字列、そうでなければ None。"""
    try:
        with urllib.request.urlopen(_RAW_MANIFEST_URL, timeout=timeout_seconds) as response:
            remote = json.loads(response.read().decode('utf-8')).get('version', '0.0.0')
        if _version_tuple(remote) > _version_tuple(local_version()):
            return remote
        return None
    except Exception:
        return None  # 非公開リポ・オフライン等。起動を妨げない


def notify_if_updated():
    """新バージョンがあれば1回だけ通知する（通知済みバージョンは config.local に記録）。"""
    remote = newer_remote_version()
    if remote is None:
        return
    config = fusion_utils.load_config()
    if config.get('update_notified_version') == remote:
        return
    fusion_utils.save_local_config({'update_notified_version': remote})
    fusion_utils.ui().messageBox(
        f'QUHP CAM Assistant の新バージョン v{remote} があります'
        f'（現在 v{local_version()}）。\n\n'
        'README の「ZIP でアップデート」の手順で更新してください。\n'
        f'{REPO_URL}',
        'QUHP CAM Assistant')
