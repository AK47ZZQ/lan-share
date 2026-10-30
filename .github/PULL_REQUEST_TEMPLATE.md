## 改了什么

<!-- 一句话说清这个 PR 的目的 -->

## 关联

- 反馈看板条目：`data/反馈/待处理/xxx.md`（如有）
- Issue / 挂账：#

## 验证（必填，不许写"应该没问题"）

- [ ] `check.bat` 全绿（或逐段跑过：pytest / ruff / mypy / pip-audit / UI 分层 / SQLite 方言 / 文档基线）
- [ ] 碰 DB 的改动：加跑了 PG 引擎（`LANSHARE_TEST_PG_URL=...`）
- [ ] 存储层新增读函数：已加进 `tests/test_store_readers_smoke.py`
- [ ] 改了服务端路由/模型：已跑 `scripts/export-openapi.py` 重导出契约
- [ ] 改后端：已重启 `lan-share-api` 并 curl `/healthz` 核过
- [ ] 跨 `app/` + `web/`：两边都做了（`npm run build` + 重启）
- [ ] 前端改动：`cd web && npm run build` 通过

## 文档同步（批次收尾第 4 件）

- [ ] `CHANGELOG.md`
- [ ] `CHANGELOG.md` 顶部批次段 + 测试基线数字
- [ ] `CHANGELOG.md`（本次变更的用户可见影响）
- [ ] `README.md` 版本号（若对外可见）

## 红线自查

- [ ] 没碰真实数据（测试只用 `debug` 账号，数据自建自清，按唯一前缀定位）
- [ ] 没提交凭据（`docs/DEBUG-ACCOUNT.md` / keystore / `pgpass.conf` / `certs/*.pem`）
- [ ] 只 `git add` 了本批次文件（没 `git add -A`）

## 坑位补充

<!-- 如果这次踩了新坑，改完补进 CHANGELOG.md 或相关模块的注释 -->
