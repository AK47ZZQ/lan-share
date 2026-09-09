"""LAN Share 服务入口。

NSSM 服务命令是 `python C:\lan-share\server.py`、uvicorn 是
`uvicorn server:app`——本文件只保留入口身份，全部实现位于 app/ 包
（app 对象在 app.asgi，组装在 app.main）。⚠️ `if __name__ == "__main__"`
的 uvicorn 启动块不可删：漏了服务会秒退（NSSM 显示 PAUSED）。
"""

from app.main import app  # noqa: F401


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="127.0.0.1", port=8766, reload=False, log_level="info")
