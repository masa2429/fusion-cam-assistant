# Fusion 360 スクリプト：整列（Arrange）API の実機調査ダンプ
#
# 使い方:
#   1. できれば「手動で整列（修正 → 整列）を1回実行した」ドキュメントを開く
#      （既存フィーチャの設定値が、自動配置実装の正解データになる）
#   2. Fusion の「ユーティリティ → アドイン → スクリプト」でこのファイルを追加して実行
#   3. 保存先を聞かれるのでテキストファイルを指定（例: tools/output/arrange_dump.txt）
#
# 目的（自動配置コマンド auto_place の実装前に確定する）:
#   - root.features.arrangeFeatures の有無（必要 Fusion バージョン）
#   - create2DInput の引数と ArrangeFeatureInput / definition / arrangeComponents の実プロパティ名
#   - solverType の列挙値（無償の矩形ソルバーはどれか）
#   - 部品間隔・枠余白・回転許可の設定名と、手動整列フィーチャに入っている実値
#
# 最後に「作成テスト」（create2DInput → add を最小構成で実行）を Yes/No で選べる。
# 実行した場合はドキュメントに整列フィーチャが1つ増えるので、不要なら Ctrl+Z で戻すこと。

import traceback

import adsk.core
import adsk.fusion

_MAX_DEPTH = 3
_MAX_ITEMS = 12
# 再帰ダンプで辿らない汎用プロパティ（ノイズ・循環の元）
_SKIP_PROPS = {
    'classType', 'cast', 'objectType', 'isValid', 'attributes', 'entityToken',
    'nativeObject', 'assemblyContext', 'parentComponent', 'parentDesign',
    'parent', 'timelineObject', 'this', 'thisown', 'faces', 'edges', 'vertices',
    'bodies', 'meshManager',
}


def _doc_first_line(obj):
    doc = getattr(obj, '__doc__', None)
    if not doc:
        return ''
    return doc.strip().splitlines()[0][:200]


def _dump_class_surface(lines, cls_name):
    """adsk.fusion のクラスの公開メンバー一覧を docstring（swig はシグネチャ入り）付きで出す。"""
    cls = getattr(adsk.fusion, cls_name, None)
    if cls is None:
        lines.append(f'adsk.fusion.{cls_name}: <存在しない>')
        return
    lines.append(f'adsk.fusion.{cls_name}:')
    for member in sorted(dir(cls)):
        if member.startswith('_') or member in ('cast', 'classType', 'thisown'):
            continue
        try:
            attr = getattr(cls, member)
            doc = _doc_first_line(attr)
        except Exception:
            doc = '<doc取得失敗>'
        lines.append(f'    .{member}  {doc}')


def _dump_enum(lines, cls_name):
    cls = getattr(adsk.fusion, cls_name, None)
    if cls is None:
        return
    members = [(m, getattr(cls, m)) for m in dir(cls)
               if not m.startswith('_') and isinstance(getattr(cls, m, None), int)]
    if members:
        lines.append(f'enum adsk.fusion.{cls_name}:')
        for name, value in sorted(members, key=lambda x: x[1]):
            lines.append(f'    {name} = {value}')


