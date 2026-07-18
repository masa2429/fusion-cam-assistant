# 不具合レポート生成：環境情報＋症状＋traceback＋直近ログを .txt に書き出し、
# メモ帳で開いて開発者へ送ってもらう（クリップボード API が不確実なため）。
#
# 自動トリガー（例外時の show_error_report）と手動トリガー（「問題を報告」コマンド）の
# 両方から使う。報告経路は必ずオフラインで速いこと（newer_remote_version は呼ばない）。

import datetime
import os
import subprocess
import tempfile
import traceback
import urllib.parse
import urllib.request
import zipfile

import adsk.core

from . import fusion_utils
from . import update_check

# 「〜へ送って報告」の文言に使う。将来宛先を変えやすいよう定数化。
REPORT_HINT = '開発者'

# フォームへ送るレポート本文の上限。超えたら先頭を切り詰める
# （末尾にログと traceback があり、そちらが重要なため末尾を優先して残す）。
MAX_SUBMIT_CHARS = 40000

_TRUNCATE_MARK = '...(先頭を省略)...\n'

# zip へ同梱するログの上限。ログは追記のみでローテーションが無く肥大しうる。
MAX_LOG_BYTES = 5 * 1024 * 1024

_LOG_TRUNCATE_MARK = '...(古い部分を省略)...\n'


def _log_tail(max_lines=80):
    """ログファイルの末尾 max_lines 行を返す。無ければ ''。"""
    try:
        if not os.path.isfile(fusion_utils.LOG_FILE):
            return ''
        with open(fusion_utils.LOG_FILE, encoding='utf-8') as f:
            lines = f.readlines()
        return ''.join(lines[-max_lines:])
    except OSError:
        return ''


def _environment_lines(command_name):
    """環境情報の各行。1行が失敗しても他は出せるよう個別に try で囲む。"""
    lines = []

    try:
        lines.append('アドイン version: {}'.format(update_check.local_version()))
    except Exception:
        lines.append('アドイン version: (取得失敗)')

    try:
        lines.append('Fusion: {}'.format(fusion_utils.app().version))
    except Exception:
        lines.append('Fusion: (取得失敗)')

    try:
        lines.append('アドイン実体: {}'.format(fusion_utils.ADDIN_DIR))
    except Exception:
        lines.append('アドイン実体: (取得失敗)')

    try:
        lines.append('アクティブドキュメント名: {}'.format(fusion_utils.app().activeDocument.name))
    except Exception:
        lines.append('アクティブドキュメント名: (不明)')

    try:
        lines.append('アクティブワークスペース: {}'.format(fusion_utils.ui().activeWorkspace.id))
    except Exception:
        lines.append('アクティブワークスペース: (取得失敗)')

    try:
        lines.append('個人設定: {}'.format(
            'あり' if os.path.isfile(fusion_utils.LOCAL_CONFIG_PATH) else 'なし'))
    except Exception:
        lines.append('個人設定: (取得失敗)')

    try:
        lines.append('コマンド: {}'.format(command_name))
    except Exception:
        lines.append('コマンド: (取得失敗)')

    try:
        lines.append('発生日時: {:%Y-%m-%d %H:%M:%S}'.format(datetime.datetime.now()))
    except Exception:
        lines.append('発生日時: (取得失敗)')

    return lines


def build_report(command_name, symptom=None, exc_text=None, f3d_path=None):
    """報告テキストを組み立てて返す。f3d_path を渡すと書き出した f3d の場所も載せる。"""
    parts = ['Fusion CAM Assistant 不具合レポート']
    parts.extend(_environment_lines(command_name))

    if symptom:
        parts.append('')
        parts.append('--- 症状（利用者記入）---')
        parts.append(symptom)

    if f3d_path:
        parts.append('')
        parts.append('--- 対象 f3d ---')
        parts.append(f3d_path + '（レポートの zip に design.f3d として同梱）')

    if exc_text:
        parts.append('')
        parts.append('--- traceback ---')
        parts.append(exc_text)

    parts.append('')
    parts.append('--- 直近のログ ---')
    parts.append(_log_tail())

    return '\n'.join(parts)


