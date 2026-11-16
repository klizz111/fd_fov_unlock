<#
    Test-Launch.ps1 — 逐个开关做「注入能不能活过载入地图」的对照实验。

    用法：
        pwsh -File .\Test-Launch.ps1 -ModsList FDCamDump -WaitSec 45
        pwsh -File .\Test-Launch.ps1 -ModsList FDCamDump -DisableHooks HookLoadMap,HookAActorTick -WaitSec 45
        pwsh -File .\Test-Launch.ps1 -ModsList FDCamDump,ConsoleEnablerMod -WaitSec 45

    每次运行会：
      1. 重写 ue4ss\Mods\mods.txt（只启用 -ModsList 里的 mod，Keybinds 常开）
      2. 按 -DisableHooks 把 [Hooks] 段对应的键置 0（-RestoreHooks 恢复默认 1）
      3. 用 deploy.py run 拉起游戏
      4. 轮询 -WaitSec 秒，报告「活着 / 崩了」，并打印 UE4SS.log 与游戏日志尾部
#>
[CmdletBinding()]
param(
    [string]$GameRoot = '',
    [string]$PythonExe = 'python',
    [string[]]$ModsList = @('FDCamDump'),
    [string[]]$DisableHooks = @(),
    [switch]$RestoreHooks,
    [int]$WaitSec = 45
)

$ErrorActionPreference = 'Stop'
if (-not $GameRoot) {
    if (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'Paralogue\Binaries\Win64')) { $GameRoot = $PSScriptRoot }
    elseif ($env:FDMOD_GAME_DIR) { $GameRoot = $env:FDMOD_GAME_DIR }
    else { throw '未指定游戏目录：请传 -GameRoot <游戏根目录>，或设置环境变量 FDMOD_GAME_DIR' }
}

$W   = Join-Path $GameRoot 'Paralogue\Binaries\Win64'
$U   = Join-Path $W 'ue4ss'
$Exe = Join-Path $W 'Paralogue-Win64-Shipping.exe'

# ---- 现有 mod 全表（mods.txt 里出现过的名字）
$AllMods = @('CheatManagerEnablerMod', 'ConsoleCommandsMod', 'ConsoleEnablerMod',
             'SplitScreenMod', 'LineTraceMod', 'BPML_GenericFunctions',
             'BPModLoaderMod', 'FDCamDump', 'Keybinds')

$modsTxt = Join-Path $U 'Mods\mods.txt'
$lines = New-Object System.Collections.Generic.List[string]
foreach ($m in $AllMods) {
    $on = ($ModsList -contains $m) -or ($m -eq 'Keybinds')
    $lines.Add(("{0} : {1}" -f $m, $(if ($on) { 1 } else { 0 })))
}
$lines.Add('')
$lines.Add('; Built-in keybinds, do not move up!')
$lines.Add('Keybinds : 1')
Set-Content -LiteralPath $modsTxt -Value $lines -Encoding UTF8
Write-Host ("mods.txt -> enabled: " + (($ModsList + 'Keybinds') -join ', ')) -ForegroundColor Cyan

# ---- hooks
$ini = Join-Path $U 'UE4SS-settings.ini'
$txt = Get-Content -LiteralPath $ini -Raw
function Set-Hook([string]$t, [string]$k, [string]$v) {
    $pat = "(?m)^([ \t]*" + [regex]::Escape($k) + "[ \t]*=[ \t]*)([^\r\n]*)"
    if ([regex]::IsMatch($t, $pat)) { return [regex]::Replace($t, $pat, { param($m) $m.Groups[1].Value + $v }) }
    return $t
}
if ($RestoreHooks) {
    foreach ($h in @('HookProcessInternal','HookProcessLocalScriptFunction','HookInitGameState',
                     'HookLoadMap','HookCallFunctionByNameWithArguments','HookBeginPlay','HookEndPlay',
                     'HookLocalPlayerExec','HookAActorTick','HookEngineTick','HookGameViewportClientTick',
                     'HookUObjectProcessEvent','HookProcessConsoleExec','HookUStructLink')) {
        $txt = Set-Hook $txt $h '1'
    }
    Write-Host 'hooks -> all 1 (default)' -ForegroundColor Cyan
}
if ($DisableHooks.Count -gt 0) {
    foreach ($h in $DisableHooks) { $txt = Set-Hook $txt $h '0' }
    Write-Host ("hooks -> disabled: " + ($DisableHooks -join ', ')) -ForegroundColor Cyan
}
Set-Content -LiteralPath $ini -Value $txt -Encoding UTF8 -NoNewline

# ---- 干净启动
Get-Process -Name 'Paralogue-Win64-Shipping' -ErrorAction SilentlyContinue | Stop-Process -Force
$logPath = Join-Path $U 'UE4SS.log'
if (Test-Path $logPath) { Remove-Item -LiteralPath $logPath -Force }

Push-Location $GameRoot
& $PythonExe 'deploy.py' run 2>&1 | Select-Object -Last 6
Pop-Location

# ---- 轮询
$alive = $false
for ($i = 0; $i -lt $WaitSec; $i += 3) {
    Start-Sleep -Seconds 3
    $p = Get-Process -Name 'Paralogue-Win64-Shipping' -ErrorAction SilentlyContinue
    if (-not $p) { break }
    $alive = $true
}

Write-Host ''
$p = Get-Process -Name 'Paralogue-Win64-Shipping' -ErrorAction SilentlyContinue
if ($p) {
    Write-Host ("[RESULT] ALIVE  pid=$($p.Id)  已存活 >= ${WaitSec}s") -ForegroundColor Green
} else {
    Write-Host "[RESULT] DEAD  进程已退出/崩溃" -ForegroundColor Red
}

Write-Host '--- UE4SS.log 尾部 ---' -ForegroundColor Cyan
if (Test-Path $logPath) { Get-Content -LiteralPath $logPath -Tail 8 } else { 'no UE4SS.log' }

Write-Host '--- 游戏日志：崩溃摘要 ---' -ForegroundColor Cyan
$gl = Join-Path $GameRoot 'Paralogue\Saved\Logs\Paralogue.log'
if (Test-Path $gl) {
    Get-Content -LiteralPath $gl -Tail 400 -Encoding UTF8 |
        Select-String -Pattern 'Unhandled Exception|Critical error|LogLoad: LoadMap' |
        Select-Object -Last 4 | ForEach-Object { $_.Line }
}

Write-Host '--- 抓取产物 ---' -ForegroundColor Cyan
Get-ChildItem -LiteralPath (Join-Path $W 'FDCamDump') -File -ErrorAction SilentlyContinue |
    Select-Object Name, Length | Format-Table -AutoSize
Get-ChildItem -LiteralPath $W -Include '*.jmap','*.usmap' -File -ErrorAction SilentlyContinue |
    Select-Object Name, Length | Format-Table -AutoSize
