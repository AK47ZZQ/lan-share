# LAN Share 环境矩阵 —— 沙箱内执行
#
# 由 run.ps1 调用。跑一组刻意刁难的安装环境，每个场景独立装-验-卸。
#
# 纪律：
#   · 一个场景只刁难一件事 —— 混在一起失败时说不清是谁的锅
#   · 每个场景都必须收尾卸载，否则下一个场景会被残留影响
#   · 任何一步失败都记录并继续下一个，不要中断整轮

$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"

# 沙箱里证书一律不校验：全程自签，校验没意义还会掩盖真正的问题
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    [Net.ServicePointManager]::ServerCertificateValidationCallback = { $true }
} catch { }

$ResultDir = "C:\result"
$SetupDir = "C:\pkg"
$Lines = New-Object System.Collections.Generic.List[string]

function Say([string]$s) {
    $line = "$(Get-Date -Format 'HH:mm:ss')  $s"
    Write-Host $line
    $Lines.Add($line)
}

function Flush {
    try {
        New-Item -ItemType Directory -Force -Path $ResultDir | Out-Null
        $Lines | Set-Content -Path (Join-Path $ResultDir "result.txt") -Encoding UTF8
    } catch { }
}

function Get-Setup {
    $exe = Get-ChildItem -Path $SetupDir -Filter "LANShare-Setup-*.exe" -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    return $exe
}

function Wait-Http([string]$url, [int]$seconds = 120) {
    $deadline = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $r = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 4
            if ($r.StatusCode -eq 200) { return $true }
        } catch { }
        Start-Sleep -Seconds 3
    }
    return $false
}

function Get-SvcState([string]$name) {
    $s = Get-Service -Name $name -ErrorAction SilentlyContinue
    if ($s) { return [string]$s.Status } else { return "NOT INSTALLED" }
}

