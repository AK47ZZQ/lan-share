# DESIGN-v3 · 归档区（2026-09-13 文档整顿设立）

> **这三份是 v3 立项期的设计稿，部分设计与实际落地不符**——2026-09-13 整顿
> 时经代码核实后归档。**描述"现在是什么样"请看 `项目记忆/01-架构与现状.md`。**

## 为什么归档（均已用代码核实）

| 文件 | 设计稿怎么写 | 实际落地（核实结果） |
|---|---|---|
| `OVERVIEW.md` | 目录树含 `migrations/`（Alembic 环境） | **仓库无 alembic**，走 `init_db` 幂等迁移 |
| `DATABASE.md` | "Alembic 管理 schema / revision 链" | 同上，**Alembic 从未引入** |
| `FRONTEND.md` | Pinia + Naive UI + Vitest | **三者均未引入**（`web/package.json` 无依赖）——实际为 `auth.ts` 单例 + 自封装玻璃拟态组件 + check.bat 门禁 |

三份文件自身也带"实际落地偏离"的批注，属已知偏离，此处归档避免误导。

## 仍然活着的设计文档（`docs/DESIGN-v3/` 根下）

- `APP-DESIGN.md` —— App 技术设计方案（持续更新）
- `APP-REQUIREMENTS.md` —— App 需求规格（产品意图存档，被 APP-DESIGN 引用）
- `APP-VISUAL-SPEC.md` —— **视觉规范 v1.0，改 App 界面前必读**
- `PERMISSIONS.md` —— 角色与空间授权模型（**实现真相在 `app/spaces.py::resolve()`**，
  本文是模型说明）
