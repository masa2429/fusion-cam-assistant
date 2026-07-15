# QUHP CAM Assistant インストールスクリプト
# リポジトリを clone した場所から Fusion の AddIns フォルダへジャンクションを作る。
# 使い方: リポジトリのルートで右クリック →「PowerShell で実行」または
#   powershell -ExecutionPolicy Bypass -File .\install.ps1

$ErrorActionPreference = 'Stop'

$repoRoot = $PSScriptRoot
$source = Join-Path $repoRoot 'QuhpCamAssistant'
$addins = Join-Path $env:APPDATA 'Autodesk\Autodesk Fusion 360\API\AddIns'
$target = Join-Path $addins 'QuhpCamAssistant'

if (-not (Test-Path $source)) {
    Write-Error "QuhpCamAssistant フォルダが見つかりません: $source"
}
if (-not (Test-Path $addins)) {
    Write-Error ("Fusion の AddIns フォルダが見つかりません: $addins`n" +
                 "Fusion 360 を一度起動してから再実行してください。")
}

if (Test-Path $target) {
    $item = Get-Item $target -Force
    if ($item.LinkType -eq 'Junction') {
        Write-Host "既存のジャンクションを更新します: $target"
        Remove-Item $target -Force
    } else {
        Write-Error ("AddIns に既に QuhpCamAssistant フォルダが存在します（ジャンクションではありません）。`n" +
                     "手動で退避してから再実行してください: $target")
    }
}

New-Item -ItemType Junction -Path $target -Target $source | Out-Null
Write-Host ''
Write-Host '=== インストール完了 ===' -ForegroundColor Green
Write-Host "リンク: $target"
Write-Host "実体  : $source"
Write-Host ''
Write-Host '次の手順:'
Write-Host '  1. Fusion 360 を起動（起動済みなら Shift+S でスクリプトとアドインを開く）'
Write-Host '  2. アドインタブの QuhpCamAssistant を「実行」（「起動時に実行」推奨）'
Write-Host '  3. 製造ワークスペースの「工具」タブに「QUHP CAM」パネルが出れば成功'
