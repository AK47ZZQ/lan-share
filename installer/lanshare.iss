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
#define AppVersion     "3.95"
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
DefaultDirName={autopf}\LAN Share
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
; 用 "" 转义内层引号 —— schtasks 的 /tr 参数本身要带引号包住含空格的路径
; （{app} 默认是 C:\Program Files\LAN Share）。
Filename: "{sys}\schtasks.exe"; Parameters: "/create /tn ""LANShareTray"" /tr """"{app}\tray\LANShareTray.exe"""" /sc onlogon /rl highest /f"; Flags: runhidden waituntilterminated; Tasks: autostart

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
; 按进程名精确杀，不用通配。
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM postgres.exe /T"; Flags: runhidden; RunOnceId: "KillPostgres"
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM caddy.exe /T"; Flags: runhidden; RunOnceId: "KillCaddy"
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM python.exe /T"; Flags: runhidden; RunOnceId: "KillPython"

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
  // 原因是 InitializeWizard 跑在向导刚创建、用户还没选安装目录的时候，
  // app 常量此刻是空的（尤其我们把 AppendDefaultDirName 关掉之后，没有任何
  // 默认值可以兜底）。真正的路径要等用户在「选择目标位置」页点下一步才确定。
  //
  // 所以默认值留空，等 CurPageChanged 到了数据目录页再填 —— 那时
  // WizardDirValue 一定有值。
  //
  // 注意：这段用 // 而不是花括号注释 —— 花括号注释里只要出现一个右花括号
  // 就会提前闭合，而这里要提到那个常量名，必然会带出来。
  DataDirPage.Values[0] := '';
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
      Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM postgres.exe /T',
           '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
      Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM caddy.exe /T',
           '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
      Sleep(1500);

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
    { 静默安装时绝不能弹框：脚本里的 MsgBox 不受 /SUPPRESSMSGBOXES 控制，
      会一直等人点，自动安装永远不返回。 }
    if not WizardSilent then
      MsgBox('服务配置失败（错误码 ' + IntToStr(ResultCode) + '）。' + #13#10 + #13#10 +
             '安装文件已经放好了，只是服务没起来。可以手动重试：' + #13#10 +
             '  cd /d "' + ExpandConstant('{app}') + '"' + #13#10 +
             '  runtime\python.exe ' + Args + #13#10 + #13#10 +
             '具体原因看：' + ExpandConstant('{app}') + '\logs\', mbError, MB_OK);
  end;
end;
