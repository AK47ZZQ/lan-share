"""LAN Share v3 兼容 shim。

v3 M0 拆包后全部实现位于 app/ 包；本文件保留是为了：
1. NSSM 服务入口 `python C:\lan-share\server.py` 与 `uvicorn server:app` 不变；
2. 既有测试/脚本 `import server` 继续工作（显式再导出）。
新代码一律从 app.* 导入，不要往本文件加逻辑。
"""

from app.config import PROJECT_ROOT, SHARE_ROOT  # noqa: F401
from app.db import _db, _db_lock, init_db  # noqa: F401
from app.main import app  # noqa: F401
from app.security import _login_fails, _rate, _rate_lock  # noqa: F401
from app.api.auth import user_count  # noqa: F401
from app.api.files import _zips, _zips_lock  # noqa: F401
from app.api.share import _share_fails, _tickets, _tickets_lock  # noqa: F401
