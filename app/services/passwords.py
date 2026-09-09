"""口令校验服务（07 审查批次 3C：从 api/auth.py 收口）。

scrypt 校验统一下放线程池；哑哈希等时校验原是 auth 私有，webdav 的
Basic 认证函数体内反向 import——登录与 DAV 两侧的防时序策略必须同源，
这里就是那个唯一事实源。
"""
import secrets

from fastapi.concurrency import run_in_threadpool

from app.security import hash_password, verify_password


async def verify_pw(pw: str, stored: str) -> bool:
    return await run_in_threadpool(verify_password, pw, stored)


# 用户名不存在/未设独立密码时也跑一次等价 scrypt（懒初始化哑哈希）：消除
# "不存在用户 0ms / 存在用户 300ms"的时序用户名枚举（R1-P3-2）
_DUMMY_HASH: str | None = None


async def verify_pw_with_dummy(pw: str, stored: str | None) -> bool:
    global _DUMMY_HASH
    if stored is None:
        if _DUMMY_HASH is None:
            _DUMMY_HASH = await run_in_threadpool(hash_password, secrets.token_urlsafe(16))
        stored = _DUMMY_HASH
    return await verify_pw(pw, stored)
