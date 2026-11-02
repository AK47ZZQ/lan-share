# LAN Share 环境矩阵 —— 宿主侧启动器
#
# 生成 .wsb、拉起 Windows Sandbox、等结果。
# 沙箱内的逻辑全在 inside.ps1。

param(
    [string]$Setup = "",
    [switch]$KeepOpen
)

$ErrorActionPreference = "Stop"

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Repo = (Resolve-Path (Join-Path $Here "..\..")).Path

# ── 找安装包 ────────────────────────────────────────────────
if ([string]::IsNullOrWhiteSpace($Setup)) {
    $outDir = Join-Path $Repo "build\out"
    $cand = Get-ChildItem -Path $outDir -Filter "LANShare-Setup-*.exe" -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $cand) {
        Write-Host "找不到安装包。先跑 installer\build.ps1 + ISCC，或者用 -Setup 指定。" -ForegroundColor Red
        exit 1
    }
    $Setup = $cand.FullName
}
$Setup = (Resolve-Path $Setup).Path
Write-Host "安装包：$Setup" -ForegroundColor Cyan

# ── 准备结果目录 ────────────────────────────────────────────
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$resultDir = Join-Path $Here "runs\$stamp"
New-Item -ItemType Directory -Force -Path $resultDir | Out-Null

# ── 生成 .wsb ───────────────────────────────────────────────
# 沙箱内需要：
#   C:\pkg     ← 安装包所在目录（只读）
#   C:\matrix  ← inside.ps1（只读）
#   C:\result  ← 结果回写（可写）
$pkgDir = Split-Path -Parent $Setup
$setupName = Split-Path -Leaf $Setup

$wsb = @"
<Configuration>
  <Networking>Enable</Networking>
  <MappedFolders>
    <MappedFolder>
      <HostFolder>$pkgDir</HostFolder>
      <SandboxFolder>C:\pkg</SandboxFolder>
      <ReadOnly>true</ReadOnly>
    </MappedFolder>
    <MappedFolder>
      <HostFolder>$Here</HostFolder>
      <SandboxFolder>C:\matrix</SandboxFolder>
      <ReadOnly>true</ReadOnly>
    </MappedFolder>
    <MappedFolder>
      <HostFolder>$resultDir</HostFolder>
      <SandboxFolder>C:\result</SandboxFolder>
      <ReadOnly>false</ReadOnly>
    </MappedFolder>
  </MappedFolders>
  <LogonCommand>
    <Command>powershell -NoProfile -ExecutionPolicy Bypass -File C:\matrix\inside.ps1</Command>
  </LogonCommand>
</Configuration>
"@

$wsbPath = Join-Path $resultDir "matrix.wsb"
$wsb | Set-Content -Path $wsbPath -Encoding UTF8
Write-Host "沙箱配置：$wsbPath" -ForegroundColor Cyan
Write-Host "结果目录：$resultDir" -ForegroundColor Cyan

# ── 启动 ────────────────────────────────────────────────────
Write-Host ""
Write-Host "拉起 Windows Sandbox（跑完会自动关机）…" -ForegroundColor Yellow

# 直接用 WindowsSandbox.exe，别用 Start-Process 关联 .wsb
# —— 关联方式会弹「选择打开方式」对话框，卡在那里等人点
$sandboxExe = Join-Path $env:WINDIR "System32\WindowsSandbox.exe"
$proc = Start-Process -FilePath $sandboxExe -ArgumentList $wsbPath -PassThru

Write-Host "沙箱进程 PID=$($proc.Id)，等结果…" -ForegroundColor Yellow
Write-Host "（跑完沙箱会自动关机。中途要停就关掉沙箱窗口。）"
Write-Host ""

$resultFile = Join-Path $resultDir "result.txt"
$deadline = (Get-Date).AddMinutes(45)
$done = $false
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 10
    if (Test-Path $resultFile) {
        # result.txt 是边跑边刷的，等它出现「汇总」再算完
        $txt = Get-Content $resultFile -Raw -ErrorAction SilentlyContinue
        if ($txt -and $txt -match "汇总") { $done = $true; break }
    }
    if ($proc.HasExited -and -not (Test-Path $resultFile)) { break }
}

Write-Host ""
if ($done) {
    Write-Host "===== 结果 =====" -ForegroundColor Green
    Get-Content $resultFile | ForEach-Object { Write-Host $_ }
} else {
    Write-Host "没等到结果（超时或沙箱被提前关掉）。" -ForegroundColor Red
    Write-Host "看看 $resultDir 里有没有 result.txt" -ForegroundColor Yellow
}

# ── 收尾：强制结束沙箱进程 ──────────────────────────────────
# 用户明确要求过：不强制结束会弹「意外关闭」提示，还长期占着进程
if (-not $KeepOpen) {
    Get-Process -Name "WindowsSandbox*", "WindowsSandboxClient",
        "WindowsSandboxRemoteSession" -ErrorAction SilentlyContinue |
        Stop-Process -Force -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "结果已存：$resultFile" -ForegroundColor Cyan
