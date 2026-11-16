<#
    Send-GameKey.ps1 — 把真实按键送进 Fallen Doll 的 UnrealWindow。

    为什么不能用 PostMessage：UE4SS 的热键走的是键盘钩子，只认真实输入事件。
    为什么 keybd_event 有时白按：它发给「前台窗口」，游戏一失焦就丢。
    SetForegroundWindow 从后台进程常被系统拒绝，所以这里用三步：
      1) ShowWindow(SW_RESTORE) + AttachThreadInput(前台线程) + SetForegroundWindow
      2) 若仍不是前台 -> 把光标移到窗口中心做一次真实左键点击（点击激活系统允许）
      3) 校验 GetForegroundWindow == 目标句柄，再 keybd_event 按下/抬起

    用法：
        pwsh -File .\Send-GameKey.ps1 -Key F6
        pwsh -File .\Send-GameKey.ps1 -Key F7 -NoClick
        pwsh -File .\Send-GameKey.ps1 -ListOnly
#>
[CmdletBinding()]
param(
    [ValidateSet('F1','F2','F3','F4','F5','F6','F7','F8','F9','F10','F11','F12')][string]$Key = 'F6',
    [string]$ProcessName = 'Paralogue-Win64-Shipping',
    [string]$PreferClass = 'UnrealWindow',
    [switch]$NoClick,
    [switch]$ListOnly
)

$ErrorActionPreference = 'Stop'

Add-Type -Namespace W4 -Name U -MemberDefinition @'
public delegate bool EnumProc(System.IntPtr hWnd, System.IntPtr lParam);
[System.Runtime.InteropServices.DllImport("user32.dll")]
public static extern bool EnumWindows(EnumProc cb, System.IntPtr lParam);
[System.Runtime.InteropServices.DllImport("user32.dll")]
public static extern uint GetWindowThreadProcessId(System.IntPtr hWnd, out uint pid);
[System.Runtime.InteropServices.DllImport("user32.dll", CharSet=System.Runtime.InteropServices.CharSet.Auto)]
public static extern int GetClassName(System.IntPtr hWnd, System.Text.StringBuilder sb, int n);
[System.Runtime.InteropServices.DllImport("user32.dll", CharSet=System.Runtime.InteropServices.CharSet.Auto)]
public static extern int GetWindowText(System.IntPtr hWnd, System.Text.StringBuilder sb, int n);
[System.Runtime.InteropServices.DllImport("user32.dll")]
public static extern bool IsWindowVisible(System.IntPtr hWnd);
[System.Runtime.InteropServices.DllImport("user32.dll")]
public static extern bool SetForegroundWindow(System.IntPtr hWnd);
[System.Runtime.InteropServices.DllImport("user32.dll")]
public static extern bool BringWindowToTop(System.IntPtr hWnd);
[System.Runtime.InteropServices.DllImport("user32.dll")]
public static extern bool ShowWindow(System.IntPtr hWnd, int n);
[System.Runtime.InteropServices.DllImport("user32.dll")]
public static extern bool AttachThreadInput(uint a, uint b, bool attach);
[System.Runtime.InteropServices.DllImport("kernel32.dll")]
public static extern uint GetCurrentThreadId();
[System.Runtime.InteropServices.DllImport("user32.dll")]
public static extern void keybd_event(byte bVk, byte bScan, uint dwFlags, System.UIntPtr extra);
[System.Runtime.InteropServices.DllImport("user32.dll")]
public static extern void mouse_event(uint flags, int dx, int dy, uint data, System.UIntPtr extra);
[System.Runtime.InteropServices.DllImport("user32.dll")]
public static extern bool SetCursorPos(int x, int y);
[System.Runtime.InteropServices.DllImport("user32.dll")]
public static extern System.IntPtr GetForegroundWindow();
[System.Runtime.InteropServices.DllImport("user32.dll")]
public static extern bool GetWindowRect(System.IntPtr hWnd, out RECT r);
public struct RECT { public int L, T, R, B; }
'@ -ErrorAction Stop

$proc = Get-Process -Name $ProcessName -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $proc) { throw "找不到进程 $ProcessName（游戏没开？）" }
$targetPid = [uint32]$proc.Id

