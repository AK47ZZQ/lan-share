"""v3 管理 API 聚合门面：成员/邀请与授权/运维配置三域（B2 按域拆分）。

本文件只做 router 聚合——app/main.py 的 include_router(admin.router) 与全部
URL 保持不变；实现按域住三个子模块：

- admin_users.py  成员列表/凭据重置/停用/删除
- admin_access.py 邀请制建号（含匿名 redeem）+ 目录授权 grants
- admin_ops.py    公网地址 / webhook 配置 / 家庭动态流 / 成员数据导出

权限：除 /api/invites/redeem（凭邀请码匿名）外全部 require_admin。
"""
from fastapi import APIRouter

from app.api.admin_access import router as access_router
from app.api.admin_ops import router as ops_router
from app.api.admin_users import router as users_router

router = APIRouter()
router.include_router(users_router)
router.include_router(access_router)
router.include_router(ops_router)
