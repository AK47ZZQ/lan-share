## 改了什么

<!-- 一句话说清这个 PR 的目的 -->

## 关联

Closes #

## 验证

不许写"应该没问题"。

- [ ] `check.bat` 全绿（八段：pytest 基线 / ruff / mypy / pip-audit / UI 分层 / SQLite 方言 / 文档基线 / 安全消融）
- [ ] 碰了数据库的改动：额外跑了 PostgreSQL 引擎（`LANSHARE_TEST_PG_URL=...`）
- [ ] 存储层新增了读函数：已加进 `tests/test_store_readers_smoke.py`
- [ ] 改了路由或模型：已跑 `scripts/export-openapi.py` 重导出契约
- [ ] 改了后端：已重启 `lan-share-api` 并 `curl /healthz` 核过
- [ ] 改了前端：`cd web && npm run build` 通过
- [ ] 同时动了 `app/` 和 `web/`：两边都验过

## 文档

- [ ] `CHANGELOG.md` 记了这一笔
- [ ] 测试基线数字如有变化已同步
- [ ] 用户可见的行为改动写进了 CHANGELOG

## 红线自查

- [ ] 没有提交凭据（`.env` / keystore / `pgpass.conf` / `certs/*.pem`）
- [ ] 测试用的数据是自建自清的，没碰真实文件
- [ ] 截图里没有真实 IP、域名、邮箱、家庭照片
