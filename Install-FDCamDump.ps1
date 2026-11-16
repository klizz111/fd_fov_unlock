<#
    Install-FDCamDump.ps1
    ------------------------------------------------------------------
    将 UE4SS v4.0.0（支持 UE 5.7）装进 Fallen Doll，并部署 FDCamDump 抓取 mod。

    用法：
        pwsh -File .\Install-FDCamDump.ps1
        pwsh -File .\Install-FDCamDump.ps1 -GameRoot '<GAME_DIR>' -ZipPath '.\downloads\UE4SS_v4.0.0.zip'

    幂等：重复运行只会覆盖 ue4ss\ 与增强的 ini 项，并在覆盖前把旧文件挪到 _backup_<时间戳>。
#>
[CmdletBinding()]
param(
    [string]$GameRoot  = '',      # 空 = 自动探测（kit 放在游戏目录里就用 PSScriptRoot，否则读 FDMOD_GAME_DIR）
    [string]$ZipPath   = (Join-Path $PSScriptRoot 'downloads\UE4SS_v4.0.0.zip'),
    [string]$ExpectSha = '251ad0d1381a241750e036f5918cbf2b5d7ec58ebc608cb2dd09d459d0c81e45'
)

$ErrorActionPreference = 'Stop'
function Say($m, $c = 'Gray') { Write-Host $m -ForegroundColor $c }

if (-not $GameRoot) {
    if (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'Paralogue\Binaries\Win64')) { $GameRoot = $PSScriptRoot }
    elseif ($env:FDMOD_GAME_DIR) { $GameRoot = $env:FDMOD_GAME_DIR }
    else { throw '未指定游戏目录：请传 -GameRoot <游戏根目录>，或设置环境变量 FDMOD_GAME_DIR' }
}

$Win64 = Join-Path $GameRoot 'Paralogue\Binaries\Win64'
$Exe   = Join-Path $Win64 'Paralogue-Win64-Shipping.exe'
$ModSrc = Join-Path $PSScriptRoot 'mod\FDCamDump'

Say "== 前置检查 ==" 'Cyan'
if (-not (Test-Path -LiteralPath $Exe)) { throw "找不到游戏 exe：$Exe" }
if (-not (Test-Path -LiteralPath $ZipPath)) { throw "找不到 UE4SS 压缩包：$ZipPath`n   从 https://github.com/UE4SS-RE/RE-UE4SS/releases/tag/v4.0.0 下载 UE4SS_v4.0.0.zip 放到 downloads\ 下" }
if (-not (Test-Path -LiteralPath $ModSrc)) { throw "找不到 mod 源目录：$ModSrc" }

$running = (Get-Process -Name 'Paralogue-Win64-Shipping' -ErrorAction SilentlyContinue)
if ($running) { throw '游戏正在运行，请先完全退出（含后台进程），否则 DLL 被占用无法写入。' }

$sha = (Get-FileHash -LiteralPath $ZipPath -Algorithm SHA256).Hash.ToLower()
Say ("zip sha256 = " + $sha)
if ($ExpectSha -and $sha -ne $ExpectSha.ToLower()) { throw "压缩包 SHA-256 与预期不符，拒绝安装。`n  预期 $ExpectSha`n  实际 $sha" }
Say 'zip 校验通过（与 GitHub v4.0.0 发布页 digest 一致）' 'Green'

