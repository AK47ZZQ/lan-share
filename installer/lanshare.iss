; ============================================================================
;  LAN Share — Windows 一键安装脚本（Inno Setup 6）
;
;  设计目标：小白双击 → 下一步 → 下一步 → 完成 → 浏览器自动打开登录页。
;  不需要预先装 Python / PostgreSQL / Node / Caddy / NSSM。
;
;  编译方式：
;     iscc installer\lanshare.iss
;  前提：先跑 installer\build.ps1 把产物收进 build\dist\
;
;  build\dist\ 目录约定：
;     runtime\        embeddable Python + Lib\site-packages（62 MB）
;     app\            后端源码 app/ + server.py 等
;     web\dist\       前端构建产物（后端直接挂载）
;     bin\            caddy.exe + nssm.exe
;     tray\           托盘程序（第一批可以不打包）
;
;  重要：服务启动入口必须是 app.main:app，不是 app.asgi:app。
;        后者是裸 FastAPI 对象（只有 4 个路由），服务能起来、端口在听，
;        但所有路由 404 —— 2026-10-30 实测踩过。
; ============================================================================

#define AppName        "LAN Share"
#define AppVersion     "4.0.12"
#define AppPublisher   "AK47ZZQ"
#define AppURL         "https://github.com/AK47ZZQ/lan-share"
#define HttpPort       "8764"
#define ApiPort        "8766"
#define WebPort        "8765"
#define ServiceName    "LANShare-API"
#define ServiceDisplay "LAN Share API"
#define CaddyService   "LANShare-Caddy"
#define CaddyDisplay   "LAN Share Web"

