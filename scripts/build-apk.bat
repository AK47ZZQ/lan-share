@echo off
REM LAN Share Android APK build helper.
REM The repo lives under a non-ASCII path, which breaks the Android NDK/CMake
REM build (libdartjni.so never appears). Workaround: mirror the mobile source
REM to an ASCII-only physical directory, build there, copy the APK back.
REM Usage: scripts\build-apk.bat [debug|release]   (default: debug)

setlocal
set MODE=%1
if "%MODE%"=="" set MODE=debug

set SRC=C:\lan-share\mobile
set DST=C:\lan-share-mobile
set PYTHON=C:\Users\<你的用户名>\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe
set FLUTTER_BIN=C:\lan-share\env\flutter\bin
REM 构建必须用仓库自带的 SDK（env/android-sdk 有 gradle 要的 NDK 28.2）：
REM 机器级 ANDROID_HOME 指向 C:\tools\android-sdk（无 ndk 目录），flutter 每次
REM 构建按它重写镜像里的 local.properties ⇒ gradle 触发 sdkmanager 装包 →
REM sdkmanager.bat 崩 0xC0000409（2026-09-25 v1.1.9 出包实录）。脚本层显式覆盖。
set ANDROID_HOME=C:\lan-share\env\android-sdk
set ANDROID_SDK_ROOT=C:\lan-share\env\android-sdk

echo === Mirror mobile source to %DST% (ASCII path) ===
if not exist "%DST%" mkdir "%DST%"
robocopy "%SRC%" "%DST%" /MIR /XD build .dart_tool .git /XF *.log /NFL /NDL /NP
if %ERRORLEVEL% GEQ 8 (
  echo MIRROR FAILED: robocopy exit %ERRORLEVEL%
  exit /b 1
)

echo === Verify mirror freshness (lib/*.dart + pubspec must equal source) ===
REM v1.1.0+104 事故（反馈 df91）：构建时镜像未同步（pubspec 新、Dart 旧），
REM APK 带新版本号却是旧 UI——sha256 一致、时间线正确等证据只证明"包是那时
REM 构建的"，证明不了"包里是最新代码"。逐文件哈希比对，不新鲜即中止。
powershell -NoProfile -ExecutionPolicy Bypass -File "%SRC%\..\scripts\verify-mirror.ps1" || exit /b 1

echo === flutter pub get ===
cd /d "%DST%"
set PATH=%FLUTTER_BIN%;%PATH%
set JAVA_HOME=C:\Program Files\RedHat\java-17-openjdk-17.0.19.0.10-1
set PUB_HOSTED_URL=https://pub.flutter-io.cn
set FLUTTER_STORAGE_BASE_URL=https://storage.flutter-io.cn
call flutter pub get || exit /b 1

echo === Patch flutter_inappwebview_android proguard line (AGP hard-error) ===
REM inappwebview 1.1.3 的 proguard-android.txt 被新版 AGP 拒绝：缓存内补丁
powershell -NoProfile -ExecutionPolicy Bypass -File "%SRC%\..\scripts\patch-inappwebview.ps1" || exit /b 1

echo === flutter build apk --%MODE% ===
call flutter build apk --%MODE% || exit /b 1

echo === Copy APK back to repo ===
if not exist "%SRC%\build\apk" mkdir "%SRC%\build\apk"
copy /Y "%DST%\build\app\outputs\flutter-apk\app-%MODE%.apk" "%SRC%\build\apk\lanshare-%MODE%.apk"
echo DONE: %SRC%\build\apk\lanshare-%MODE%.apk
endlocal