$found = New-Object System.Collections.Generic.List[object]
$cb = [W4.U+EnumProc]{
    param($hWnd, $lParam)
    $wpid = [uint32]0
    [void][W4.U]::GetWindowThreadProcessId($hWnd, [ref]$wpid)
    if ($wpid -eq $targetPid) {
        $cn = New-Object System.Text.StringBuilder 256
        [void][W4.U]::GetClassName($hWnd, $cn, 256)
        $tt = New-Object System.Text.StringBuilder 512
        [void][W4.U]::GetWindowText($hWnd, $tt, 512)
        $r = New-Object W4.U+RECT
        [void][W4.U]::GetWindowRect($hWnd, [ref]$r)
        $found.Add([pscustomobject]@{
            Handle=[int64]$hWnd; Class=$cn.ToString(); Title=$tt.ToString()
            Visible=[W4.U]::IsWindowVisible($hWnd); Width=$r.R-$r.L; Height=$r.B-$r.T
            L=$r.L; T=$r.T
        })
    }
    return $true
}
[void][W4.U]::EnumWindows($cb, [IntPtr]::Zero)

Write-Host "进程 $targetPid 的顶层窗口：" -ForegroundColor Cyan
$found | ForEach-Object { Write-Host ("  0x{0:X8}  {1,-20} {2,5}x{3,-5} vis={4,-5} '{5}'" -f $_.Handle,$_.Class,$_.Width,$_.Height,$_.Visible,$_.Title) }
if ($ListOnly) { return }

$win = $found | Where-Object { $_.Class -eq $PreferClass -and $_.Visible } | Sort-Object { $_.Width*$_.Height } -Descending | Select-Object -First 1
if (-not $win) { $win = $found | Where-Object { $_.Visible } | Sort-Object { $_.Width*$_.Height } -Descending | Select-Object -First 1 }
if (-not $win) { throw "该进程没有可见顶层窗口" }
Write-Host ("TARGET 0x{0:X8} class={1} {2}x{3}" -f $win.Handle,$win.Class,$win.Width,$win.Height) -ForegroundColor Green

$h  = [IntPtr]$win.Handle
$vk = @{ F1=0x70;F2=0x71;F3=0x72;F4=0x73;F5=0x74;F6=0x75;F7=0x76;F8=0x77;F9=0x78;F10=0x79;F11=0x7A;F12=0x7B }[$Key]

function Get-Fg { return [W4.U]::GetForegroundWindow() }

# 1) 常规抢前台 + AttachThreadInput
[void][W4.U]::ShowWindow($h, 9)
$fg = Get-Fg
$fgThread = [uint32]0
if ($fg -ne [IntPtr]::Zero) { [void][W4.U]::GetWindowThreadProcessId($fg, [ref]$fgThread) }
$myThread = [W4.U]::GetCurrentThreadId()
if ($fgThread -ne 0 -and $fgThread -ne $myThread) { [void][W4.U]::AttachThreadInput($myThread, $fgThread, $true) }
[void][W4.U]::BringWindowToTop($h)
[void][W4.U]::SetForegroundWindow($h)
if ($fgThread -ne 0 -and $fgThread -ne $myThread) { [void][W4.U]::AttachThreadInput($myThread, $fgThread, $false) }
Start-Sleep -Milliseconds 350

# 2) 还不行就点一下
if ((Get-Fg) -ne $h -and -not $NoClick) {
    Write-Host "  [i] SetForegroundWindow 未生效，改用鼠标点击激活" -ForegroundColor Yellow
    $cx = $win.L + [int]($win.Width * 0.5)
    $cy = $win.T + [int]($win.Height * 0.06)   # 贴顶，尽量只做「激活」不点到菜单项
    [void][W4.U]::SetCursorPos($cx, $cy)
    Start-Sleep -Milliseconds 150
    [W4.U]::mouse_event(0x0002, 0, 0, 0, [UIntPtr]::Zero)   # LEFTDOWN
    Start-Sleep -Milliseconds 80
    [W4.U]::mouse_event(0x0004, 0, 0, 0, [UIntPtr]::Zero)   # LEFTUP
    Start-Sleep -Milliseconds 350
}

$fgNow = Get-Fg
if ($fgNow -ne $h) {
    Write-Host ("[FAIL] 前台窗口是 0x{0:X8}，不是游戏 0x{1:X8}；按键会被丢掉" -f [int64]$fgNow, $win.Handle) -ForegroundColor Red
    Write-Host "       请手动点一下游戏窗口，然后重跑本命令" -ForegroundColor Red
    exit 2
}

[W4.U]::keybd_event([byte]$vk, 0, 0, [UIntPtr]::Zero)
Start-Sleep -Milliseconds 140
[W4.U]::keybd_event([byte]$vk, 0, 2, [UIntPtr]::Zero)
Write-Host "KEY $Key 已送达游戏窗口（前台校验通过）" -ForegroundColor Green
