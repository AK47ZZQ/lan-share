# 镜像新鲜度守卫（v1.1.0+104 事故，反馈 df91）：
# build-apk.bat 构建前必须证明 C:\lan-share-mobile 与 C:\lan-share\mobile 的
# Dart 源码 + pubspec 完全一致。事故复盘：构建时镜像未同步（pubspec 新、
# lib 旧），APK 带新版本号却装着旧 UI——sha256 与 version.json 一致、构建
# 时间线正确，所有既有证据都只证明"包是那时构建的"，证明不了"包里是最
# 新代码"。本脚本逐文件比对 SHA256，不一致即 exit 1，构建中止。
$ErrorActionPreference = 'Stop'
$src = 'C:\lan-share\mobile'
$dst = 'C:\lan-share-mobile'

function Get-SourceManifest($root) {
    Get-ChildItem -LiteralPath $root -Recurse -File | Sort-Object FullName |
        ForEach-Object {
            $rel = $_.FullName.Substring($root.Length).TrimStart('\').ToLower()
            $hash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
            "$rel=$hash"
        }
}

$drift = @(Compare-Object (Get-SourceManifest "$src\lib") (Get-SourceManifest "$dst\lib"))
$pubspecOk = (Get-FileHash "$src\pubspec.yaml").Hash -eq (Get-FileHash "$dst\pubspec.yaml").Hash

if ($drift.Count -gt 0 -or -not $pubspecOk) {
    $drift | Select-Object -First 20 | ForEach-Object {
        Write-Host ("MIRROR DRIFT: " + $_.SideIndicator + " " + $_.InputObject)
    }
    if (-not $pubspecOk) { Write-Host "MIRROR DRIFT: pubspec.yaml" }
    Write-Host "MIRROR STALE: mobile 源与镜像不一致，构建中止（先查 robocopy 为何没同步）"
    exit 1
}
Write-Host "MIRROR OK: lib 与 pubspec 逐文件哈希一致"
exit 0