[Setup]
AppId={{8F3C2A14-7B5E-4D91-A6C3-1E5F8D2B9A07}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
DefaultDirName={code:GetDefaultDir}
; 用户选一次安装目录，所有文件就在那儿 —— 不再自动追加一层。
;
; AppendDefaultDirName 默认是 yes：用户在向导里把路径改成 C:\lanshare 之后，
; Inno 发现末段不是默认的 "LAN Share"，会自动再拼上去，实际装到
; C:\lanshare\LAN Share。于是「只选了一次目录」却散成两处：
;   C:\lanshare\LAN Share   程序
;   C:\lanshare\data        数据
; 关掉它，选哪就是哪。数据目录默认取 {app}\data，也在同一个根下。
AppendDefaultDirName=no
DefaultGroupName={#AppName}
; 2026-11-07：许可从 AGPL 改为 PolyForm Noncommercial（商用需授权）。
; 安装时必须让用户看到条款 —— 商用限制要知情同意才站得住。
LicenseFile=LICENSE.txt
DisableProgramGroupPage=yes
OutputDir=..\build\out
OutputBaseFilename=LANShare-Setup-{#AppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
; 需要管理员权限：注册 Windows 服务、写 HKLM 注册表都要提权
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
UninstallDisplayName={#AppName} {#AppVersion}
UninstallDisplayIcon={app}\bin\lanshare.ico
CloseApplications=no

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "autostart"; Description: "开机时自动启动 LAN Share 托盘图标"; GroupDescription: "启动选项:"; Flags: checkedonce
Name: "desktopicon"; Description: "在桌面创建 LAN Share 快捷方式"; GroupDescription: "附加图标:"; Flags: checkedonce

[Files]
; ── Python 运行时 + 依赖（最大的一块，62 MB）──
Source: "..\build\dist\runtime\*"; DestDir: "{app}\runtime"; Flags: recursesubdirs createallsubdirs ignoreversion
; ── 后端源码 ──
Source: "..\build\dist\app\*"; DestDir: "{app}\app"; Flags: recursesubdirs createallsubdirs ignoreversion
; ── 前端产物（后端直接挂载，不需要 Node）──
Source: "..\build\dist\web\*"; DestDir: "{app}\web"; Flags: recursesubdirs createallsubdirs ignoreversion
; ── 二进制：Caddy + NSSM ──
Source: "..\build\dist\bin\caddy.exe"; DestDir: "{app}\bin"; Flags: ignoreversion
Source: "..\build\dist\bin\nssm.exe"; DestDir: "{app}\bin"; Flags: ignoreversion
; conf/ 空目录占位 —— Caddyfile 由 lanshare_cli 在 install 时按实际端口生成
Source: "..\build\dist\conf\*"; DestDir: "{app}\conf"; Flags: recursesubdirs createallsubdirs ignoreversion skipifsourcedoesntexist
; ── 安装/运维 CLI（[Code] 里要调它来注册服务）──
Source: "..\build\dist\lanshare_cli\*"; DestDir: "{app}\lanshare_cli"; Flags: recursesubdirs createallsubdirs ignoreversion

; PostgreSQL：安装器默认数据库，装完由 lanshare_cli install 初始化
; 不带这个目录安装会直接失败（required_files 会报缺 initdb）
Source: "..\build\dist\pgsql\*"; DestDir: "{app}\pgsql"; Flags: recursesubdirs createallsubdirs ignoreversion
; ── 服务入口 ──
Source: "..\build\dist\server.py"; DestDir: "{app}"; Flags: ignoreversion
; ── 版本号（check-update 靠它知道当前装的是哪个版本）──
Source: "..\build\dist\VERSION"; DestDir: "{app}"; Flags: ignoreversion
; ── 静态资源（必需：app/main.py 启动时 mount /static，缺了直接崩）──
Source: "..\build\dist\static\*"; DestDir: "{app}\static"; Flags: recursesubdirs createallsubdirs ignoreversion
; ── certs / logs 只建空目录占位（证书由 Caddy 自己生成，私钥绝不打包）──
Source: "..\build\dist\certs\*"; DestDir: "{app}\certs"; Flags: recursesubdirs createallsubdirs ignoreversion skipifsourcedoesntexist
Source: "..\build\dist\logs\*"; DestDir: "{app}\logs"; Flags: recursesubdirs createallsubdirs ignoreversion skipifsourcedoesntexist
; ── 托盘程序（存在才打包，第一批可以没有）──
Source: "..\build\dist\tray\*"; DestDir: "{app}\tray"; Flags: recursesubdirs createallsubdirs ignoreversion skipifsourcedoesntexist
; ── 图标 ──
Source: "..\build\dist\bin\lanshare.ico"; DestDir: "{app}\bin"; Flags: ignoreversion skipifsourcedoesntexist

[Dirs]
; 🔴 这里必须用 {code:GetDataDir} 而不是写死 {commonappdata}。
;
; 2026-11-01 分盘实测抓到：装到 D:\LANShare 时注册表里 DataFolder 已经是
; D:\LANShare\data，但 [Dirs] 段照样在 C:\ProgramData\LANShare\data 建目录，
; 日志里明晃晃三行：
;     Creating directory: C:\ProgramData\LANShare
;     Creating directory: C:\ProgramData\LANShare\data
;     Creating directory: C:\ProgramData\LANShare\logs
; 用户选 D 盘、结果 C 盘长出一个数据目录，就是这么来的。
;
; 注释原本还写着「data 目录默认放在 ProgramData，且不会被卸载误删」——
; 挂了 uninsdeletekey 之后这套已经废弃了，数据跟着安装目录走。
Name: "{code:GetDataDir}"; Permissions: users-modify
; CLI 安装时会往里写 Caddyfile / 证书占位，先建好避免权限问题
Name: "{app}\conf"
Name: "{app}\certs"
Name: "{app}\logs"

[Registry]
; ── 安装信息（托盘程序靠这个定位服务和数据）──
Root: HKLM; Subkey: "SOFTWARE\LANShare"; ValueType: string; ValueName: "InstallFolder"; ValueData: "{app}"; Flags: uninsdeletekey
Root: HKLM; Subkey: "SOFTWARE\LANShare"; ValueType: string; ValueName: "DataFolder"; ValueData: "{code:GetDataDir}"; Flags: uninsdeletekey
Root: HKLM; Subkey: "SOFTWARE\LANShare"; ValueType: string; ValueName: "ApiPort"; ValueData: "{#ApiPort}"; Flags: uninsdeletekey
Root: HKLM; Subkey: "SOFTWARE\LANShare"; ValueType: string; ValueName: "WebPort"; ValueData: "{#WebPort}"; Flags: uninsdeletekey
Root: HKLM; Subkey: "SOFTWARE\LANShare"; ValueType: string; ValueName: "Version"; ValueData: "{#AppVersion}"; Flags: uninsdeletekey
; 和 lanshare_cli 的 DEFAULT_SERVICE_PREFIX 保持一致（lanshare，无连字符）。
; 托盘按 ServicePrefix+"-api" 拼服务名，两边不一致就会找不到服务。
; 注：CLI 装完会用自己的实际前缀再回写一次，这里只是文件复制阶段的初值。
Root: HKLM; Subkey: "SOFTWARE\LANShare"; ValueType: string; ValueName: "ServicePrefix"; ValueData: "lanshare"; Flags: uninsdeletekey
; ── 托盘开机自启：改用「任务计划 + 最高权限」──
; 托盘 manifest 现在是 requireAdministrator（要控制 Windows 服务）。
; 这种 exe 放进 HKCU/HKLM\...\Run 自启，每次开机都会弹 UAC 确认框 ——
; 用户不会为了一个家用网盘每次开机点「是」。任务计划用 /RL HIGHEST
; 可以静默以最高权限启动，代价只是要求安装时是管理员（本来就是）。
; 具体注册见 [Run] 段的 schtasks。
; ── 安装路径加入 PATH（方便命令行调 python.exe）──

[Icons]
; 桌面和开始菜单的主图标都指向**托盘程序**，不是网页。
; 托盘才是控制面板入口（启停服务、看状态、复制地址），比直接开网页有用。
; 网页那个保留在开始菜单，名字写明白是「在浏览器中打开」。
Name: "{commondesktop}\{#AppName}"; Filename: "{app}\tray\LANShareTray.exe"; Parameters: "--panel"; Tasks: desktopicon
Name: "{group}\{#AppName}"; Filename: "{app}\tray\LANShareTray.exe"; Parameters: "--panel"
Name: "{group}\在浏览器中打开"; Filename: "https://127.0.0.1:{#WebPort}"
Name: "{group}\打开数据文件夹"; Filename: "{code:GetDataDir}"
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"

[Run]
; 真正的安装动作不在这里 —— 见 [Code] 里的 CurStepChanged。
; 服务注册、Caddyfile 生成、端口检查、启动、健康检查全部由 lanshare_cli 完成，
; 失败会打印人话错误 + 日志路径，不用在 Pascal 里跟 nssm 的命令行搏斗。
;
; 这里只放「装完之后给用户看的东西」。

; ── 勾选「打开 LAN Share」→ 启动控制面板（GUI），不是浏览器 ──
; 2026-11-01 用户反馈「安装完成后勾选打开，点完成没动静，GUI 界面没被打开」。
; 原先这里 shellexec 一个 https://127.0.0.1:8765，浏览器被证书警告页挡在前面，
; 用户看到的就是「没反应」。控制面板才是用户想点的东西。
; ⚠️ 必须加 shellexec：托盘 manifest 是 requireAdministrator，
; Inno 默认用 CreateProcess 启动子进程，遇到需要提权的 exe 直接失败：
;   CreateProcess 失败；错误代码 740。请求的操作需要提升。
; 2026-11-02 用户笔记本实测装完就弹这个框。shellexec 走 ShellExecuteEx 会
; 正常请求提权；安装器本身已是管理员，所以不会再弹一次 UAC。
Filename: "{app}\tray\LANShareTray.exe"; Parameters: "--panel"; Flags: shellexec nowait postinstall skipifsilent skipifdoesntexist; Description: "打开 LAN Share 控制面板"
; 仍然保留「在浏览器里打开」（用 localhost，证书 SAN 里一定有它）
Filename: "https://localhost:{#WebPort}"; Flags: shellexec nowait postinstall skipifsilent skipifdoesntexist; Description: "在浏览器中打开 LAN Share"

; ── 启动托盘 ──
; 去掉 postinstall：那标志只在「安装完成」页勾选时才执行。
; 装完如果弹了任何错误（哪怕只是自检误报），完成页可能根本不出现，
; 托盘就永远不启动 —— 用户装完桌面上什么都没有，以为没装上。
; 现在安装流程走到这里就起；只有 /SILENT 才跳过。
; 同样要 shellexec，否则装完自动启动托盘时又是 740
Filename: "{app}\tray\LANShareTray.exe"; Flags: shellexec nowait skipifsilent skipifdoesntexist; Tasks: autostart
; 注册开机自启任务（最高权限、不弹 UAC）。/F 覆盖同名任务，幂等。
;
; 🔴 引号层数要算清楚（2026-11-04 审查）：
; Inno 的 Parameters 里，`""` 还原成一个 `"`。原写法
;   /tr """"{app}\tray\LANShareTray.exe""""
; 实际发出去是 /tr ""C:\Program Files\LAN Share\tray\LanShareTray.exe""
; —— 参数值本身带了一对引号，CommandLineToArgvW 把它切成
;   C:\Program / Files\LAN / Share\tray\LANShareTray.exe
; schtasks 于是拿 /tr C:\Program 去建任务，创建失败；而这条是
; runhidden 且没人看返回码，所以「勾了开机自启但重启不出现」完全无声。
;
; 正确写法是 /tr ""{app}\...\LANShareTray.exe"" —— 还原成
;   /tr "C:\Program Files\LAN Share\tray\LanShareTray.exe" ✔
Filename: "{sys}\schtasks.exe"; Parameters: "/create /tn ""LANShareTray"" /tr ""{app}\tray\LANShareTray.exe"" /sc onlogon /rl highest /f"; Flags: runhidden waituntilterminated; Tasks: autostart

[UninstallRun]
; 撤销安装 = 撤销 lanshare_cli install 做的事：先停服务再删服务，数据保留。
; 顺序由 CLI 内部保证（它知道要先停 caddy 再停 api）。
;
; 不带 --purge-data：删用户照片这种事必须问人，不能由卸载脚本决定。
; 卸载脚本这边已经问过了（CurUninstallStepChanged 的 usUninstall 阶段），
; 用户选「是」时由安装器自己 DelTree，CLI 只管停服务、删服务、清注册表。
;
; runminimized 而不是 runhidden：CLI 会输出每一步的结果（服务删没删掉、
; 防火墙规则清没清），藏起来的话中间哪一步失败用户和我们都看不见 ——
; 「卸载只删了一部分」有一半原因是这里把错误吞了。
Filename: "{app}\runtime\python.exe"; Parameters: "-m lanshare_cli uninstall"; Flags: runminimized waituntilterminated; RunOnceId: "StopAndRemoveServices"
; 删掉开机自启任务（托盘已经是任务计划自启，不再走 Run 键）
Filename: "{sys}\schtasks.exe"; Parameters: "/delete /tn ""LANShareTray"" /f"; Flags: runhidden; RunOnceId: "RemoveAutostartTask"
; CLI 摘掉服务之后，postgres.exe / caddy.exe 不一定立刻退出，数据目录里的
; PG 文件还锁着 —— 后面 DelTree 会返回 False，用户看到「点了是但 data 还在」。
;
; 🔴 不能按进程名通配（原来的 /F /IM postgres.exe）：
; 用户机器上可能装着另一个 PostgreSQL（自己开发用的）、或者正跑着别的
; Python 程序（Jupyter、其它服务、乃至别的 agent）。按名字杀 = 卸载本产品
; 顺手把别人的进程全干掉，而且完全没有提示。
;
; 改成「进程路径在本次安装目录下才杀」。PowerShell 的 $_ .Path 就是可执行
; 文件全路径，用户自装的那份在 Program Files 下，不会命中。
; python.exe 不再单独处理：它由 NSSM 托管，服务被删除时 NSSM 会带走子进程，
; 多杀一次只会伤及无辜。
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -Command ""Get-Process postgres,caddy -ErrorAction SilentlyContinue | Where-Object {{ $_.Path -like '{app}\*' }} | Stop-Process -Force -ErrorAction SilentlyContinue"""; Flags: runhidden; RunOnceId: "KillLockedBinaries"

[UninstallDelete]
; 删「运行时生成、不在安装清单里」的东西：__pycache__ / Caddyfile / 日志 / 证书。
;
; 🔴 绝不能写 Type: filesandordirs; Name: "{app}"。
;    数据默认就在 {app}\data，整目录删会把用户的照片和数据库一起抹掉。
;    2026-11-01 真机反馈「卸载时说删了数据，其实早就删没了」就是这么来的：
;    [UninstallDelete] 在删装文件阶段执行，早于 CurUninstallStepChanged(usPostUninstall)
;    里那个「是否删除数据」的对话框 —— 问的时候数据已经没了，问了个寂寞。
;
; 现在按子目录精确列，{app}\data 完全不在这个表里。
; 数据只在 CurUninstallStepChanged 里、用户明确选「是」之后才删。
Type: filesandordirs; Name: "{app}\conf"
Type: filesandordirs; Name: "{app}\logs"
Type: filesandordirs; Name: "{app}\certs"
Type: filesandordirs; Name: "{app}\runtime"
Type: filesandordirs; Name: "{app}\app"
Type: filesandordirs; Name: "{app}\lanshare_cli"
Type: filesandordirs; Name: "{app}\web"
Type: filesandordirs; Name: "{app}\static"
Type: filesandordirs; Name: "{app}\pgsql"
Type: filesandordirs; Name: "{app}\bin"
Type: filesandordirs; Name: "{app}\tray"
; server.py 在 {app} 根目录被 import 时会生成 {app}\__pycache__，
; 它不归任何子目录管，漏了就是卸载后留一个空文件夹。
; 2026-11-01 用户反馈「卸载后残留 __pycache__」就是这个。
Type: filesandordirs; Name: "{app}\__pycache__"

[Code]
var
  DataDirPage: TInputDirWizardPage;
  LastAutoDataDir: String;
  // 卸载时「是否删除数据」的选择。usUninstall 阶段问，usPostUninstall 阶段执行。
  // 必须分成两个阶段：问的时候文件还没删，用户看到的目录是完整的。
  DeleteDataOnUninstall: Boolean;
  // 🔴 数据目录必须在这里存一份。
  //
  // 执行顺序是：usUninstall（问）→ [UninstallRun] 调 CLI → CLI 删掉注册表
  // → usPostUninstall（删数据）。
  // 到 usPostUninstall 时 GetDataDir('') 已经读不到 DataFolder 了，会退回
  // 兜底的 {commonappdata}\LANShare\data —— 去删一个不存在的 C 盘路径，
  // 用户真正的数据目录（可能在 G 盘）原封不动。
  // 2026-11-01 用户反馈「卸载时点了是，还是残留 2 个文件夹」就是这个。
  DataPathForPurge: String;
  // ── 升级检测（3.98）──
  //
  // 安装器必须能认出「这台机器上已经有一份 LAN Share」。不认的后果是实打实的：
  // 3.97 及以前双击更新包会被当成全新安装，向导页数据目录的默认值优先级高于
  // 注册表，用户真实的数据目录被悄悄改成新目录；服务还在跑、文件被占用，
  // 装到一半直接失败（exit 5）。
  // 配置阶段失败过（lanshare_cli install 返回非 0）。
  // 静默安装里没法弹框，只能靠 GetCustomSetupExitCode 把失败传出去 ——
  // 传不出去的话，托盘「立即更新」会把「服务一个都没起来」当成更新成功。
  InstallStepFailed: Boolean;
  IsUpgrade: Boolean;
  PrevInstallDir: String;   // 上次装在哪
  PrevVersion: String;      // 上次的版本（空 = 老安装没写过 Version）
  NewVersion: String;       // 本包的版本

{ ── 升级检测（3.98）────────────────────────────────────────── }

{ 把 "3.97" 这类版本串转成可比较的整数：每段乘 1000 累加。
  段数不定（3.97 / 3.97.1），统一按 3 段算 —— 本项目版本号没超过 3 段，
  每段 < 1000，进位空间够。 }
{ 独立更新看护需要安装器自报 PID（传给 %TEMP% 副本作轮询目标）。
  Inno 的 Exec/ewNoWait 拿不到子进程句柄，自报家门最简。 }
function GetCurrentProcessId: Longint;
  external 'GetCurrentProcessId@kernel32.dll stdcall';

function VersionKey(const V: String): Int64;
var
  I, Seg: Integer;
  Num: Int64;
begin
  Result := 0;
  Num := 0;
  Seg := 0;
  for I := 1 to Length(V) do
  begin
    if (V[I] >= '0') and (V[I] <= '9') then
      Num := Num * 10 + (Ord(V[I]) - Ord('0'))
    else if V[I] = '.' then
    begin
      Result := Result * 1000 + Num;
      Num := 0;
      Inc(Seg);
      if Seg >= 3 then Break;
    end;
  end;
  Result := Result * 1000 + Num;
end;

{ 服务名前缀。这台机器上装的可能是任意前缀（测试实例用过 lstest 之类），
  从注册表读，读不到才用默认值。 }
function GetPrefix(): String;
begin
  if (not RegQueryStringValue(HKEY_LOCAL_MACHINE, 'SOFTWARE\LANShare',
                              'ServicePrefix', Result)) or (Result = '') then
    Result := 'lanshare';
end;

{ 安装目录默认值：
  · 升级 → 用上次装的那个目录，不给「装出第二个实例」的机会
  · 新装 → 系统程序目录下的 LAN Share }
function GetDefaultDir(Param: String): String;
begin
  if IsUpgrade and (PrevInstallDir <> '') then
    Result := PrevInstallDir
  else
    Result := ExpandConstant('{autopf}\LAN Share');
end;

{ 覆盖安装前必须先停服务：python.exe / caddy.exe / *.dll 被占用时，
  Inno 复制文件会失败（3.97 实测 exit 5，整个安装回滚）。

  顺序和 lanshare_cli 的 StopAll 一致：入口 → 应用 → 数据库。
  PostgreSQL 停止要做 checkpoint，-Force 保证即使还有连接也停得掉；
  末尾再等 1.5 秒让端口和文件句柄真正释放。 }
{ 安装失败看护（2026-11-07）：把托盘 exe 复制到 %TEMP% 换名
  （LanShareUpdateWatcher.exe），带 --update-watcher <安装器PID> <托盘exe> 启动。
  映像名不同 → 前面的 taskkill /IM LanShareTray.exe 杀不到它；
  ewNoWait 脱管 → 安装器退出不影响它；C# 侧分支在互斥量之前 return。
  任何一步失败都只是退回「没有看护」的现状，绝不影响安装本身。 }
procedure SpawnDetachedUpdateWatcher();
var
  RC: Integer;
  Src, Dst, Tmp: String;
begin
  Tmp := ExpandConstant('{%TEMP}');
  if Tmp = '' then Exit;
  Src := ExpandConstant('{app}\tray\LanShareTray.exe');
  Dst := Tmp + '\LanShareUpdateWatcher.exe';
  { 覆盖旧副本；删不掉就让 FileCopy 失败 = 退回现状 }
  DeleteFile(Dst);
  if not CopyFile(Src, Dst, False) then Exit;
  if not FileExists(Dst) then Exit;
  Exec(Dst, '--update-watcher ' + IntToStr(GetCurrentProcessId())
       + ' "' + ExpandConstant('{app}\tray\LanShareTray.exe') + '"',
       Tmp, SW_HIDE, ewNoWait, RC);
end;

procedure StopServicesForUpgrade();
var
  RC: Integer;
  Cmd: String;
  P: String;
begin
  P := GetPrefix();
  Cmd := '-NoProfile -Command "foreach ($s in @(''' + P + '-caddy'',''' + P
       + '-api'',''' + P + '-postgresql'')) { Stop-Service $s -Force '
       + '-ErrorAction SilentlyContinue }; Start-Sleep -Milliseconds 1500"';
  Exec('powershell.exe', Cmd, '', SW_HIDE, ewWaitUntilTerminated, RC);

  { 托盘不是服务，Stop-Service 管不到它 —— 但它同样占着 tray\LanShareTray.exe。
    不关掉的话，Inno 覆盖这个文件时报「拒绝访问(5)」，整个安装回滚。
    3.99 第一次真机升级就是这么失败的（日志里 DeleteFile 重试 4 次后 Abort）。 }
  Exec('taskkill.exe', '/F /IM LanShareTray.exe', '', SW_HIDE,
       ewWaitUntilTerminated, RC);
  Sleep(600);

  { 此刻起到 ssPostInstall 拉回托盘之间是「无看护窗口」：复制失败回滚
    （exit 5 类）会让托盘死掉且没人拉回 —— v4.0.7 加时审查记录的缺口。 }
  SpawnDetachedUpdateWatcher();
end;

{ 安装器启动的第一件事：这台机器上有没有已安装的 LAN Share？

  只做识别、不弹窗 —— 静默安装（托盘「一键更新」走的就是这条路）
  不能有任何等人点击的阻塞。给用户看的提示放在欢迎页，见 InitializeWizard。 }
function InitializeSetup(): Boolean;
var
  RegDir, RegVer: String;
begin
  Result := True;
  IsUpgrade := False;
  PrevInstallDir := '';
  PrevVersion := '';
  NewVersion := '{#AppVersion}';

  if RegQueryStringValue(HKEY_LOCAL_MACHINE, 'SOFTWARE\LANShare',
                         'InstallFolder', RegDir)
     and (RegDir <> '') and DirExists(RegDir) then
  begin
    IsUpgrade := True;
    PrevInstallDir := RegDir;
    if RegQueryStringValue(HKEY_LOCAL_MACHINE, 'SOFTWARE\LANShare',
                           'Version', RegVer) then
      PrevVersion := RegVer;
  end;

  { 降级拦截（2026-11-04 审查）。

    在此之前 Installer 完全没有版本概念：手滑双击一个旧包，它一句话不说就把
    程序文件盖回去，然后拿老代码去连新版 schema 的库 —— 用户只会看到
    「更新完就坏了」，而且不知道是自己装错了包。

    静默安装（托盘一键更新走这条路）更不能自动降级：那说明更新源返回了
    比本机更旧的版本，通常意味着清单被改错、或者有人往源里塞了旧包。
    直接失败并给出非 0 退出码，比「默默降级然后一切看起来正常」安全得多。 }
  if IsUpgrade and (PrevVersion <> '') then
  begin
    if VersionKey(PrevVersion) > VersionKey(NewVersion) then
    begin
      if WizardSilent then
      begin
        InstallStepFailed := True;
        Result := False;
        Exit;
      end;
      if MsgBox('这台机器上装的是 ' + PrevVersion + '，而这个是 ' +
                NewVersion + '（更旧）。' + #13#10 + #13#10 +
                '继续安装会降级：新版本里的功能和修复会消失。' +
                '数据和账号不会丢。' + #13#10 + #13#10 +
                '确实要降级吗？', mbConfirmation, MB_YESNO) <> IDYES then
      begin
        Result := False;
        Exit;
      end;
    end;
  end;
end;

{ 安装器最终退出码。

  默认 0，配置阶段失败时返回 1。这个函数存在的唯一理由：静默安装
  （/VERYSILENT，托盘一键更新走的就是它）不能弹框，所以「服务没起来」
  这件事如果不从这里传出去，外面看到的就是一个干净的退出码 0 ——
  托盘会以为更新成功，用户看到网盘打不开，而日志里什么都看不出来。
  这类「失败伪装成成功」是所有后续排障的最大障碍。 }
function GetCustomSetupExitCode(): Integer;
begin
  if InstallStepFailed then
    Result := 1
  else
    Result := 0;
end;

{ 命令行里有没有某个参数（不区分大小写）。 }
{ 只为 /DELETE_DATA 服务：静默卸载默认选「否」（保留数据）， }
{ 自动化测试需要一条能走到「删除数据」分支的路径。 }
function CmdLineHas(Param: String): Boolean;
var
  I: Integer;
begin
  Result := False;
  for I := 1 to ParamCount do
  begin
    if CompareText(ParamStr(I), Param) = 0 then
    begin
      Result := True;
      Exit;
    end;
  end;
end;

{ ── 数据目录：安装时用向导页选择，卸载时从注册表读回 ── }
function GetDataDir(Param: String): String;
var
  RegDir: String;
begin
  { 🔴 升级时，注册表里的 DataFolder 是**唯一权威**。

    3.97 及以前这里没有这个分支，向导页的默认值（安装目录下的 data）优先级高于
    注册表 —— 于是双击更新包会让用户真实的数据目录被悄悄换成新目录，
    程序和文件从此分家，看起来就像「数据丢了」。

    静默安装更严重：页面对象存在但不可见，默认值照样被读走。
    这条分支是升级路径正确性的地基。 }
  if IsUpgrade then
  begin
    if RegQueryStringValue(HKEY_LOCAL_MACHINE, 'SOFTWARE\LANShare',
                           'DataFolder', RegDir) and (RegDir <> '') then
    begin
      Result := RegDir;
      Exit;
    end;
  end;

  { 安装阶段：向导页有值就用它（用户刚选的） }
  if (DataDirPage <> nil) and (DataDirPage.Values[0] <> '') then
  begin
    Result := DataDirPage.Values[0];
    Exit;
  end;
  { 卸载阶段：没有向导页，读安装时写进注册表的值 }
  if RegQueryStringValue(HKEY_LOCAL_MACHINE, 'SOFTWARE\LANShare',
                         'DataFolder', RegDir) and (RegDir <> '') then
  begin
    Result := RegDir;
    Exit;
  end;
  // 兜底：跟安装目录走。
  //
  // 这里原来是 ExpandConstant('{commonappdata}\LANShare\data')，
  // 也就是 C:\ProgramData\LANShare\data —— 违背「所有文件都在安装目录」
  // 这条明确要求，而且和「数据目录页」的默认值（app + \data）不是同一个地方。
  // 一旦向导页没值（静默安装、或页面被跳过），注册表就写 ProgramData，
  // 而上次装的 PG 服务还指着 app + \data\pgdata —— 两边对不上，PG 起不来。
  // 2026-11-02 用户实机正是这个组合。
  //
  // 注意：这段必须用 // 而不是花括号注释 —— 花括号里只要出现一个右花括号
  // 就会提前闭合，而这里必然会提到那个常量名。
  Result := ExpandConstant('{app}\data');
end;

procedure InitializeWizard;
begin
  { ── 升级：不建数据目录页，也不给改安装位置 ──
    两个值都从注册表来（GetDefaultDir / GetDataDir 里已经处理），
    向导从 4 页缩到 2 页。用户点「下一步」就该装完。 }
  if IsUpgrade then
  begin
    WizardForm.WelcomeLabel1.Caption := '更新 LAN Share';
    if PrevVersion <> '' then
      WizardForm.WelcomeLabel2.Caption :=
        '检测到已安装的 LAN Share ' + PrevVersion + '：' + #13#10 +
        PrevInstallDir + #13#10 + #13#10 +
        '本次将更新到 ' + NewVersion + '。程序文件会被替换，' +
        '数据和账号全部保留，安装目录和存放位置也不变。'
    else
      WizardForm.WelcomeLabel2.Caption :=
        '检测到已安装的 LAN Share（版本未知）：' + #13#10 +
        PrevInstallDir + #13#10 + #13#10 +
        '本次将更新到 ' + NewVersion + '。数据会原样保留。';
    DataDirPage := nil;
    Exit;
  end;

  DataDirPage := CreateInputDirPage(wpSelectDir,
    '数据存放位置',
    '照片和文件会保存在这里。',
    '默认放在安装目录下的 data 文件夹 —— 你上一步选的那个目录，程序和数据都在里面，' +
    '备份时整个复制走就行。如果 C 盘空间紧张，可以改到别的盘。',
    False, '');
  DataDirPage.Add('');

  // 这里绝对不能写 ExpandConstant 展开 app 常量。
  // 2026-11-01 真机装 3.79 崩了：Runtime error "An attempt was made to
  // expand the app constant before it was initialized"。
  //
  // 真正可读的时机是 CurPageChanged 到了数据目录页 —— 那时 WizardDirValue 必有值。
  DataDirPage.Values[0] := '';
end;

{ 升级时跳过「选择目标位置」和「数据目录」两页：
  两个值都由注册表决定，让用户在这里改只会制造「装出第二个实例」的机会。 }
function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;
  if IsUpgrade then
  begin
    if PageID = wpSelectDir then
      Result := True
    else if (DataDirPage <> nil) and (PageID = DataDirPage.ID) then
      Result := True;
  end;
end;

// 进入数据目录页时，用上一步刚选的安装目录当默认值。
// 这是 app 常量唯一能安全读取的时机。
procedure CurPageChanged(CurPageID: Integer);
var
  Suggested: String;
begin
  if (DataDirPage <> nil) and (CurPageID = DataDirPage.ID) then
  begin
    Suggested := WizardDirValue + '\data';

    // 这里必须区分「用户手动改过」和「还是我们填的」。
    //
    // 老逻辑只在值为空时才填，结果：用户先前进到数据页（自动填了
    // C:\Program Files\LAN Share\data），再退回去把安装目录改成 D:\LANShare，
    // 数据目录还停在 C 盘 —— 程序装 D 盘、数据留 C 盘，用户看到的就是
    // 「我明明选了 D 盘，为什么 C 盘里还有」。2026-11-01 真机反馈的正是这个。
    //
    // 现在的判据：值为空 = 没填过；值等于上次自动填的 = 用户没改过。
    // 两种情况都跟着安装目录走，用户手动改过的一律尊重。
    if (DataDirPage.Values[0] = '') or (DataDirPage.Values[0] = LastAutoDataDir) then
    begin
      DataDirPage.Values[0] := Suggested;
      LastAutoDataDir := Suggested;
    end;
  end;
end;


{ ── 卸载时询问是否保留数据（绝不能默认删用户照片）── }
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataPath: String;
  Answer: Integer;
  ResultCode: Integer;
begin
  if CurUninstallStep = usUninstall then
  begin
    // 在「删程序文件」之前问。
    //
    // 老代码把这个对话框放在 usPostUninstall —— 那时 [UninstallDelete] 早就
    // 把整个 {app}（含 data）删干净了，用户点「是」是删空气，点「否」也
    // 救不回照片。现在 {app}\data 不在 [UninstallDelete] 里，这里问才有意义。
    DeleteDataOnUninstall := False;
    // 此刻注册表还在，GetDataDir 能读到真实值。存下来给 usPostUninstall 用。
    DataPath := GetDataDir('');
    DataPathForPurge := DataPath;
    Log('LANShare: usUninstall 数据路径 = ' + DataPath);
    Log('LANShare: /DELETE_DATA 检测 = ' + IntToStr(Ord(CmdLineHas('/DELETE_DATA'))));

    // 自动化测试专用：/DELETE_DATA 直接选「是」，不用弹框。
    if CmdLineHas('/DELETE_DATA') then
    begin
      DeleteDataOnUninstall := True;
      Log('LANShare: 走 /DELETE_DATA 分支，DeleteDataOnUninstall = True');
    end
    else if DirExists(DataPath) then
    begin
      // 用 SuppressibleMsgBox，不是 WizardSilent + MsgBox。
      //
      // Inno 6.7 里 WizardSilent 只能在安装阶段调，卸载阶段调它会抛
      // 致命异常，整个卸载器当场崩掉：
      //     Runtime error (at 13:154): 内部错误：Cannot call "WizardSilent"
      //     function during Uninstall。
      // 后果是后面的删文件、删服务、清注册表一步都没跑 —— 用户看到的就是
      // 「卸载只删了一部分，还有东西要手动删」。
      //
      // SuppressibleMsgBox 自带静默语义：/SILENT 或 /VERYSILENT 时直接
      // 返回第四个参数（这里给 IDNO = 保留数据），非静默时正常弹框。
      Answer := SuppressibleMsgBox(
        '卸载 LAN Share，是否同时删除数据？' + #13#10 + #13#10 +
        '数据目录：' + DataPath + #13#10 + #13#10 +
        '选「否」（推荐）：保留全部照片、文件和数据库。' + #13#10 +
        '　　　　　　　　　　以后重新安装可以直接接着用。' + #13#10 + #13#10 +
        '选「是」：永久删除上面的整个目录，无法恢复。',
        mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO);
      DeleteDataOnUninstall := (Answer = IDYES);
    end;

    // 真正的删除放在 usUninstall 里做。
    //
    // 实测：usPostUninstall 是卸载器的收尾阶段，那里 Exec / DelTree 都会
    // 「立刻返回成功」但什么也没干 —— 日志里 rmdir 用了 360ms、退出码 0，
    // 200MB 的 pgdata 一个字节没少。usUninstall 是正常阶段，Exec 真生效。
    //
    // 先按进程名杀掉占着数据目录的（CLI 卸载在 [UninstallRun] 里，还没跑，
    // 此刻 postgres/caddy 依然活着）。
    if DeleteDataOnUninstall and (DataPathForPurge <> '') and DirExists(DataPathForPurge) then
    begin
      Log('LANShare: 开始删除数据目录 ' + DataPathForPurge);

      { 先按「服务」停，不要 taskkill。两个理由：

        1. NSSM 配的是 AppExit Default Restart + AppRestartDelay 3000 ——
           强行杀掉被托管的进程，3 秒后它就把服务重新拉起来，正好和下面的
           rmdir 撞车。这就是「点了是，但 data 还在」的另一半原因。
        2. taskkill /IM 是按映像名**全机**杀。用户机器上可能跑着另一个
           PostgreSQL（自己开发用的）或别的 Python 程序，会一起被干掉，
           而且没有任何提示。 }
      Exec('powershell.exe',
           '-NoProfile -Command "foreach ($s in @(''' + GetPrefix() + '-api'','''
           + GetPrefix() + '-caddy'',''' + GetPrefix() + '-postgresql''`)) '
           + '{ Stop-Service $s -Force -ErrorAction SilentlyContinue }"',
           '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
      Sleep(3000);

      { 补刀：服务停了进程可能还在。按**安装路径**过滤，不碰别人的同名进程。 }
      Exec('powershell.exe',
           '-NoProfile -Command "Get-Process postgres,caddy -ErrorAction SilentlyContinue '
           + '| Where-Object {{ $_.Path -like ''' + ExpandConstant('{app}') + '\*'' }} '
           + '| Stop-Process -Force -ErrorAction SilentlyContinue"',
           '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
      Sleep(800);

      Exec(ExpandConstant('{cmd}'), '/c rmdir /s /q "' + DataPathForPurge + '"',
           '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
      Log('LANShare: rmdir 退出码=' + IntToStr(ResultCode));

      if DirExists(DataPathForPurge) then
      begin
        Sleep(2000);
        Exec(ExpandConstant('{cmd}'), '/c rmdir /s /q "' + DataPathForPurge + '"',
             '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
        Log('LANShare: rmdir 重试退出码=' + IntToStr(ResultCode));
      end;

      { pgdata 是 data_dir 的**同级**目录（3.95 搬出来的），不在它里面。
        只删 data 会留下整个数据库（账号、口令哈希、文件索引），而 PG 的
        密码锚点 .lanshare-pgpass 恰恰在 data 里 —— 于是变成「数据看起来
        删了、几百 MB 的库还在、密码没了」。下次重装到同一路径：
        is_initialized() 因为 PG_VERSION 还在返回 True → 跳过 initdb →
        新密码对不上旧库 → 认证失败 → 卡在「PostgreSQL 在 30 秒内没有
        接受连接」，装多少次都失败。 }
      if DirExists(ExtractFileDir(DataPathForPurge) + '\pgdata') then
      begin
        Log('LANShare: 删除数据库目录');
        Exec(ExpandConstant('{cmd}'), '/c rmdir /s /q "'
             + ExtractFileDir(DataPathForPurge) + '\pgdata"',
             '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
      end;

      if DirExists(DataPathForPurge) then
      begin
        Log('LANShare: 数据目录仍存在');
        SuppressibleMsgBox('数据目录没有完全删除：' + #13#10 + DataPathForPurge + #13#10 + #13#10 +
                           '可能有进程还占用着文件。重启电脑后可以手动删除这个目录。',
                           mbInformation, MB_OK, IDOK);
      end
      else
        Log('LANShare: 数据目录已删除');
    end;
  end
  else if CurUninstallStep = usPostUninstall then
  begin
    // 数据已经在 usUninstall 阶段删过了 —— 那里 Exec 才真正生效。
    // 这里只清理 Inno 自己留下的空壳。
    Log('LANShare: usPostUninstall 收尾');
    if DeleteDataOnUninstall then
    begin
      DeleteFile(ExpandConstant('{app}\unins000.dat'));
      RemoveDir(ExpandConstant('{app}'));
    end;
  end;
end;

{ ── 文件就位后，把服务编排交给 lanshare_cli ── }
{ 用 Exec 而不是 [Run]，唯一原因是 [Run] 拿不到返回码，失败了安装器还以为成功。 }
procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
  Args: String;
  PyExe: String;
begin
  { 覆盖文件之前必须先停服务。

    python.exe / caddy.exe / *.dll 正被占用时，Inno 复制文件会失败并整体
    回滚 —— 这就是 3.97 实测的 exit 5。停完之后由下面的 ssPostInstall
    阶段调 lanshare_cli install 重新拉起，用户感知不到中间这段。 }
  if CurStep = ssInstall then
  begin
    if IsUpgrade then
      StopServicesForUpgrade();
    Exit;
  end;

  if CurStep <> ssPostInstall then
    Exit;

  PyExe := ExpandConstant('{app}\runtime\python.exe');
  { --force：允许覆盖已有服务（停旧服务、不删数据）。
    没有它就等于"这机器上曾经装过就再也装不上"，而安装器本来就是干重装的活。 }
  Args := '-m lanshare_cli install --force'
        + ' --data-dir "' + GetDataDir('') + '"'
        + ' --api-port {#ApiPort}'
        + ' --web-port {#WebPort}';

  { SW_SHOWMINNOACTIVE：给用户一个能瞄一眼进度的窗口，但不抢焦点。
    完全隐藏的话，装 40 秒黑屏没动静，用户会以为卡死了。 }
  if not Exec(PyExe, Args, ExpandConstant('{app}'),
              SW_SHOWMINNOACTIVE, ewWaitUntilTerminated, ResultCode) then
  begin
    if not WizardSilent then
      MsgBox('无法启动配置程序：' + #13#10 + PyExe + #13#10 + #13#10 +
             '安装文件已就位，但服务没有配置。', mbError, MB_OK);
    Exit;
  end;

  if ResultCode <> 0 then
  begin
    InstallStepFailed := True;
    { 静默安装时绝不能弹框：脚本里的 MsgBox 不受 /SUPPRESSMSGBOXES 控制，
      会一直等人点，自动安装永远不返回。 }
    if not WizardSilent then
      MsgBox('服务配置失败（错误码 ' + IntToStr(ResultCode) + '）。' + #13#10 + #13#10 +
             '安装文件已经放好了，只是服务没起来。可以手动重试：' + #13#10 +
             '  cd /d "' + ExpandConstant('{app}') + '"' + #13#10 +
             '  runtime\python.exe ' + Args + #13#10 + #13#10 +
             '具体原因看：' + ExpandConstant('{app}') + '\logs\', mbError, MB_OK);
  end;

  { 升级装完把托盘拉回来。

    静默安装不会勾选 [Tasks] 里的 autostart，而用户原来的托盘刚好被我们在
    ssInstall 阶段关掉了 —— 不拉回来的话，他会以为程序装没了。
    拉起失败不影响安装结果（服务已经起来了，用户也能自己点快捷方式）。 }
  if IsUpgrade then
  begin
    if FileExists(ExpandConstant('{app}\tray\LANShareTray.exe')) then
      Exec(ExpandConstant('{app}\tray\LANShareTray.exe'), '',
           ExpandConstant('{app}'), SW_SHOWNORMAL, ewNoWait, ResultCode);
  end;
end;