def _write_to(path, text):
    """指定パスへ報告テキストを書く（上書き）。
    メモ帳で日本語が化けないよう utf-8-sig（BOM 付き）で書く。"""
    with open(path, 'w', encoding='utf-8-sig') as f:
        f.write(text)
    return path


def write_report(text):
    """報告テキストを %TEMP% に書き、絶対パスを返す。"""
    name = 'fusioncam_report_{:%Y%m%d_%H%M%S}.txt'.format(datetime.datetime.now())
    path = os.path.join(tempfile.gettempdir(), name)
    return _write_to(path, text)


def rewrite_report(path, text):
    """既存のレポートを同じパスへ書き直す（f3d のパスを本文へ足すときに使う）。"""
    return _write_to(path, text)


def export_f3d(report_path):
    """アクティブなデザインを、レポート .txt と同じ場所・同じ stem の .f3d に書き出す。
    成功なら絶対パス、失敗・デザイン無しなら None。
    ❗ 例外を外に投げない（f3d が出せなくても報告経路は生かす）。"""
    try:
        design = fusion_utils.active_design()
        if design is None:
            return None
        # ドキュメント名は日本語・記号でファイル名が壊れうるので使わない
        # （ドキュメント名はレポート本文に入っている）
        path = os.path.splitext(report_path)[0] + '.f3d'
        options = design.exportManager.createFusionArchiveExportOptions(path)
        if not design.exportManager.execute(options) or not os.path.isfile(path):
            fusion_utils.log('f3d の書き出しに失敗しました: ' + path)
            return None
        return path
    except Exception:
        fusion_utils.log('f3d の書き出しに失敗:\n' + traceback.format_exc())
        return None


def reveal_in_explorer(path):
    """エクスプローラーでファイルを選択状態にして開く（チャットへドラッグしやすくするため）。
    ❗ os.startfile は .f3d を Fusion で開いてしまうので使わない。
    ❗ explorer は '/select,' とパスを空白なしで繋ぐ必要がある（間に空白が入ると選択が効かない）。
    ユーザー名に空白があるとパスにも空白が入るため引用符で囲む。"""
    try:
        subprocess.Popen('explorer /select,"{}"'.format(path))
    except Exception:
        fusion_utils.log('エクスプローラーで表示できませんでした: ' + str(path))


def _log_for_bundle():
    """zip へ同梱するログ全文を返す。上限を超える分は末尾（新しい側）だけ残す。
    読めなければ ''。❗ 例外を外に投げない。"""
    try:
        if not os.path.isfile(fusion_utils.LOG_FILE):
            return ''
        size = os.path.getsize(fusion_utils.LOG_FILE)
        with open(fusion_utils.LOG_FILE, 'rb') as f:
            if size > MAX_LOG_BYTES:
                f.seek(size - MAX_LOG_BYTES)
                # 切り口が文字の途中に入りうるので errors='replace' で読む
                return _LOG_TRUNCATE_MARK + f.read().decode('utf-8', 'replace')
            return f.read().decode('utf-8', 'replace')
    except Exception:
        fusion_utils.log('ログの読み込みに失敗:\n' + traceback.format_exc())
        return ''


def build_bundle(report_path, text, f3d_path=None):
    """レポート本文・ログ全文・f3d を1つの zip にまとめ、そのパスを返す。
    zip は report_path と同じフォルダ・同じ stem の .zip。失敗なら None。
    ❗ 例外を外に投げない（zip が作れなくても報告経路は生かす）。"""
    zip_path = os.path.splitext(report_path)[0] + '.zip'
    try:
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as archive:
            # 取り出してメモ帳で開いても化けないよう BOM 付きで格納する
            archive.writestr('report.txt', (text or '').encode('utf-8-sig'))
            log_text = _log_for_bundle()
            if log_text:
                archive.writestr('fusioncam.log', log_text.encode('utf-8-sig'))
            if f3d_path and os.path.isfile(f3d_path):
                archive.write(f3d_path, 'design.f3d')
        return zip_path
    except Exception:
        fusion_utils.log('zip の作成に失敗:\n' + traceback.format_exc())
        return None


