# LAN Share 安装包构建脚本
#
# 作用：把散落在仓库各处的产物收进 build\dist\，让 installer\lanshare.iss 能编译。
#
# 用法（在仓库根目录，PowerShell）：
#     .\installer\build.ps1
#     .\installer\build.ps1 -SkipFrontend      # 前端已构建过，跳过 npm
#     .\installer\build.ps1 -SkipDeps          # 依赖已装过，跳过 pip
#
# 产物：
#     build\dist\runtime\     embeddable Python + Lib\site-packages
#     build\dist\app\         后端源码
#     build\dist\web\dist\    前端产物
#     build\dist\bin\         caddy.exe + nssm.exe + 图标
#     build\dist\conf\        Caddyfile
#     build\out\              最终安装包（iscc 输出）
#
# 踩过的坑（别重犯）：
#   1. pip 的 --target 必须用 Windows 路径（C:\...），不能用 MSYS 的 /c/...，
#      否则会静默装到 C:\c\Users\... 去，pip 还报 Successfully installed。
#   2. 服务入口是 app.main:app，不是 app.asgi:app（后者全路由 404）。
#   3. embeddable Python 的 python311._pth 必须启用 import site 并加 Lib\site-packages。

[CmdletBinding()]
param(
    [switch]$SkipFrontend,
    [switch]$SkipDeps,
    [switch]$CleanRuntime,
    [string]$PythonVersion = "3.11.9",
    [string]$PipMirror = "https://mirrors.aliyun.com/pypi/simple/",
    [string]$PythonMirror = "https://mirrors.huaweicloud.com/python"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Build = Join-Path $Root "build"
$Dist = Join-Path $Build "dist"

function Step($msg) { Write-Host "`n=== $msg ===" -ForegroundColor Cyan }
function Ok($msg)   { Write-Host "  OK  $msg" -ForegroundColor Green }
function Fail($msg) { Write-Host "  FAIL $msg" -ForegroundColor Red; exit 1 }

Set-Location $Root
Write-Host "仓库根目录: $Root"

# ────────────────────────────────────────────────────────────
Step "0. 清理旧产物（保留 runtime，重装依赖太贵）"
if ($CleanRuntime -and (Test-Path (Join-Path $Dist "runtime"))) {
    Remove-Item (Join-Path $Dist "runtime") -Recurse -Force
    Write-Host "  -CleanRuntime: runtime 已删" -ForegroundColor Yellow
}
if (Test-Path $Dist) {
    # runtime 有 87 MB、装一次要 1-2 分钟，重建时保留它。
    # 想彻底重来：删掉整个 build\dist 或加 -CleanRuntime
    Get-ChildItem $Dist -Directory | Where-Object { $_.Name -ne "runtime" } | Remove-Item -Recurse -Force
    Get-ChildItem $Dist -File -ErrorAction SilentlyContinue | Remove-Item -Force
    Ok "已清理（runtime 保留）"
} else {
    New-Item -ItemType Directory -Path $Dist -Force | Out-Null
    Ok "build\dist 已创建"
}
New-Item -ItemType Directory -Path (Join-Path $Build "out") -Force | Out-Null

# ────────────────────────────────────────────────────────────
Step "1. 前端构建"
$webDist = Join-Path $Root "web\dist"
if ($SkipFrontend -and (Test-Path (Join-Path $webDist "index.html"))) {
    Ok "跳过（-SkipFrontend 且产物已存在）"
} else {
    Push-Location (Join-Path $Root "web")
    try {
        if (-not (Test-Path "node_modules")) {
            Write-Host "  安装前端依赖..."
            npm ci
            if ($LASTEXITCODE -ne 0) { Fail "npm ci 失败" }
        }
        npm run build
        if ($LASTEXITCODE -ne 0) { Fail "npm run build 失败" }
    } finally { Pop-Location }
    if (-not (Test-Path (Join-Path $webDist "index.html"))) { Fail "前端产物缺失" }
    Ok "前端构建完成"
}

# ────────────────────────────────────────────────────────────
Step "2. 下载 embeddable Python $PythonVersion"
$runtime = Join-Path $Dist "runtime"
New-Item -ItemType Directory -Path $runtime -Force | Out-Null
$embedZip = Join-Path $Build "python-embed.zip"

if (-not (Test-Path $embedZip)) {
    $url = "$PythonMirror/$PythonVersion/python-$PythonVersion-embed-amd64.zip"
    Write-Host "  下载: $url"
    try {
        Invoke-WebRequest -Uri $url -OutFile $embedZip -UseBasicParsing -TimeoutSec 300
    } catch {
        Fail "下载失败: $_`n  手动下载后放到 $embedZip"
    }
}
$sizeMB = [math]::Round((Get-Item $embedZip).Length / 1MB, 1)
if ($sizeMB -lt 5) { Fail "下载的文件只有 $sizeMB MB，不像完整的 embeddable 包" }
Ok "python-embed.zip ($sizeMB MB)"

Expand-Archive -Path $embedZip -DestinationPath $runtime -Force
Ok "解压到 build\dist\runtime"

# ────────────────────────────────────────────────────────────
Step "3. 修 _pth（embeddable 的关键，不修就 import 不到依赖）"
$pth = Join-Path $runtime "python311._pth"
if (-not (Test-Path $pth)) { Fail "找不到 $pth" }
# `.` 是 runtime 目录（python.exe 所在处）；`..` 是安装根。
# 必须加 `..`：embeddable Python 的 _pth 是隔离模式，不读 PYTHONPATH、不读 cwd，
# 所以 `python -m lanshare_cli` 和 `import app` 全靠这里的路径。
# 漏了 `..` 的症状：No module named lanshare_cli（2026-10-30 实测踩过）。
$pthContent = @"
python311.zip
.
Lib\site-packages
..
import site
"@
Set-Content -Path $pth -Value $pthContent -Encoding ASCII
New-Item -ItemType Directory -Path (Join-Path $runtime "Lib\site-packages") -Force | Out-Null
Ok "python311._pth 已改（Lib\site-packages + import site）"

# ────────────────────────────────────────────────────────────
Step "4. 安装运行时依赖到 embeddable"
$sitePackages = Join-Path $runtime "Lib\site-packages"
if ($SkipDeps -and (Test-Path (Join-Path $sitePackages "fastapi"))) {
    Ok "跳过（-SkipDeps 且依赖已存在）"
} else {
    $reqFile = Join-Path $Root "installer\requirements-runtime.txt"
    if (-not (Test-Path $reqFile)) { Fail "找不到 $reqFile" }

    # 关键：用系统 Python 的 pip，--target 传 Windows 绝对路径
    # 绝不要传 MSYS 风格的 /c/... —— Windows 版 pip 会解析成 C:\c\...
    $sysPip = (Get-Command pip -ErrorAction SilentlyContinue)
    if (-not $sysPip) { Fail "系统 pip 不可用，请先装 Python 或在 PATH 里提供 pip" }

    Write-Host "  安装依赖 -> $sitePackages"
    & pip install -r $reqFile --target $sitePackages --no-warn-script-location `
        -i $PipMirror --trusted-host ([Uri]$PipMirror).Host
    if ($LASTEXITCODE -ne 0) { Fail "pip install 失败" }

    # 必须验证真的落到目标目录（这次踩过：装到 C:\c\ 去了，pip 还说成功）
    $pkgCount = (Get-ChildItem $sitePackages -Directory -ErrorAction SilentlyContinue).Count
    if ($pkgCount -lt 10) {
        Fail "site-packages 只有 $pkgCount 个目录，依赖没装到目标位置（检查 --target 路径格式）"
    }
    Ok "依赖安装完成（$pkgCount 个包目录）"
}

# ────────────────────────────────────────────────────────────
Step "5. 冒烟：embeddable 能不能 import 关键包"
$py = Join-Path $runtime "python.exe"
$smoke = & $py -c @"
import importlib, sys
mods = ['fastapi','starlette','pydantic','uvicorn','aiofiles','PIL','pillow_heif','pyotp','qrcode','multipart','tzdata']
bad = []
for m in mods:
    try: importlib.import_module(m)
    except Exception as e: bad.append(f'{m}: {e}')
if bad:
    print('FAILED: ' + '; '.join(bad)); sys.exit(1)
print('OK ' + str(len(mods)) + ' modules')
"@ 2>&1
if ($LASTEXITCODE -ne 0) { Fail "import 冒烟失败: $smoke" }
Ok $smoke

# ────────────────────────────────────────────────────────────
Step "6. 复制后端源码"
$appDst = Join-Path $Dist "app"
New-Item -ItemType Directory -Path $appDst -Force | Out-Null
# 只复制运行需要的，不要 __pycache__ / 测试 / .pyc
$exclude = @("__pycache__", "*.pyc", "*.pyo")
robocopy (Join-Path $Root "app") $appDst /E /XD __pycache__ /XF *.pyc *.pyo /NFL /NDL /NJH /NJS /NC /NS | Out-Null
if ($LASTEXITCODE -ge 8) { Fail "robocopy app 失败 (exit $LASTEXITCODE)" }
# server.py 要放在**安装根**（{app}\server.py），不是 app/ 里面 ——
# NSSM 的服务命令是 `python server.py`，工作目录就是安装根。
foreach ($f in @("server.py")) {
    $src = Join-Path $Root $f
    if (-not (Test-Path $src)) { Fail "缺少 $f" }
    Copy-Item $src $Dist -Force
}
$appFiles = (Get-ChildItem $appDst -Recurse -File).Count
Ok "后端源码 $appFiles 个文件"

# ────────────────────────────────────────────────────────────
Step "6.5 安装/运维 CLI（lanshare_cli）"
# 安装器最后要调 `python -m lanshare_cli install` 来注册服务，
# 所以这个包必须跟安装包一起走。
$cliSrc = Join-Path $Root "lanshare_cli"
if (-not (Test-Path $cliSrc)) { Fail "仓库根缺少 lanshare_cli/" }
$cliDst = Join-Path $Dist "lanshare_cli"
robocopy $cliSrc $cliDst /E /XD __pycache__ /XF *.pyc /NFL /NDL /NJH /NJS /NC /NS | Out-Null
if ($LASTEXITCODE -ge 8) { Fail "robocopy lanshare_cli 失败 (exit $LASTEXITCODE)" }
$cliFiles = (Get-ChildItem $cliDst -Recurse -File).Count
if ($cliFiles -lt 4) { Fail "lanshare_cli 复制后只有 $cliFiles 个文件，不完整" }
Ok "lanshare_cli/ $cliFiles 个文件"

# ────────────────────────────────────────────────────────────
Step "7. 复制前端产物"
$webDst = Join-Path $Dist "web\dist"
New-Item -ItemType Directory -Path $webDst -Force | Out-Null
robocopy $webDist $webDst /E /NFL /NDL /NJH /NJS /NC /NS | Out-Null
if (-not (Test-Path (Join-Path $webDst "index.html"))) { Fail "前端产物复制失败" }
Ok "前端产物已复制"

# ────────────────────────────────────────────────────────────
Step "7.5 静态资源 static/（必需，缺了 app.main 直接崩）"
# app/main.py:138 mount("/static", StaticFiles(PROJECT_ROOT / "static"))
# PROJECT_ROOT = app/ 的父目录 = 安装根，所以 static/ 要放在 dist 根
$staticSrc = Join-Path $Root "static"
if (-not (Test-Path $staticSrc)) { Fail "仓库根缺少 static/ 目录" }
$staticDst = Join-Path $Dist "static"
robocopy $staticSrc $staticDst /E /NFL /NDL /NJH /NJS /NC /NS | Out-Null
if ($LASTEXITCODE -ge 8) { Fail "robocopy static 失败 (exit $LASTEXITCODE)" }
$staticCount = (Get-ChildItem $staticDst -Recurse -File).Count
if ($staticCount -lt 1) { Fail "static/ 复制后是空的" }
Ok "static/ $staticCount 个文件"

# ────────────────────────────────────────────────────────────
Step "7.6 logs/ 目录（服务运行时写入）"
New-Item -ItemType Directory -Path (Join-Path $Dist "logs") -Force | Out-Null
Set-Content -Path (Join-Path $Dist "logs\.gitkeep") -Value "" -Encoding ASCII
Ok "logs/ 已建"

# ────────────────────────────────────────────────────────────
Step "7.7 certs/ —— 刻意不打包私钥"
# certs/key.pem 是私钥，打进公开安装包等于把密钥发给所有人。
# 安装版改用 Caddy 的 tls internal（Caddy 自动生成并管理内部 CA 证书），
# 所以这里只建空目录占位，不复制任何 .pem。
New-Item -ItemType Directory -Path (Join-Path $Dist "certs") -Force | Out-Null
Set-Content -Path (Join-Path $Dist "certs\.gitkeep") -Value "" -Encoding ASCII
Ok "certs/ 只建空目录（不打包私钥）"

# ────────────────────────────────────────────────────────────
Step "8. 二进制：Caddy + NSSM"
$binDst = Join-Path $Dist "bin"
New-Item -ItemType Directory -Path $binDst -Force | Out-Null

$caddy = Join-Path $Root "bin\caddy.exe"
if (-not (Test-Path $caddy)) { Fail "找不到 bin\caddy.exe" }
Copy-Item $caddy $binDst -Force
Ok "caddy.exe"

# NSSM：优先仓库内，其次常见位置
$nssmCandidates = @(
    (Join-Path $Root "bin\nssm.exe"),
    "C:\Users\$env:USERNAME\.openclaw\nssm\nssm.exe",
    (Join-Path $Root "env\nssm\nssm.exe")
)
$nssmSrc = $nssmCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $nssmSrc) { Fail "找不到 nssm.exe，请放到 bin\nssm.exe" }
Copy-Item $nssmSrc $binDst -Force
Ok "nssm.exe（来自 $nssmSrc）"

# 图标（可选）
$ico = Join-Path $Root "logo\lanshare.ico"
if (Test-Path $ico) { Copy-Item $ico $binDst -Force; Ok "lanshare.ico" }
else { Write-Host "  跳过图标（logo\lanshare.ico 不存在，不影响安装）" -ForegroundColor Yellow }

# ────────────────────────────────────────────────────────────
Step "8.5 PostgreSQL（安装器默认数据库，2026-10-31 起只跑 PG）"

# 源：开发环境里的嵌入式 PG（zonky 解出来的那份，就是生产 5433 在用的）
$pgSrc = Join-Path $Root "env\pginstall\zonky"
if (-not (Test-Path $pgSrc)) {
    Fail "找不到 PG 二进制：$pgSrc`n  这是开发环境里的嵌入式 PostgreSQL，构建机必须有。`n  通常来自 env/pginstall/zonky（zonky embedded-postgres 解包产物）。"
}

$pgDst = Join-Path $Dist "pgsql"
New-Item -ItemType Directory -Path $pgDst -Force | Out-Null

# bin：剔除 wx*.dll —— 那是 PG 自带 GUI 工具（pgAdmin 一类）的依赖，
# 服务端只用 postgres/initdb/pg_ctl/psql，留着白占 14 MB。
robocopy (Join-Path $pgSrc "bin") (Join-Path $pgDst "bin") /E /XF wx*.dll /NFL /NDL /NJH /NJS /NC /NS | Out-Null
robocopy (Join-Path $pgSrc "lib") (Join-Path $pgDst "lib") /E /NFL /NDL /NJH /NJS /NC /NS | Out-Null
# share 必须带：initdb 靠 share/postgresql.conf.sample 生成配置，
# 还带 timezone、编码表。漏了 initdb 直接失败。
robocopy (Join-Path $pgSrc "share") (Join-Path $pgDst "share") /E /NFL /NDL /NJH /NJS /NC /NS | Out-Null

# 不复制：postgres-windows-x86_64.txz（22MB 原始包，解压后的东西才是我们要的）
#         META-INF（Java 包的元数据，对 Windows 服务无用）

# 校验关键文件真的在
$pgRequired = @(
    "bin\postgres.exe", "bin\initdb.exe", "bin\pg_ctl.exe", "bin\psql.exe",
    "share\postgresql.conf.sample"
)
$pgMissing = @()
foreach ($f in $pgRequired) {
    if (-not (Test-Path (Join-Path $pgDst $f))) { $pgMissing += $f }
}
if ($pgMissing.Count -gt 0) {
    Fail ("PG 打包不完整，缺少：" + ($pgMissing -join ", "))
}
$pgMB = [math]::Round(((Get-ChildItem $pgDst -Recurse -File | Measure-Object -Property Length -Sum).Sum) / 1MB, 1)
Ok "pgsql/ ($pgMB MB，已剔除 wx* GUI 依赖和原始 tarball)"

# ────────────────────────────────────────────────────────────
Step "9. conf/ 目录（Caddyfile 由 lanshare_cli 安装时生成）"
# Caddyfile 不再由这里生成 —— 端口是安装时才知道的（用户可能改），
# 由 lanshare_cli/caddyfile.py 用 f-string 渲染，没有 PowerShell 的 $ 转义问题。
# 这里只建个空目录占位，让 .iss 的 [Files] 有东西可拷。
$confDst = Join-Path $Dist "conf"
New-Item -ItemType Directory -Path $confDst -Force | Out-Null
Set-Content -Path (Join-Path $confDst ".gitkeep") -Value "" -Encoding ASCII
Ok "conf/ 已建（Caddyfile 在 install 时生成）"

# ────────────────────────────────────────────────────────────
Step "10. 托盘程序（可选，第一批可以没有）"
$traySrc = Join-Path $Root "installer\tray\bin"
if (Test-Path $traySrc) {
    $trayDst = Join-Path $Dist "tray"
    New-Item -ItemType Directory -Path $trayDst -Force | Out-Null
    robocopy $traySrc $trayDst /E /NFL /NDL /NJH /NJS /NC /NS | Out-Null
    Ok "托盘程序已复制"
} else {
    New-Item -ItemType Directory -Path (Join-Path $Dist "tray") -Force | Out-Null
    Write-Host "  跳过（托盘还没编译，.iss 里有 skipifsourcedoesntexist）" -ForegroundColor Yellow
}

# ────────────────────────────────────────────────────────────
Step "汇总"
$totalMB = [math]::Round(((Get-ChildItem $Dist -Recurse -File | Measure-Object -Property Length -Sum).Sum) / 1MB, 1)
Get-ChildItem $Dist -Directory | ForEach-Object {
    $mb = [math]::Round(((Get-ChildItem $_.FullName -Recurse -File | Measure-Object -Property Length -Sum).Sum) / 1MB, 1)
    Write-Host ("  {0,-12} {1,8} MB" -f $_.Name, $mb)
}
Write-Host ("  {0,-12} {1,8} MB" -f "合计", $totalMB) -ForegroundColor Cyan

Write-Host "`n下一步：编译安装包" -ForegroundColor Cyan
Write-Host "  iscc installer\lanshare.iss" -ForegroundColor White
Write-Host "  产物: build\out\LANShare-Setup-*.exe" -ForegroundColor White
Write-Host "`n或者直接验证 CLI（不用打包，几秒钟）" -ForegroundColor Cyan
Write-Host "  python -m lanshare_cli install --dry-run" -ForegroundColor White
