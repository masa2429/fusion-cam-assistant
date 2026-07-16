# Fusion API の共通ユーティリティ：ハンドラ保持・設定読み込み・コマンド登録・単位変換

import json
import os
import tempfile
import traceback

import adsk.cam
import adsk.core

_handlers = []  # イベントハンドラはGCされないようモジュールで保持する（Fusion APIの定石）

# realpath でジャンクションを実体パスに解決する（AddIns からリンクされていても
# リポジトリ内の templates/ を ../templates で見つけられるようにするため）
ADDIN_DIR = os.path.dirname(os.path.dirname(os.path.realpath(os.path.abspath(__file__))))

MM_PER_CM = 10.0


def keep(handler):
    _handlers.append(handler)
    return handler


def clear_handlers():
    _handlers.clear()


def app():
    return adsk.core.Application.get()


def ui():
    return app().userInterface


LOG_FILE = os.path.join(tempfile.gettempdir(), 'quhpcam.log')


def log(message):
    text = '[QuhpCam] {}'.format(message)
    try:
        app().log(text)
    except Exception:
        pass
    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(text + '\n')
    except OSError:
        pass


def cm_to_mm(value_cm):
    return value_cm * MM_PER_CM


def mm_to_cm(value_mm):
    return value_mm / MM_PER_CM


LOCAL_CONFIG_PATH = os.path.join(ADDIN_DIR, 'config.local.json')


def _merge_config(base, override):
    """dict は再帰マージ、それ以外（リスト・スカラ）は置換。"""
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge_config(base[key], value)
        else:
            base[key] = value


def save_local_config(updates):
    """config.local.json に個人設定を保存する（既存内容とマージ。ZIP更新で消えない）。"""
    current = {}
    if os.path.isfile(LOCAL_CONFIG_PATH):
        try:
            with open(LOCAL_CONFIG_PATH, encoding='utf-8') as f:
                current = json.load(f)
        except (json.JSONDecodeError, OSError):
            log('config.local.json が壊れているため作り直します')
    _merge_config(current, updates)
    with open(LOCAL_CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(current, f, ensure_ascii=False, indent=4)


def load_config():
    """config.json を読み、config.local.json（個人設定）を上書きマージして返す。"""
    config_path = os.path.join(ADDIN_DIR, 'config.json')
    with open(config_path, encoding='utf-8') as f:
        config = json.load(f)
    if os.path.isfile(LOCAL_CONFIG_PATH):
        try:
            with open(LOCAL_CONFIG_PATH, encoding='utf-8') as f:
                _merge_config(config, json.load(f))
        except (json.JSONDecodeError, OSError):
            log('config.local.json の読み込みに失敗（無視して既定値を使用）')
    template_dir = config.get('template_dir', '')
    if not template_dir or not os.path.isdir(template_dir):
        # 開発リポジトリ内で動かす場合のフォールバック（アドインの親 = リポジトリルート）
        fallback = os.path.join(os.path.dirname(ADDIN_DIR), 'templates')
        if os.path.isdir(fallback):
            template_dir = fallback
        else:
            raise FileNotFoundError(
                'テンプレフォルダが見つかりません。config.json の template_dir を設定してください: '
                + config.get('template_dir', '<未設定>'))
    config['template_dir'] = template_dir
    return config


def active_cam():
    """アクティブドキュメントの CAM プロダクトを返す（無ければ None）。"""
    doc = app().activeDocument
    if not doc:
        return None
    product = doc.products.itemByProductType('CAMProductType')
    return adsk.cam.CAM.cast(product) if product else None


def active_design():
    import adsk.fusion
    doc = app().activeDocument
    if not doc:
        return None
    product = doc.products.itemByProductType('DesignProductType')
    return adsk.fusion.Design.cast(product) if product else None


class _CreatedHandler(adsk.core.CommandCreatedEventHandler):
    def __init__(self, callback):
        super().__init__()
        self._callback = callback

    def notify(self, args):
        try:
            self._callback(args)
        except Exception:
            ui().messageBox('コマンド作成に失敗:\n{}'.format(traceback.format_exc()))


def add_command(panel, command_id, name, tooltip, on_created):
    """コマンド定義＋パネルボタンを登録する。on_created(args) がダイアログ構築を担う。"""
    definitions = ui().commandDefinitions
    definition = definitions.itemById(command_id)
    if definition:
        definition.deleteMe()
    definition = definitions.addButtonDefinition(command_id, name, tooltip)
    definition.commandCreated.add(keep(_CreatedHandler(on_created)))
    control = panel.controls.itemById(command_id)
    if not control:
        panel.controls.addCommand(definition)
    return definition


def remove_command(panel, command_id):
    if panel:
        control = panel.controls.itemById(command_id)
        if control:
            control.deleteMe()
    definition = ui().commandDefinitions.itemById(command_id)
    if definition:
        definition.deleteMe()