def cleanup_f3d(f3d_path):
    """zip 化に成功したあと、ばらの .f3d を消す
    （送るものを zip 1個に絞り、どれを送るか迷わせないため）。"""
    if not f3d_path:
        return
    try:
        os.remove(f3d_path)
    except Exception:
        fusion_utils.log('f3d を削除できませんでした: ' + str(f3d_path))


def finalize_report(report_path, text, f3d_path=None):
    """レポートの後処理（zip 化 → ばらの f3d 削除）。手動・自動で共用する。
    戻り値は zip のパス。作れなければ None。❗ 例外を外に投げない。"""
    bundle_path = build_bundle(report_path, text, f3d_path)
    if bundle_path:
        fusion_utils.log('レポート zip: ' + bundle_path)
        cleanup_f3d(f3d_path)
    return bundle_path


def bundle_message(bundle_path):
    """完了メッセージへ付け足す zip の案内。作れなかった場合は '' を返す
    （余計な不安を与えないため何も足さない）。"""
    if not bundle_path:
        return ''
    return ('\n\nレポート・ログ・f3d をまとめた zip を作成しました。これを' + REPORT_HINT
            + 'に送ってください:\n' + bundle_path)


def open_report(path):
    """レポートをメモ帳（既定のテキストアプリ）で開く。失敗はログに残して握る。"""
    try:
        os.startfile(path)
    except Exception:
        fusion_utils.log('レポートを開けませんでした: ' + path)


def _form_config():
    """config.json の report_form を返す。未設定・不正・読み込み失敗なら None。
    ❗ 例外を投げない（送信が使えなくてもファイル方式へ退避できるようにするため）。"""
    try:
        config = fusion_utils.load_config()
    except Exception:
        return None
    try:
        form = config.get('report_form')
        if not isinstance(form, dict):
            return None
        url = form.get('url')
        fields = form.get('fields')
        if not url or not isinstance(fields, dict) or not fields:
            return None
        return {'url': url, 'fields': fields}
    except Exception:
        return None


def can_submit():
    """フォーム送信が使えるか。False なら従来のファイル方式で報告する。"""
    return _form_config() is not None


def _truncate_for_submit(text):
    """上限を超えたら末尾を残して先頭を切り詰める。"""
    text = text or ''
    if len(text) <= MAX_SUBMIT_CHARS:
        return text
    keep = MAX_SUBMIT_CHARS - len(_TRUNCATE_MARK)
    return _TRUNCATE_MARK + text[-keep:]


def submit_report(report_text, command_name, symptom='', reporter='', timeout_seconds=10):
    """Google フォームへレポートを POST する。成功なら True。
    ❗ 例外を外に投げない（失敗はログに残して False）。"""
    form = _form_config()
    if form is None:
        return False
    try:
        try:
            version = update_check.local_version()
        except Exception:
            version = '(取得失敗)'

        fields = form['fields']
        values = {
            'reporter': reporter or '',
            'symptom': symptom or '',
            'command': command_name or '',
            'version': version,
            'report': _truncate_for_submit(report_text),
        }
        payload = {}
        for key, entry_id in fields.items():
            if entry_id:
                payload[entry_id] = values.get(key, '')

        data = urllib.parse.urlencode(payload).encode('utf-8')
        request = urllib.request.Request(
            form['url'], data=data, headers={'User-Agent': 'FusionCamAssistant'})
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            status = getattr(response, 'status', None) or response.getcode()
        if 200 <= int(status) < 300:
            return True
        fusion_utils.log('レポート送信に失敗: HTTP {}'.format(status))
        return False
    except Exception:
        fusion_utils.log('レポート送信に失敗: ' + traceback.format_exc())
        return False


