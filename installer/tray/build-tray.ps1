# LAN Share 托盘 + 控制面板构建脚本
#
# 用 Windows 自带的 csc.exe（.NET Framework 4.x 编译器）直接编译，
# 不需要 Visual Studio、不需要 .NET SDK、不需要 NuGet。
#
# 用法：  powershell -ExecutionPolicy Bypass -File installer\tray\build-tray.ps1
#
# 产物：bin\LanShareTray.exe —— 单文件，约 35 KB
#       Win10/11 自带 .NET Framework 4.8 运行时，无需额外安装任何东西。
#
# ⚠️ 本文件必须 UTF-8 BOM + CRLF，否则 PowerShell 5.1 按 GBK 读会语法崩。

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$Here   = Split-Path -Parent $MyInvocation.MyCommand.Path
$OutDir = Join-Path $Here "bin"
$Out    = Join-Path $OutDir "LanShareTray.exe"

# 应用图标：logo\lanshare.ico（由 scripts/make_icon.py 生成）
# /win32icon   → 资源管理器、任务栏、Alt+Tab 显示这个图标
# /resource    → 嵌进程序集，托盘和窗口用 new Icon(stream) 取，不依赖外部文件
$IconPath = Join-Path (Split-Path -Parent (Split-Path -Parent $Here)) "logo\lanshare.ico"
if (-not (Test-Path $IconPath)) {
    Write-Host "! 图标不存在: $IconPath（先跑 python scripts/make_icon.py）" -ForegroundColor Yellow
    $IconPath = $null
}

# 源文件一起编；顺序无所谓
$Sources = @(
    (Join-Path $Here "LanShareCore.cs"),
    (Join-Path $Here "UiKit.cs"),
    (Join-Path $Here "LanSharePanel.cs"),
    (Join-Path $Here "LanShareTray.cs")
)

foreach ($s in $Sources) {
    if (-not (Test-Path $s)) {
        Write-Host "找不到源文件: $s" -ForegroundColor Red
        exit 1
    }
}

# 找 csc.exe：64 位优先
$csc = $null
foreach ($p in @(
    "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe",
    "$env:WINDIR\Microsoft.NET\Framework\v4.0.30319\csc.exe"
)) {
    if (Test-Path $p) { $csc = $p; break }
}
if (-not $csc) {
    Write-Host "找不到 csc.exe。需要 .NET Framework 4.x（Win10 自带）。" -ForegroundColor Red
    exit 1
}

New-Item -ItemType Directory -Path $OutDir -Force | Out-Null

Write-Host "编译 $($Sources.Count) 个源文件 ..." -ForegroundColor Cyan

# 必需的额外引用：
#   System.ServiceProcess  —— 服务启停（ServiceController）
#   System.Web.Extensions  —— JavaScriptSerializer（解析 CLI 的 JSON 输出）
$iconArgs = @()
if ($IconPath) {
    $iconArgs = @("/win32icon:$IconPath", "/resource:$IconPath,LanShareTray.lanshare.ico")
}

# DPI 感知写在清单里，不靠运行时调 SetProcessDpiAwarenessContext ——
# 那个 API 要求在本进程创建任何 UI 之前调用，实际跑了十几轮发现时机不稳定，
# 同一份代码有时生效有时静默失败，失败时窗口被系统当 96 DPI 位图拉伸 1.5 倍，
# 字全糊。清单由加载器在读 PE 时就处理，没有这个问题。
$ManifestPath = Join-Path $PSScriptRoot "app.manifest"
$manifestArgs = @()
if (Test-Path $ManifestPath) {
    $manifestArgs = @("/win32manifest:$ManifestPath")
} else {
    Write-Host "警告：app.manifest 不存在，DPI 感知可能失效" -ForegroundColor Yellow
}

& $csc /target:winexe /out:$Out `
    /reference:System.dll `
    /reference:System.Core.dll `
    /reference:System.Windows.Forms.dll `
    /reference:System.Drawing.dll `
    /reference:System.ServiceProcess.dll `
    /reference:System.Web.Extensions.dll `
    /optimize+ `
    /warn:4 `
    $iconArgs `
    $manifestArgs `
    $Sources

if ($LASTEXITCODE -ne 0) {
    Write-Host "编译失败（exit $LASTEXITCODE）" -ForegroundColor Red
    exit 1
}

$size = [math]::Round((Get-Item $Out).Length / 1KB, 1)
Write-Host "  OK  $Out  ($size KB)" -ForegroundColor Green