function Test-Install([string]$label, [string]$dir, [string]$setup) {
    # 静止安装：/VERYSILENT 不弹窗，/SUPPRESSMSGBOXES 抑制脚本里的 MsgBox
    # （脚本 MsgBox 不受 /SILENT 控制，会一直等人点，自动安装永远不返回）
    $args = "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /DIR=`"$dir`""
    try {
        $p = Start-Process -FilePath $setup -ArgumentList $args -Wait -PassThru
        $code = $p.ExitCode
    } catch {
        return "安装进程启动失败：$($_.Exception.Message)"
    }
    Say "  [$label] 安装退出码 = $code"
    if ($code -ne 0) { return "安装失败，退出码 $code" }

    # 服务状态
    $svc = @{}
    foreach ($n in @("lanshare-api", "lanshare-caddy", "lanshare-postgresql")) {
        $svc[$n] = Get-SvcState $n
        Say "  [$label] $n = $($svc[$n])"
    }
    if ($svc["lanshare-api"] -ne "Running") {
        return "API 服务没起来（$($svc['lanshare-api'])）"
    }

    # 站点可达性（证书自签，前面已关校验）
    if (-not (Wait-Http "https://localhost:8765/healthz" 150)) {
        return "HTTPS 健康检查一直不通"
    }
    Say "  [$label] HTTPS 200 OK"

    # 数据目录可写 + 位置对不对
    $reg = Get-ItemProperty -Path "HKLM:\SOFTWARE\LANShare" -ErrorAction SilentlyContinue
    if (-not $reg) { return "注册表 HKLM\SOFTWARE\LANShare 没写入" }
    Say "  [$label] InstallFolder = $($reg.InstallFolder)"
    Say "  [$label] DataFolder    = $($reg.DataFolder)"
    $want = $dir.TrimEnd('\')
    if ($reg.InstallFolder.TrimEnd('\') -ne $want) {
        return "安装目录不符：期望 $want，实际 $($reg.InstallFolder)"
    }
    if (-not (Test-Path $reg.DataFolder)) { return "数据目录不存在：$($reg.DataFolder)" }
    try {
        $probe = Join-Path $reg.DataFolder ".matrix-write-test"
        "ok" | Set-Content -Path $probe -Encoding UTF8
        Remove-Item $probe -Force
    } catch { return "数据目录不可写：$($_.Exception.Message)" }

    return "PASS"
}

function Test-Uninstall([string]$label, [string]$setup, [bool]$deleteData) {
    $args = "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART"
    if ($deleteData) { $args += " /DELETE_DATA" }
    try {
        $p = Start-Process -FilePath $setup -ArgumentList $args -Wait -PassThru
        Say "  [$label] 卸载退出码 = $($p.ExitCode)"
    } catch {
        return "卸载进程启动失败：$($_.Exception.Message)"
    }

    # Inno 有 /SECONDPHASE 第二进程，Start-Process -Wait 只等第一阶段
    Start-Sleep -Seconds 20

    $left = @()
    foreach ($n in @("lanshare-api", "lanshare-caddy", "lanshare-postgresql")) {
        $st = Get-SvcState $n
        if ($st -ne "NOT INSTALLED") { $left += "服务 $n 还在（$st）" }
    }
    if (Test-Path "HKLM:\SOFTWARE\LANShare") { $left += "注册表键还在" }
    $fw = Get-NetFirewallRule -DisplayName "*LAN Share*" -ErrorAction SilentlyContinue
    if ($fw) { $left += "防火墙规则还在（$($fw.Count) 条）" }

    if ($left.Count -gt 0) { return "有残留：$($left -join '；')" }
    return "PASS"
}

# ────────────────────────────── 主流程 ──────────────────────────────
Say "===== LAN Share 环境矩阵 ====="
Say "系统：$([System.Environment]::OSVersion.VersionString)"

$setup = Get-Setup
if (-not $setup) {
    Say "FATAL: C:\pkg 下没有 LANShare-Setup-*.exe"
    Flush
    shutdown /s /t 3 /f
    exit 1
}
Say "安装包：$($setup.Name)  $([math]::Round($setup.Length/1MB,1)) MB"

$summary = @()

# ── 1. Program Files（默认位） ──────────────────────────────
Say ""
Say "--- 场景 1: Program Files ---"
$r = Test-Install "default" "C:\Program Files\LAN Share" $setup.FullName
if ($r -eq "PASS") {
    $u = Test-Uninstall "default" $setup.FullName $true
    if ($u -ne "PASS") { $r = $u }
}
$summary += "default(Program Files)          : $r"

# ── 2. 路径含空格 ──────────────────────────────────────────
Say ""
Say "--- 场景 2: 路径含空格 ---"
$r = Test-Install "space" "C:\LAN Share Test" $setup.FullName
if ($r -eq "PASS") {
    $u = Test-Uninstall "space" $setup.FullName $true
    if ($u -ne "PASS") { $r = $u }
}
$summary += "space(C:\LAN Share Test)        : $r"

# ── 3. 中文路径 ────────────────────────────────────────────
Say ""
Say "--- 场景 3: 中文路径 ---"
$r = Test-Install "cjk" "C:\网盘测试" $setup.FullName
if ($r -eq "PASS") {
    $u = Test-Uninstall "cjk" $setup.FullName $true
    if ($u -ne "PASS") { $r = $u }
}
$summary += "cjk(C:\网盘测试)                : $r"

# ── 4. 非系统盘（subst 造真盘符）────────────────────────────
Say ""
Say "--- 场景 4: D 盘 ---"
$mounted = $false
try {
    subst D: C:\ 2>$null
    $mounted = $true
} catch { }
if ($mounted) {
    $r = Test-Install "drive" "D:\LANShare" $setup.FullName
    if ($r -eq "PASS") {
        # 装到 D 盘时，C 盘的 Program Files / ProgramData 不该被写
        if (Test-Path "C:\Program Files\LAN Share") { $r = "C 盘 Program Files 被写入" }
        if (Test-Path "C:\ProgramData\LANShare") { $r = "C 盘 ProgramData 被写入" }
    }
    if ($r -eq "PASS") {
        $u = Test-Uninstall "drive" $setup.FullName $true
        if ($u -ne "PASS") { $r = $u }
    }
    subst D: /D 2>$null
} else {
    $r = "SKIP（subst 不可用）"
}
$summary += "drive(D:\LANShare)              : $r"

# ── 5. 端口被占（必须明确失败，不能装作成功）──────────────
Say ""
Say "--- 场景 5: 端口被抢占 ---"
$squat = $null
try {
    $squat = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, 8766)
    $squat.Start()
    $args = "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /DIR=`"C:\PortBusy`""
    $p = Start-Process -FilePath $setup.FullName -ArgumentList $args -Wait -PassThru
    Say "  [portbusy] 安装退出码 = $($p.ExitCode)"
    # 端口被占时安装应当失败（非 0）或至少不注册服务
    $apiState = Get-SvcState "lanshare-api"
    if ($p.ExitCode -eq 0 -and $apiState -eq "Running") {
        $r = "FAIL：端口被占仍然报成功"
    } else {
        $r = "PASS（安装如实失败，退出码 $($p.ExitCode)，服务 $apiState）"
    }
} catch {
    $r = "测试自身失败：$($_.Exception.Message)"
} finally {
    if ($squat) { try { $squat.Stop() } catch { } }
}
# 清理可能的半装
try {
    $u = Test-Uninstall "portbusy" $setup.FullName $true
} catch { }
$summary += "portbusy(8766 被占)             : $r"

# ── 6. 卸载零残留 ──────────────────────────────────────────
Say ""
Say "--- 场景 6: 卸载零残留 ---"
$r = Test-Install "uninst" "C:\LANShareUninst" $setup.FullName
if ($r -eq "PASS") {
    $r = Test-Uninstall "uninst" $setup.FullName $true
    if ($r -eq "PASS") {
        if (Test-Path "C:\LANShareUninst") {
            $left = Get-ChildItem "C:\LANShareUninst" -Recurse -ErrorAction SilentlyContinue |
                Select-Object -First 5 -ExpandProperty Name
            $r = "PASS（安装目录还在，剩：$($left -join ', ')）"
        }
    }
}
$summary += "uninstall(零残留)               : $r"

# ────────────────────────────── 汇总 ──────────────────────────────
Say ""
Say "===== 汇总 ====="
foreach ($s in $summary) { Say $s }
$failCount = ($summary | Where-Object { $_ -notmatch ": PASS" }).Count
Say ""
if ($failCount -eq 0) {
    Say "全部通过"
} else {
    Say "$failCount 个场景未通过"
}
Flush

Start-Sleep -Seconds 2
shutdown /s /t 3 /f
