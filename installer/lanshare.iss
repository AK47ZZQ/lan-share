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
#define AppVersion     "3.73"
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
; data 目录默认放在 ProgramData（所有用户可访问，且不会被卸载误删）
Name: "{commonappdata}\LANShare\data"; Permissions: users-modify
Name: "{commonappdata}\LANShare\logs"; Permissions: users-modify
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
; ── 托盘开机自启（HKA = 安装模式下自动选对根键，避免 admin 装到错误账户）──
Root: HKA; Subkey: "SOFTWARE\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "LANShareTray"; ValueData: """{app}\tray\LANShareTray.exe"""; Tasks: autostart; Flags: uninsdeletevalue
; ── 安装路径加入 PATH（方便命令行调 python.exe）──

[Icons]
; 桌面和开始菜单的主图标都指向**托盘程序**，不是网页。
; 托盘才是控制面板入口（启停服务、看状态、复制地址），比直接开网页有用。
; 网页那个保留在开始菜单，名字写明白是「在浏览器中打开」。
Name: "{commondesktop}\{#AppName}"; Filename: "{app}\tray\LANShareTray.exe"; Tasks: desktopicon
Name: "{group}\{#AppName}"; Filename: "{app}\tray\LANShareTray.exe"
Name: "{group}\在浏览器中打开"; Filename: "https://127.0.0.1:{#WebPort}"
Name: "{group}\打开数据文件夹"; Filename: "{code:GetDataDir}"
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"

[Run]
; 真正的安装动作不在这里 —— 见 [Code] 里的 CurStepChanged。
; 服务注册、Caddyfile 生成、端口检查、启动、健康检查全部由 lanshare_cli 完成，
; 失败会打印人话错误 + 日志路径，不用在 Pascal 里跟 nssm 的命令行搏斗。
;
; 这里只放「装完之后给用户看的东西」。

; ── 打开浏览器到首启页面（HTTPS —— CLI 生成的 Caddyfile 用 tls internal 自签）──
Filename: "https://127.0.0.1:{#WebPort}"; Flags: shellexec nowait postinstall skipifsilent skipifdoesntexist; Description: "打开 LAN Share（首次使用需创建管理员账号）"

; ── 启动托盘 ──
; 去掉 postinstall：那标志只在「安装完成」页勾选时才执行。
; 装完如果弹了任何错误（哪怕只是自检误报），完成页可能根本不出现，
; 托盘就永远不启动 —— 用户装完桌面上什么都没有，以为没装上。
; 现在安装流程走到这里就起；只有 /SILENT 才跳过。
Filename: "{app}\tray\LANShareTray.exe"; Flags: nowait skipifsilent skipifdoesntexist; Tasks: autostart

[UninstallRun]
; 撤销安装 = 撤销 lanshare_cli install 做的事：先停服务再删服务，数据保留。
; 顺序由 CLI 内部保证（它知道要先停 caddy 再停 api）。
; 不带 --purge-data：删用户照片这种事必须问人，不能由卸载脚本决定。
; 刻意不传 --data-dir：卸载只需要停服务+删服务，数据目录只在 --purge-data 时用到。
; 而且卸载阶段向导页早已销毁，{code:GetDataDir} 会拿到失效状态 —— 传了反而出错。
Filename: "{app}\runtime\python.exe"; Parameters: "-m lanshare_cli uninstall"; Flags: runhidden waituntilterminated

[UninstallDelete]
; 删掉「运行时才生成、不在安装清单里」的东西 —— 不写的话卸载后目录里会剩一堆
; __pycache__ / Caddyfile / 日志，用户看着像没卸干净。
; 用户数据在 ProgramData 里，跟这里无关，绝不在此删除。
; 直接把安装目录整个删掉。安装目录里全是程序文件（运行时、源码、二进制），
; 运行时还会生成各层 __pycache__ 和日志，逐个列不现实。
; 用户数据在 {commonappdata}\LANShare\data，跟这里无关，绝不会被波及。
Type: filesandordirs; Name: "{app}"

[Code]
var
  DataDirPage: TInputDirWizardPage;

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
  Result := ExpandConstant('{commonappdata}\LANShare\data');
end;

procedure InitializeWizard;
begin
  DataDirPage := CreateInputDirPage(wpSelectDir,
    '选择数据存放位置', '照片和文件会保存在这里，请选一个空间充足的位置。',
    '默认位置是 C:\ProgramData\LANShare\data。如果 C 盘空间紧张，' +
    '建议改到其他盘（比如 D:\LANShare）。以后备份就是把这个目录整个复制走。',
    False, '');
  DataDirPage.Add('');
  DataDirPage.Values[0] := ExpandConstant('{commonappdata}\LANShare\data');
end;


{ ── 卸载时询问是否保留数据（绝不能默认删用户照片）── }
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataPath: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataPath := GetDataDir('');
    if DirExists(DataPath) then
    begin
      if WizardSilent then
      begin
        { 静默卸载一律保留数据 —— 删用户照片这种决定不能让脚本替人做 }
      end
      else if MsgBox('是否删除所有数据？' + #13#10 + #13#10 +
                '数据目录：' + DataPath + #13#10 + #13#10 +
                '选「否」将保留全部照片、文件和数据库，' +
                '以后重新安装可以继续使用（推荐）。' + #13#10 +
                '选「是」会永久删除，无法恢复。',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
      begin
        DelTree(DataPath, True, True, True);
      end;
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
  Args := '-m lanshare_cli install'
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
