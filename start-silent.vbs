' LAN Share — 静默启动（双击运行，无黑窗）
Set ws = CreateObject("WScript.Shell")
ws.CurrentDirectory = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)

' 创建 logs 目录
CreateObject("Scripting.FileSystemObject").CreateFolder "logs" 

' 启动 FastAPI
ws.Run "cmd /c python server.py >> logs\server.log 2>&1", 0, False

' 等待 FastAPI 就绪
WScript.Sleep 2000

' 启动 Caddy
ws.Run "cmd /c caddy.exe run --config Caddyfile --adapter caddyfile >> logs\caddy.log 2>&1", 0, False
