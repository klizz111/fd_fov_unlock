<#
    Run-Sweep.ps1 — 重启游戏，趁它自己拿到前台的那几秒把热键送进去。

    背景：UE4SS 热键只认真实按键（键盘钩子），而 keybd_event 只发给前台窗口。
    从后台进程 SetForegroundWindow 会被系统拒绝，所以只能利用「游戏刚启动时
    自己抢到前台」这个窗口期。14:47 那次 F6 能触发就是因为这个。

    用法：
        pwsh -File .\Run-Sweep.ps1 -Key F6
        pwsh -File .\Run-Sweep.ps1 -Key F7 -KeepRunning
#>
[CmdletBinding()]
param(
    [ValidateSet('F5','F6','F7','F9','F10')][string]$Key = 'F6',
    [string]$GameRoot = '',
    [string]$PythonExe = 'python',
    [int]$BootTimeoutSec = 120,
    [int]$FocusTimeoutSec = 90,
    [switch]$KeepRunning
)

$ErrorActionPreference = 'Stop'
Add-Type -Namespace W5 -Name U -MemberDefinition @'
public delegate bool EnumProc(System.IntPtr hWnd, System.IntPtr lParam);
[System.Runtime.InteropServices.DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, System.IntPtr lParam);
[System.Runtime.InteropServices.DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(System.IntPtr hWnd, out uint pid);
[System.Runtime.InteropServices.DllImport("user32.dll", CharSet=System.Runtime.InteropServices.CharSet.Auto)] public static extern int GetClassName(System.IntPtr hWnd, System.Text.StringBuilder sb, int n);
[System.Runtime.InteropServices.DllImport("user32.dll")] public static extern System.IntPtr GetForegroundWindow();
[System.Runtime.InteropServices.DllImport("user32.dll")] public static extern void keybd_event(byte bVk, byte bScan, uint dwFlags, System.UIntPtr extra);
[System.Runtime.InteropServices.DllImport("user32.dll")] public static extern bool IsWindowVisible(System.IntPtr hWnd);
'@

if (-not $GameRoot) {
    if (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'Paralogue\Binaries\Win64')) { $GameRoot = $PSScriptRoot }
    elseif ($env:FDMOD_GAME_DIR) { $GameRoot = $env:FDMOD_GAME_DIR }
    else { throw '未指定游戏目录：请传 -GameRoot <游戏根目录>，或设置环境变量 FDMOD_GAME_DIR' }
}

$W64   = Join-Path $GameRoot 'Paralogue\Binaries\Win64'
$Log   = Join-Path $W64 'ue4ss\UE4SS.log'
$DumpD = Join-Path $W64 'FDCamDump'

function Get-GameWindow {
    $p = Get-Process -Name 'Paralogue-Win64-Shipping' -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $p) { return $null }
    $targetPid = [uint32]$p.Id
    $res = $null
    $cb = [W5.U+EnumProc]{
        param($hWnd, $l)
        $wpid = [uint32]0
        [void][W5.U]::GetWindowThreadProcessId($hWnd, [ref]$wpid)
        if ($wpid -eq $targetPid) {
            $cn = New-Object System.Text.StringBuilder 128
            [void][W5.U]::GetClassName($hWnd, $cn, 128)
            if ($cn.ToString() -eq 'UnrealWindow' -and [W5.U]::IsWindowVisible($hWnd)) {
                $script:res2 = [int64]$hWnd
            }
        }
        return $true
    }
    [void][W5.U]::EnumWindows($cb, [IntPtr]::Zero)
    return $script:res2
}

Write-Host '=== 1) 结束旧进程 ===' -ForegroundColor Cyan
Get-Process -Name 'Paralogue-Win64-Shipping' -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 3
Remove-Item -LiteralPath $Log -ErrorAction SilentlyContinue
if (Test-Path $DumpD) { Remove-Item -LiteralPath (Join-Path $DumpD '*.txt') -ErrorAction SilentlyContinue }

Write-Host '=== 2) 启动游戏 ===' -ForegroundColor Cyan
Push-Location $GameRoot
& $PythonExe 'deploy.py' run 2>&1 | Select-Object -Last 2
Pop-Location

Write-Host '=== 3) 等 mod 载入 ===' -ForegroundColor Cyan
$loaded = $false
for ($i = 0; $i -lt $BootTimeoutSec; $i += 3) {
    Start-Sleep -Seconds 3
    if (Test-Path $Log) {
        $hit = Select-String -LiteralPath $Log -Pattern 'v3 loaded' -ErrorAction SilentlyContinue
        if ($hit) { $loaded = $true; break }
    }
}
if ($loaded) { Write-Host '  mod 已载入' -ForegroundColor Green } else { Write-Host '  [!] 等不到 v3 loaded，继续尝试' -ForegroundColor Yellow }

Write-Host '=== 4) 抢占前台窗口期并按键 ===' -ForegroundColor Cyan
$sent = $false
$deadline = (Get-Date).AddSeconds($FocusTimeoutSec)
while ((Get-Date) -lt $deadline) {
    $h = Get-GameWindow
    if (-not $h) { Start-Sleep -Milliseconds 500; continue }
    $fg = [W5.U]::GetForegroundWindow()
    if ([int64]$fg -eq [int64]$h) {
        $vk = @{ F5=0x74; F6=0x75; F7=0x76; F9=0x78; F10=0x79 }[$Key]
        [W5.U]::keybd_event([byte]$vk, 0, 0, [UIntPtr]::Zero)
        Start-Sleep -Milliseconds 140
        [W5.U]::keybd_event([byte]$vk, 0, 2, [UIntPtr]::Zero)
        Write-Host ("  KEY {0} 已送达 0x{1:X8}（游戏当时在前台）" -f $Key, $h) -ForegroundColor Green
        $sent = $true
        break
    }
    Start-Sleep -Milliseconds 700
}
if (-not $sent) { Write-Host '  [FAIL] 一直没等到游戏在前台，按键未发送' -ForegroundColor Red }

Write-Host '=== 5) 等产物 ===' -ForegroundColor Cyan
Start-Sleep -Seconds 15
Get-ChildItem -LiteralPath $DumpD -ErrorAction SilentlyContinue | Select-Object Name, Length, LastWriteTime | Format-Table -AutoSize
Write-Host '--- UE4SS.log 尾部 6 ---'
if (Test-Path $Log) { Get-Content -LiteralPath $Log -Tail 6 }
Write-Host '--- 进程 ---'
if (Get-Process -Name 'Paralogue-Win64-Shipping' -ErrorAction SilentlyContinue) { 'ALIVE' } else { 'DEAD' }
if (-not $KeepRunning) {
    Get-Process -Name 'Paralogue-Win64-Shipping' -ErrorAction SilentlyContinue | Stop-Process -Force
    Remove-Item -LiteralPath (Join-Path $W64 'ue4ss\crash_*.dmp') -ErrorAction SilentlyContinue
    Write-Host '（已结束游戏，保持干净）' -ForegroundColor Yellow
}
