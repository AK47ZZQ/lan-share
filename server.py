r"""LAN Share 服务入口。

NSSM 服务命令是 `python C:\lan-share\server.py`、uvicorn 是
`uvicorn server:app`——本文件只保留入口身份，全部实现位于 app/ 包
（app 对象在 app.asgi，组装在 app.main）。⚠️ `if __name__ == "__main__"`
的 uvicorn 启动块不可删：漏了服务会秒退（NSSM 显示 PAUSED）。

端口可通过环境变量 LANSHARE_API_PORT 覆盖（安装器注册服务时用它传端口），
默认 8766 保持向后兼容 —— 现有 env.bat / install-service.bat 都不用改。

（开头的 r 前缀是必需的：路径里的 \l \a 之类会被当成转义序列，
pytest 会报 DeprecationWarning。）
"""

import os

from app.main import app  # noqa: F401


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("LANSHARE_API_PORT", "8766"))
    uvicorn.run("server:app", host="127.0.0.1", port=port, reload=False, log_level="info")