Say "`n== 解包 ==" 'Cyan'
$tmp = Join-Path $env:TEMP ("fd_ue4ss_" + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Path $tmp | Out-Null
Expand-Archive -LiteralPath $ZipPath -DestinationPath $tmp -Force
foreach ($p in 'dwmapi.dll', 'ue4ss\UE4SS.dll', 'ue4ss\UE4SS-settings.ini') {
    if (-not (Test-Path -LiteralPath (Join-Path $tmp $p))) { throw "压缩包结构不符合 v4.0.0 预期，缺少 $p" }
}
Say "解包到 $tmp"

Say "`n== 备份旧装置 ==" 'Cyan'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$bak = Join-Path $Win64 ("_ue4ss_backup_" + $stamp)
$needBak = $false
foreach ($p in 'dwmapi.dll', 'ue4ss') {
    if (Test-Path -LiteralPath (Join-Path $Win64 $p)) { $needBak = $true }
}
if ($needBak) {
    New-Item -ItemType Directory -Path $bak | Out-Null
    foreach ($p in 'dwmapi.dll', 'ue4ss') {
        $src = Join-Path $Win64 $p
        if (Test-Path -LiteralPath $src) { Copy-Item -LiteralPath $src -Destination $bak -Recurse -Force }
    }
    Say "旧文件已备份到 $bak" 'Yellow'
} else {
    Say '无旧装置，跳过备份'
}

Say "`n== 复制 UE4SS ==" 'Cyan'
Copy-Item -LiteralPath (Join-Path $tmp 'dwmapi.dll') -Destination $Win64 -Force
$dstUe4ss = Join-Path $Win64 'ue4ss'
if (Test-Path -LiteralPath $dstUe4ss) { Remove-Item -LiteralPath $dstUe4ss -Recurse -Force }
Copy-Item -LiteralPath (Join-Path $tmp 'ue4ss') -Destination $dstUe4ss -Recurse -Force
Say "dwmapi.dll -> $Win64"
Say "ue4ss\      -> $dstUe4ss"

Say "`n== 配置 UE4SS-settings.ini ==" 'Cyan'
$ini = Join-Path $dstUe4ss 'UE4SS-settings.ini'
$txt = Get-Content -LiteralPath $ini -Raw
function Set-IniValue([string]$text, [string]$key, [string]$val) {
    # 只用 [ \t] 匹配空白：\s 会吃掉换行，把下一行整行吞掉（上一版就踩了这个坑）
    $pat = "(?m)^([ \t]*" + [regex]::Escape($key) + "[ \t]*=[ \t]*)([^\r\n]*)"
    if (-not [regex]::IsMatch($text, $pat)) {
        throw "settings.ini 里找不到键 $key（UE4SS 版本不符？）"
    }
    return [regex]::Replace($text, $pat, { param($m) $m.Groups[1].Value + $val })
}
$txt = Set-IniValue $txt 'MajorVersion' '5'
$txt = Set-IniValue $txt 'MinorVersion' '7'
$txt = Set-IniValue $txt 'ConsoleEnabled' '1'
$txt = Set-IniValue $txt 'GuiConsoleEnabled' '1'
$txt = Set-IniValue $txt 'GuiConsoleVisible' '1'
# 引擎版本仍以自动探测为准更稳，这里只在探测失败时才生效；顺便放开扫描时限
$txt = Set-IniValue $txt 'SecondsToScanBeforeGivingUp' '180'
Set-Content -LiteralPath $ini -Value $txt -Encoding UTF8 -NoNewline
Say 'EngineVersionOverride = 5 / 7 ；ConsoleEnabled = 1 ；GuiConsole = 1'

Say "`n== 部署 FDCamDump mod ==" 'Cyan'
$modDst = Join-Path $dstUe4ss 'Mods\FDCamDump'
if (Test-Path -LiteralPath $modDst) { Remove-Item -LiteralPath $modDst -Recurse -Force }
Copy-Item -LiteralPath $ModSrc -Destination $modDst -Recurse -Force

$modsTxt = Join-Path $dstUe4ss 'Mods\mods.txt'
$lines = Get-Content -LiteralPath $modsTxt
if (-not ($lines | Where-Object { $_ -match '^\s*FDCamDump\s*:' })) {
    $idx = 0
    for ($i = 0; $i -lt $lines.Count; $i++) { if ($lines[$i] -match '^\s*;') { $idx = $i; break } }
    if ($idx -le 0) { $lines = @($lines) + 'FDCamDump : 1' }
    else {
        $lines = @($lines[0..($idx - 1)]) + 'FDCamDump : 1' + @($lines[$idx..($lines.Count - 1)])
    }
    Set-Content -LiteralPath $modsTxt -Value $lines -Encoding UTF8
    Say 'mods.txt 已加入 FDCamDump : 1'
} else {
    Say 'mods.txt 已包含 FDCamDump'
}

# 输出目录（Lua 里用绝对路径写盘）
$dumpDir = Join-Path $Win64 'FDCamDump'
New-Item -ItemType Directory -Force -Path $dumpDir | Out-Null
Say "抓取输出目录：$dumpDir"

Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue

Say "`n== 安装完成 ==" 'Green'
Say "下一步：双击游戏根目录的「启动游戏.bat」。" 'White'
Say "  · 控制台窗口出现 [FDCamDump] loaded 即注入成功" 'White'
Say "  · 启动后 +75s / +105s 自动抓一轮；进到场景后按 F8 可随时再抓" 'White'
Say "  · 产物在 $dumpDir" 'White'
