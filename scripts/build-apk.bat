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
set FLUTTER_BIN=C:\flutter\flutter\bin

echo === Mirror mobile source to %DST% (ASCII path) ===
if not exist "%DST%" mkdir "%DST%"
robocopy "%SRC%" "%DST%" /MIR /XD build .dart_tool .git /XF *.log /NFL /NDL /NP
if %ERRORLEVEL% GEQ 8 (
  echo MIRROR FAILED: robocopy exit %ERRORLEVEL%
  exit /b 1
)

echo === flutter pub get ===
cd /d "%DST%"
set PATH=%FLUTTER_BIN%;%PATH%
set JAVA_HOME=C:\Program Files\RedHat\java-17-openjdk-17.0.19.0.10-1
set PUB_HOSTED_URL=https://pub.flutter-io.cn
set FLUTTER_STORAGE_BASE_URL=https://storage.flutter-io.cn
call flutter pub get || exit /b 1

echo === flutter build apk --%MODE% ===
call flutter build apk --%MODE% || exit /b 1

echo === Copy APK back to repo ===
if not exist "%SRC%\build\apk" mkdir "%SRC%\build\apk"
copy /Y "%DST%\build\app\outputs\flutter-apk\app-%MODE%.apk" "%SRC%\build\apk\lanshare-%MODE%.apk"
echo DONE: %SRC%\build\apk\lanshare-%MODE%.apk
endlocal