def _dump_object(lines, indent, obj, depth, seen):
    """adsk オブジェクトのプロパティ値を再帰的にダンプする。"""
    if obj is None:
        lines.append(f'{indent}<None>')
        return
    obj_type = getattr(obj, 'objectType', type(obj).__name__)
    key = id(obj)
    if key in seen or depth <= 0:
        lines.append(f'{indent}[{obj_type}] <省略（深さ/循環）>')
        return
    seen = seen | {key}
    lines.append(f'{indent}[{obj_type}]')

    # コレクション（count + item）は要素をダンプ
    if hasattr(obj, 'count') and hasattr(obj, 'item'):
        try:
            count = obj.count
            lines.append(f'{indent}  count = {count}')
            for i in range(min(count, _MAX_ITEMS)):
                lines.append(f'{indent}  item({i}):')
                _dump_object(lines, indent + '    ', obj.item(i), depth - 1, seen)
        except Exception as ex:
            lines.append(f'{indent}  <コレクション列挙失敗: {ex}>')
        return

    for member in sorted(dir(obj)):
        if member.startswith('_') or member in _SKIP_PROPS:
            continue
        try:
            value = getattr(obj, member)
        except Exception as ex:
            lines.append(f'{indent}  .{member} = <取得失敗: {type(ex).__name__}>')
            continue
        if callable(value):
            continue
        if hasattr(value, 'objectType'):
            lines.append(f'{indent}  .{member}:')
            _dump_object(lines, indent + '    ', value, depth - 1, seen)
        else:
            lines.append(f'{indent}  .{member} = {value!r}')


def _try_creation_test(lines, ui, design, arrange_features):
    """最小構成で create2DInput → add を試す（ユーザー同意済み）。"""
    root = design.rootComponent
    lines.append('\n' + '=' * 80)
    lines.append('作成テスト（create2DInput → add）')

    # envelope 用プロファイル: 「配置枠 280x280」スケッチがあれば使う
    profile = None
    for i in range(root.sketches.count):
        sketch = root.sketches.item(i)
        if sketch.name == '配置枠 280x280' and sketch.profiles.count > 0:
            profile = sketch.profiles.item(0)
            lines.append(f'envelope プロファイル: スケッチ {sketch.name!r} の profiles.item(0)')
            break
    if profile is None:
        lines.append('スケッチ「配置枠 280x280」が無いため作成テストを中止'
                     '（先に「配置チェック」コマンドで枠を作成しておくこと）')
        return

    occurrences = [root.occurrences.item(i) for i in range(root.occurrences.count)]
    lines.append(f'対象トップレベルオカレンス: {len(occurrences)} 件')
    if not occurrences:
        lines.append('オカレンスが無いため作成テストを中止')
        return

    try:
        arrange_input = arrange_features.create2DInput()
        lines.append('create2DInput() 引数なし: 成功')
    except Exception as ex:
        lines.append(f'create2DInput() 引数なし: 失敗 {ex}')
        lines.append('  ※上のクラス表面ダンプの create2DInput の docstring で引数を確認すること')
        return

    lines.append('\n--- 作成直後の ArrangeFeatureInput ---')
    _dump_object(lines, '  ', arrange_input, _MAX_DEPTH, set())

    # envelope と対象を設定できる範囲で設定してみる（メソッド名は実機の実在に依存）
    try:
        if hasattr(arrange_input, 'setProfileOrFaceEnvelope'):
            arrange_input.setProfileOrFaceEnvelope([profile])
            lines.append('setProfileOrFaceEnvelope([profile]): 成功')
    except Exception as ex:
        lines.append(f'setProfileOrFaceEnvelope: 失敗 {ex}')
    try:
        comps = arrange_input.arrangeComponents
        added = 0
        for occurrence in occurrences:
            try:
                comps.add(occurrence)
                added += 1
            except Exception as ex:
                lines.append(f'arrangeComponents.add({occurrence.name}): 失敗 {ex}')
        lines.append(f'arrangeComponents.add: {added}/{len(occurrences)} 件成功')
    except Exception as ex:
        lines.append(f'arrangeComponents アクセス失敗: {ex}')

    lines.append('\n--- 設定後の ArrangeFeatureInput ---')
    _dump_object(lines, '  ', arrange_input, _MAX_DEPTH, set())

    try:
        feature = arrange_features.add(arrange_input)
        lines.append('\nadd(): 成功（不要なら Ctrl+Z で戻すこと）')
        lines.append('\n--- 作成された ArrangeFeature ---')
        _dump_object(lines, '  ', feature, _MAX_DEPTH, set())
        try:
            lines.append(f'\narrangeStatistics = {feature.arrangeStatistics}')
        except Exception as ex:
            lines.append(f'arrangeStatistics 取得失敗: {ex}')
    except Exception as ex:
        lines.append(f'\nadd(): 失敗\n{traceback.format_exc()}')
        _ = ex