def submit_or_open(report_text, path, command_name, symptom='', reporter=''):
    """送信を試み、失敗したらローカルのレポートを開く。戻り値は送信できたか。"""
    if submit_report(report_text, command_name, symptom=symptom, reporter=reporter):
        return True
    open_report(path)
    return False


def _attach_design(command_name, exc_text, path, text):
    """自動報告用：f3d を書き出して本文へ場所を足し、zip にまとめる。
    戻り値は (最終テキスト, zip パス or None, f3d パス or None)。
    ❗ 例外を外に投げない（書き出しに失敗しても報告は完走させる）。"""
    f3d_path = export_f3d(path)
    if f3d_path:
        text = build_report(command_name, exc_text=exc_text, f3d_path=f3d_path)
        rewrite_report(path, text)
        fusion_utils.log('対象 f3d: ' + f3d_path)
    bundle_path = finalize_report(path, text, f3d_path)
    return text, bundle_path, f3d_path


def show_error_report(command_name):
    """例外の except 節から呼ぶ。環境＋traceback＋ログの報告を作って開く。
    ❗ この関数自体は絶対に例外を投げない（最後の手段の messageBox まで try で囲む）。"""
    try:
        exc_text = traceback.format_exc()
        text = build_report(command_name, exc_text=exc_text)
        # 送信可否に関わらず、まずローカルに必ず残す
        path = write_report(text)
        fusion_utils.log('不具合レポート: ' + path)

        if not can_submit():
            # 送信先が未設定・設定が壊れている場合は従来のファイル方式
            text, bundle_path, f3d_path = _attach_design(command_name, exc_text, path, text)
            open_report(path)
            fusion_utils.ui().messageBox(
                'エラーが発生しました。不具合レポートを作成しました。\n'
                '開いたテキストの内容を' + REPORT_HINT + 'に送って報告してください。\n\n' + path
                + bundle_message(bundle_path),
                'Fusion CAM Assistant')
            reveal_path = bundle_path or f3d_path
            if reveal_path:
                reveal_in_explorer(reveal_path)
            return

        # ❗ f3d 書き出しと zip は重く、大きなデザインだと数十秒かかる。
        # エラー直後に無反応になるとフリーズに見えるので、同意を取ってから行う。
        result = fusion_utils.ui().messageBox(
            'エラーが発生しました。\n不具合レポートを開発者に送信しますか？\n\n'
            '送信内容：アドイン version・Fusion バージョン・ドキュメント名・'
            'エラー内容・直近のログ\n\n保存先: ' + path,
            'Fusion CAM Assistant',
            adsk.core.MessageBoxButtonTypes.YesNoButtonType,
            adsk.core.MessageBoxIconTypes.QuestionIconType)
        if result != adsk.core.DialogResults.DialogYes:
            # 拒否されたので f3d 書き出しも zip も行わない（パスはログに残すだけ）
            fusion_utils.log('レポート送信は見送られました: ' + path)
            return

        text, bundle_path, f3d_path = _attach_design(command_name, exc_text, path, text)
        reveal_path = bundle_path or f3d_path

        if submit_or_open(text, path, command_name):
            fusion_utils.ui().messageBox(
                '送信しました。ご協力ありがとうございます。\n\nローカル保存先: ' + path
                + bundle_message(bundle_path),
                'Fusion CAM Assistant')
        else:
            fusion_utils.ui().messageBox(
                '送信に失敗しました（オフラインの可能性）。\n'
                '開いたテキストの内容を' + REPORT_HINT + 'に送ってください。\n\n' + path
                + bundle_message(bundle_path),
                'Fusion CAM Assistant')
        if reveal_path:
            reveal_in_explorer(reveal_path)
    except Exception:
        try:
            fusion_utils.ui().messageBox('エラー:\n' + traceback.format_exc())
        except Exception:
            pass
