# LAN Share 托盘程序构建脚本
#
# 用 Windows 自带的 csc.exe（.NET Framework 4.x 编译器）直接编译，
# 不需要 Visual Studio、不需要 .NET SDK。
#
# 用法：  powershell -ExecutionPolicy Bypass -File installer\tray\build-tray.ps1

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Src  = Join-Path $Here "LanShareTray.cs"
$OutDir = Join-Path $Here "bin"
$Out  = Join-Path $OutDir "LanShareTray.exe"

if (-not (Test-Path $Src)) { Write-Host "找不到源文件: $Src" -ForegroundColor Red; exit 1 }

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

Write-Host "编译 LanShareTray.cs ..." -ForegroundColor Cyan
& $csc /target:winexe /out:$Out `
    /reference:System.dll `
    /reference:System.Windows.Forms.dll `
    /reference:System.Drawing.dll `
    /optimize+ `
    $Src

if ($LASTEXITCODE -ne 0) {
    Write-Host "编译失败（exit $LASTEXITCODE）" -ForegroundColor Red
    exit 1
}

$size = [math]::Round((Get-Item $Out).Length / 1KB, 1)
Write-Host "  OK  $Out  ($size KB)" -ForegroundColor Green
