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
    Write-Host "! 图标不存在: $IconPath（先跑 python scripts/make-desktop-icon.py）" -ForegroundColor Yellow
    $IconPath = $null
}

# 源文件一起编；顺序无所谓
$Sources = @(
    (Join-Path $Here "LanShareCore.cs"),
    (Join-Path $Here "UiKit.cs"),
    (Join-Path $Here "LanSharePanel.cs"),
    (Join-Path $Here "WebPanel.cs"),
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
# WebView2 的 .NET Framework 引用（NuGet 包里 lib/net462 的产物）。
#
# 只在托盘控制面板里用得上：UI 是外置的 ui/index.html，C# 只做数据桥接。
# 三个 dll 都要拷到输出目录 —— Core 是托管包装，Loader 是原生引导，
# 少了任何一个启动时都会抛「找不到 WebView2」。
$libDir = Join-Path $Here "lib"
$wv2Refs = @()
foreach ($dll in @("Microsoft.Web.WebView2.Core.dll", "Microsoft.Web.WebView2.WinForms.dll")) {
    $f = Join-Path $libDir $dll
    if (Test-Path $f) {
        $wv2Refs += "/r:$f"
    } else {
        Write-Host "警告：缺少 $dll，WebView2 面板将不可用（会回退到 WinForms 版）" -ForegroundColor Yellow
    }
}

$iconArgs = @()
if ($IconPath) {
    $iconArgs = @("/win32icon:$IconPath", "/resource:$IconPath,LanShareTray.lanshare.ico")

    # 另外把 128px PNG 单独嵌进去 —— 控制面板顶栏要把它当 data URI 传给 HTML。
    # 不直接从 ICO 里解：GDI+ 对多帧 ICO 调 ToBitmap() 会抛
    # 「请求的范围扩展超过了数组的结尾」，试过指定尺寸帧也一样，
    # 干脆在构建期就准备好一张干净的 PNG。
    # $PSScriptRoot = <repo>\installer	ray，要上溯两级才到 <repo>\logo
    $logoPng = Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) "logo\lanshare-256.png"
    if (Test-Path $logoPng) {
        $iconArgs += "/resource:$logoPng,LanShareTray.Logo.png"
    } else {
        Write-Host "警告：找不到 logo\lanshare-256.png，界面顶栏图标会隐藏" -ForegroundColor Yellow
    }
}

# 托盘四态图标：由 scripts/make-desktop-icon.py 生成（和手机 App 同一套绘制代码）。
#
# 为什么嵌入 PNG 而不是让 C# 现画：
# 之前 C# 里自己画云（三个 g.FillEllipse 大概拼），和 App 的 SVG 描边云
# 完全不是一回事，用户一眼就看出「电脑上那个丑」。现在两边共用
# scripts/brandmark.py，C# 只负责把 PNG 转成 Icon，不参与绘图。
#
# 为什么带 alpha 的 PNG 能转成 Icon 需要 CreateIconIndirect：
# Bitmap.GetHicon() 会丢掉 alpha 通道，圆角变成方块、边缘变成黑边。
$LogoSrc = Join-Path (Split-Path -Parent (Split-Path -Parent $Here)) "logo"
$trayStates = @(
    @{ File = "lanshare-tray-32.png";       Res = "LanShareTray.tray-none.png" },
    @{ File = "lanshare-tray-32-ok.png";    Res = "LanShareTray.tray-ok.png" },
    @{ File = "lanshare-tray-32-warn.png";  Res = "LanShareTray.tray-warn.png" },
    @{ File = "lanshare-tray-32-error.png"; Res = "LanShareTray.tray-error.png" }
)
$missing = @()
foreach ($t in $trayStates) {
    $f = Join-Path $LogoSrc $t.File
    if (Test-Path $f) {
        $iconArgs += "/resource:$f,$($t.Res)"
    } else {
        $missing += $t.File
    }
}
if ($missing.Count -gt 0) {
    Write-Host ("警告：托盘图标缺失，程序会回退到旧画法：" + ($missing -join ", ")) -ForegroundColor Yellow
    Write-Host "      修：python scripts/make-desktop-icon.py" -ForegroundColor Yellow
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
    $wv2Refs `
    /optimize+ `
    /warn:4 `
    $iconArgs `
    $manifestArgs `
    $Sources

if ($LASTEXITCODE -ne 0) {
    Write-Host "编译失败（exit $LASTEXITCODE）" -ForegroundColor Red
    exit 1
}

# WebView2 的托管 dll 和原生 Loader 必须跟 exe 放一起。
# Loader 由 Core.dll 在运行时用 P/Invoke 找，找不到就抛异常 → 走回退分支。
$binDir = Split-Path -Parent $Out
foreach ($f in @("Microsoft.Web.WebView2.Core.dll",
                 "Microsoft.Web.WebView2.WinForms.dll",
                 "WebView2Loader.dll")) {
    $src = Join-Path $libDir $f
    if (Test-Path $src) { Copy-Item $src $binDir -Force }
}

# ui/ 是外置的 HTML —— 单独放，改界面不用重新编译
$uiSrc = Join-Path $Here "ui"
$uiDst = Join-Path $binDir "ui"
if (Test-Path $uiSrc) {
    New-Item -ItemType Directory -Path $uiDst -Force | Out-Null
    Copy-Item (Join-Path $uiSrc "*") $uiDst -Recurse -Force
    Write-Host "  ui/ 已同步到输出目录" -ForegroundColor DarkGray
}

$size = [math]::Round((Get-Item $Out).Length / 1KB, 1)
Write-Host "  OK  $Out  ($size KB)" -ForegroundColor Green
