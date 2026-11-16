<#
    Uninstall-FDCamDump.ps1  —  完全移除 UE4SS 注入，回到原版启动状态。
    游戏本体 exe 从未被修改，删掉下面两样即恢复。
#>
[CmdletBinding()]
param([string]$GameRoot = '')

if (-not $GameRoot) {
    if (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'Paralogue\Binaries\Win64')) { $GameRoot = $PSScriptRoot }
    elseif ($env:FDMOD_GAME_DIR) { $GameRoot = $env:FDMOD_GAME_DIR }
    else { throw '未指定游戏目录：请传 -GameRoot <游戏根目录>，或设置环境变量 FDMOD_GAME_DIR' }
}

$Win64 = Join-Path $GameRoot 'Paralogue\Binaries\Win64'
if (Get-Process -Name 'Paralogue-Win64-Shipping' -ErrorAction SilentlyContinue) {
    throw '游戏正在运行，先退出。'
}
foreach ($p in 'dwmapi.dll', 'ue4ss') {
    $t = Join-Path $Win64 $p
    if (Test-Path -LiteralPath $t) { Remove-Item -LiteralPath $t -Recurse -Force; Write-Host "removed $t" -ForegroundColor Yellow }
}
Write-Host 'UE4SS 已移除（FDCamDump\ 抓取结果保留）。' -ForegroundColor Green
