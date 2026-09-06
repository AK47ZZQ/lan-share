# Patch flutter_inappwebview_android build.gradle: it uses
# 'proguard-android.txt' which new AGP hard-rejects at configuration time.
# Replace with 'proguard-android-optimize.txt' inside the pub cache.
# Idempotent: already-patched files are skipped. `flutter pub get` does not
# restore gradle files, so the patch survives dependency re-syncs.
# ALSO strips UTF-8 BOM if present (BOM breaks Groovy parsing).
# NOTE: keep this file ASCII-only (Windows PowerShell 5.1 reads .ps1 as
# ANSI/GBK when there is no BOM, and CJK comments would corrupt parsing).
$roots = @(
  "$env:LOCALAPPDATA\Pub\Cache\hosted",
  "$env:APPDATA\Pub\Cache\hosted"
)
$patched = 0
foreach ($root in $roots) {
  if (-not (Test-Path $root)) { continue }
  $files = Get-ChildItem -Path $root -Filter "build.gradle" -Recurse -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -like "*flutter_inappwebview_android-*\android\build.gradle" }
  foreach ($f in $files) {
    $bytes = [System.IO.File]::ReadAllBytes($f.FullName)
    $hasBom = $bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF
    $c = [System.Text.Encoding]::UTF8.GetString($bytes)
    if ($hasBom) { $c = $c.TrimStart([char]0xFEFF) }
    $changed = $false
    if ($c -match 'proguard-android\.txt') {
      $c = $c -replace 'proguard-android\.txt', 'proguard-android-optimize.txt'
      $changed = $true
    }
    if ($hasBom) { $changed = $true }
    if ($changed) {
      $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
      [System.IO.File]::WriteAllText($f.FullName, $c, $utf8NoBom)
      Write-Host ("patched: " + $f.FullName)
      $patched++
    }
  }
}
Write-Host ("inappwebview proguard patch done, patched=" + $patched)

# --- flutter_keyboard_visibility 6.0.0 hardcodes compileSdkVersion 31, but
# --- its androidx.fragment 1.7.1 dependency requires compileSdk >= 34.
$kb = Get-ChildItem -Path $roots -Filter "build.gradle" -Recurse -ErrorAction SilentlyContinue |
  Where-Object { $_.FullName -like "*flutter_keyboard_visibility-6.0.0\android\build.gradle" }
foreach ($f in $kb) {
  $bytes = [System.IO.File]::ReadAllBytes($f.FullName)
  $hasBom = $bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF
  $c = [System.Text.Encoding]::UTF8.GetString($bytes)
  if ($hasBom) { $c = $c.TrimStart([char]0xFEFF) }
  $changed = $false
  if ($c -match 'compileSdkVersion 31') {
    $c = $c -replace 'compileSdkVersion 31', 'compileSdkVersion 36'
    $changed = $true
  }
  if ($hasBom) { $changed = $true }
  if ($changed) {
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($f.FullName, $c, $utf8NoBom)
    Write-Host ("patched: " + $f.FullName)
    $patched++
  }
}
Write-Host ("keyboard_visibility compileSdk patch done, total patched=" + $patched)