def run(context):
    ui = None
    try:
        app = adsk.core.Application.get()
        ui = app.userInterface

        design_product = app.activeDocument.products.itemByProductType('DesignProductType')
        if not design_product:
            ui.messageBox('このドキュメントにデザインがありません。')
            return
        design = adsk.fusion.Design.cast(design_product)
        root = design.rootComponent

        lines = []
        lines.append(f'Fusion version: {app.version}')
        lines.append(f'Document: {app.activeDocument.name}')
        lines.append(f'designType: {design.designType}  (0=direct, 1=parametric)')
        lines.append('=' * 80)

        # 1) API の有無
        lines.append('\n## 1. ArrangeFeatures の有無')
        lines.append(f"adsk.fusion.ArrangeFeatures クラス: "
                     f"{'あり' if hasattr(adsk.fusion, 'ArrangeFeatures') else 'なし'}")
        arrange_features = getattr(root.features, 'arrangeFeatures', None)
        lines.append(f'root.features.arrangeFeatures: '
                     f'{"あり" if arrange_features is not None else "なし"}')
        if arrange_features is None:
            lines.append('→ この Fusion バージョンでは整列 API が使えない')

        # 2) Arrange 関連の列挙値
        lines.append('\n## 2. Arrange 関連の列挙値')
        for name in sorted(dir(adsk.fusion)):
            if 'arrange' in name.lower() and name.endswith('Types'):
                _dump_enum(lines, name)

        # 3) 関連クラスの表面（swig docstring にシグネチャが入る）
        lines.append('\n## 3. 関連クラスの公開メンバー')
        arrange_classes = [name for name in sorted(dir(adsk.fusion))
                           if 'arrange' in name.lower()]
        lines.append(f'adsk.fusion 内の Arrange 関連クラス: {arrange_classes}')
        for name in arrange_classes:
            lines.append('')
            _dump_class_surface(lines, name)

        # 4) 既存の整列フィーチャ（手動整列の実値＝正解データ）
        lines.append('\n## 4. 既存の整列フィーチャの実値')
        if arrange_features is not None:
            count = arrange_features.count
            lines.append(f'既存 ArrangeFeature: {count} 件')
            for i in range(count):
                lines.append(f'\n--- ArrangeFeature[{i}] ---')
                _dump_object(lines, '  ', arrange_features.item(i), _MAX_DEPTH, set())
            if count == 0:
                lines.append('（手動で整列を実行したドキュメントでもう一度流すと、'
                             '間隔・回転などの実パラメータ名と値が取れる）')

        # 5) 作成テスト（任意）
        if arrange_features is not None:
            answer = ui.messageBox(
                '最小構成の作成テスト（create2DInput → add）を実行しますか？\n'
                '整列フィーチャが1つ作成されます（不要なら実行後に Ctrl+Z）。\n'
                '※スケッチ「配置枠 280x280」とトップレベルコンポーネントが必要',
                'DumpArrange', adsk.core.MessageBoxButtonTypes.YesNoButtonType)
            if answer == adsk.core.DialogResults.DialogYes:
                _try_creation_test(lines, ui, design, arrange_features)

        dialog = ui.createFileDialog()
        dialog.title = 'ダンプの保存先'
        dialog.filter = 'テキスト (*.txt)'
        dialog.initialFilename = 'arrange_dump.txt'
        if dialog.showSave() != adsk.core.DialogResults.DialogOK:
            return
        with open(dialog.filename, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
        ui.messageBox(f'ダンプ完了:\n{dialog.filename}\n\n'
                      f'結果を docs/api-notes.md に反映してから auto_place を実装すること。')
    except Exception:
        if ui:
            ui.messageBox('失敗:\n{}'.format(traceback.format_exc()))
