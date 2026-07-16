# Fusion CAM Assistant アンインストールスクリプト
# Fusion の AddIns フォルダからリンク（ジャンクション）を削除する。
# このリポジトリのフォルダ本体・テンプレート・個人設定は削除しない。

$ErrorActionPreference = 'Stop'

$addins = Join-Path $env:APPDATA 'Autodesk\Autodesk Fusion 360\API\AddIns'
$target = Join-Path $addins 'FusionCamAssistant'

# 旧名（QuhpCamAssistant）時代のリンクも掃除する
$legacy = Join-Path $addins 'QuhpCamAssistant'
if (Test-Path $legacy) {
    $legacyItem = Get-Item $legacy -Force
    if ($legacyItem.LinkType -eq 'Junction') {
        $legacyItem.Delete()
        Write-Host "旧アドインのリンクを削除しました: $legacy"
    }
}

if (-not (Test-Path $target)) {
    Write-Host 'アドインは登録されていません（削除済み）。' -ForegroundColor Yellow
} else {
    $item = Get-Item $target -Force
    if ($item.LinkType -eq 'Junction') {
        # ジャンクションは -Recurse を付けずに削除する（リンクだけが消え、実体は残る）
        $item.Delete()
        Write-Host "リンクを削除しました: $target" -ForegroundColor Green
    } else {
        Write-Host ("AddIns 内の FusionCamAssistant はリンクではなく実フォルダです。`n" +
                    "コピーでインストールされた場合はこのフォルダ自体がアドイン本体のため、`n" +
                    "内容を確認のうえエクスプローラーで手動削除してください:`n  $target") -ForegroundColor Yellow
    }
}

# 一時ファイル（ログ・テンプレの一時コピー）を掃除する
Remove-Item (Join-Path $env:TEMP 'fusioncam*') -Force -ErrorAction SilentlyContinue

Write-Host ''
Write-Host '=== アンインストール完了 ===' -ForegroundColor Green
Write-Host '補足:'
Write-Host '  - Fusion 起動中だった場合、アドインは次回起動から読み込まれなくなります'
Write-Host '  - このフォルダ（リポジトリ本体）は残っています。不要なら丸ごと削除してください'
Write-Host '  - 生成済みの切削データ（セットアップ「自動CAM」等）は各ドキュメント内に残ります'
