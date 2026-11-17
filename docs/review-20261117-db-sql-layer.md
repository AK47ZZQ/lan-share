# LANShare 数据层与 SQL 构造复审

日期：2026-11-17 · 范围：`app/db.py`、`app/store/*`（含 `schema.py` 571 行）、全仓 SQL 构造点（`app/` + `lanshare_cli/` + `scripts/`）
方法：AST 穷举全部 `execute/executemany/executescript` 调用点并逐个分类（不抽样）；动态 SQL 逐个回溯到调用方；关键结论用本地 SQLite / TestClient 复现。
标注：**[实测]** = 已跑出真实结果；**[读码]** = 代码路径确证、未构造完整运行环境。

---

## 0. 结论摘要

| # | 问题 | 位置 | 严重度 |
|---|------|------|--------|
| 1 | 写出口 `_write` 异常不回滚：SQLite 悬挂写事务持 RESERVED 锁，全站写入被堵 30s 后 500 | store 9 个模块 + filetools/uploads | **P1** [实测] |
| 2 | `GET /api/photos` 对 guest / 非 ASCII 用户名 → `UnboundLocalError` 500 | photos.py:416-429,470 | **P1** [实测] |
| 3 | 迁移脚本 DELETE+INSERT 全程 autocommit，无事务：中断 = 生产库半清空 | scripts/migrate_sqlite_to_pg.py:85-102 | **P1** [读码] |
| 4 | `sync_grants_on_move` 号称"事务内"实则无事务；UNIQUE 冲突时部分改写 + 悬挂事务 | spaces.py:286-294（3 个调用点） | **P2** [读码] |
| 5 | 改名流程 4 段各自提交（用户名/专辑/目录/shares+trash），崩溃窗口留永久错位 | services/accounts.py:57-110 | **P2** [读码] |
| 6 | 广播"已发送"先落库、投递失败无补偿 → 0 人收到但状态 sent，无法重试 | notify.py:161-167 | **P2** [读码] |
| 7 | 定时广播忽略 CAS rowcount：与手动发送竞态时重复投递全员 | notify.py:306-311 | **P2** [读码] |
| 8 | 投递提交后异常触发补偿回 draft；无 `(broadcast_id,user_id)` 唯一键 → 重试重复通知（可疑/低概率） | notify.py:141-148,242-253 | **P3** [读码] |
| 9 | PG 连接回收可强关另一线程正在用的连接（psycopg 连接非线程安全） | db.py:239-247,299-308 | **P2** [读码] |
| 10 | `row[0]` 读 PG dict_row → KeyError 被吞，`audit_keep_days` 静默失效 | services/cleanup.py:181-184 | **P2** [读码] |
| 11 | SQLite 连接未开 `PRAGMA foreign_keys=ON`：schema 的 CASCADE / SET NULL 全部不执行，删号留孤儿行 | db.py:310-312 | **P2** [实测] |
| 12 | `transaction()` 在 SQLite 上不支持嵌套：内层退出即 commit，外层回滚失效 | db.py:177-190 | **P2** [实测] |
| 13 | `is_unique_violation` 把所有 `sqlite3.IntegrityError` 当唯一冲突（NOT NULL/FK 也命中） | db.py:201-206 | **P3** [实测] |
| 14 | LIKE 转义三处缺口：note_search 私有路径缺 ESCAPE（功能丢失）/console 只去 `%`/photos 未转义 | note_search.py:52、console.py:163、photos.py:450-467 | **P3** [实测] |
| 15 | 动态 SQL 拼接点（where/order/列名）目前白名单安全，但无结构性约束 | photos.py:61-70、todos.py:124-130、users.py:90-99 | **P3** [读码] |
| 16 | 空列表生成 `IN ()`（PG 语法错误），当前靠调用点守卫 | notifications.py:43-57 | **P3** [读码] |
| 17 | `executescript` 按 `;` 切分 + 逐条 autocommit：PG 建库非原子 | db.py:152-155、schema.py:292,310 | **P3** [读码] |
| 18 | `_pg_sql` 状态机不识别 `--` 注释/双引号标识符，`?` 会漏翻译 | db.py:69-85 | **P3** [实测] |
| 19 | 无幂等键：管理端广播、待办双击重复插入 | notify.py:151-168、todos.py:224-242 | **P3** [读码] |
| 20 | `share_prepare` 先扣下载次数再发票据，两次写各自提交 | api/share.py:265-274 | **P3** [读码] |

**SQL 注入：0 处。** 245 个 SQL 执行点全部核对，34 个非字面量 SQL 参数逐个回溯，没有任何用户输入进入 SQL 文本（见 §1 证据表）。

---

## 1. 注入面：穷举结论（P0 = 无）

### 1.1 方法与计数

- `grep` + AST 双路枚举：`app/`、`lanshare_cli/`、`scripts/` 共 **245** 个 `execute/executemany/executescript` 调用点。
- 其中 **34** 个的 SQL 参数不是纯字面量（f-string / 拼接 / 变量），全部逐一回溯到构造者。
- `grep -E "(execute|...)\s*\(.*(f\"|f'|\.format\(|%\s*\()"`：无一处把值格式化进 SQL。

### 1.2 全部动态 SQL 构造点与判定

| 位置 | 构造方式 | 输入来源 | 判定 |
|---|---|---|---|
| `store/users.py:98` | `f"SELECT {col} FROM users WHERE id=?"` | `_ALLOWED_FIELDS` frozenset；唯一调用方 `api/auth.py:209` 传字面量 `"password_hash"` | 安全 |
| `store/todos.py:128-129` | `f"UPDATE todos SET {cols} WHERE id=?"`，`cols` 来自 dict 键 | 唯一调用方 `api/todos.py:251-274`，键全是字面量 | 安全 |
| `store/todos.py:147-148` | `IN ({marks})` | `len(ids)` 生成的 `?` 序列 | 安全 |
| `store/notifications.py:43-47,54-57` | `IN ({marks})` | `len(target_ids)`；`target_ids` 来自库内 JSON | 安全（空列表见 §5-14） |
| `security.py:51-59` | `NOT IN ({marks})` | `len(_AUDIT_SECURITY_ACTIONS)` 常量 | 安全 |
| `store/photos.py:61-70` | `WHERE {where_sql} ORDER BY {order_sql}` | 唯一调用方 `api/photos.py:469-475`；`order` 由 `Query(pattern="^(taken\|added)$")` 派生常量，`where` 只由常量片段 + 绑定参数拼成 | 安全（结构脆弱，见 §5-13） |
| `store/schema.py:264-266` | `setval(... '{table}' ...)` | 模块内 `_PG_SEQUENCES` 元组 | 安全 |
| `scripts/migrate_sqlite_to_pg.py:51,93,94,97` | 表名/列名 f-string | 模块内 `TABLE_ORDER` + 目标库反射列名 | 安全（但见 §3-3） |
| `lanshare_cli/pg.py:550,563` | `sql.SQL(...).format(sql.Identifier, sql.Literal)` | 管理员输入的库名/口令 | 安全（psycopg 正确转义） |
| `api/console.py:161-166` | `"... FROM audit_log" + where` | `where` 是常量 `" WHERE action LIKE ?"` | 安全 |
| `store/note_search.py:44-54` | `sql += " AND (... LIKE ?)"` | 常量片段 | 安全（ESCAPE 缺陷见 §5-12） |
| `app/db.py:116,121` | 占位符方言翻译 | 上游 SQL | 非注入面 |

### 1.3 LIKE 通配符

- `store/note_search.py:77-78` 的 `_escape_like` + `ESCAPE '\'`：**正确**（`\` → `\\` 顺序也对）。
- `api/photos.py:418-429` 的私有前缀 `esc`（`\`/`%`/`_` 三步转义 + `ESCAPE '\'`）：**正确**。
- 其余缺口见 §5-12，均不构成越权（Python 侧 `visible()` 无条件兜底，`photos.py:480`）。

### 1.4 排序字段 / 表名

- 排序：`sort` 走 `Query(pattern=...)`，映射到两个常量串（`photos.py:432-433`），用户原文不进 SQL。
- 表名：全部来自模块内元组/常量，无请求参数路径。

**结论：未发现 SQL 注入。** 唯一"用户输入进入 SQL 字符串"的路径是列名/表名类，且都被白名单或常量截断。

---

## 2. P1：写出口异常不回滚，SQLite 全站写锁被悬挂事务占死

**位置**

```python
# app/store/todos.py:151-157（同款复制在 8 个 store 模块）
def _write(sql: str, params: tuple) -> int:
    with _db_lock:
        cur = _db().execute(sql, params)     # ← 这里抛 IntegrityError/OperationalError
        n = cur.rowcount
        if not _dbmod.in_transaction():
            _db().commit()                   # ← 永远走不到
        return n
```

同款：`store/users.py:294-303`、`shares.py:111-117`、`trash.py:98-104`、`notifications.py:240-250`、`ttl_state.py:150-155`、`devices.py:117-123`、`note_search.py:81-87`、`photos.py:99-107,114-118,125-133,140-150`；另 `filetools.py:224-228`、`api/uploads.py:74-79,117-122,126-128` 是手写版。

**为什么危险（已复现）**

`sqlite3` 在 DML 前隐式 `BEGIN`。语句失败后事务**仍然开着**，而 `_db_lock` 已释放 —— 这条线程的连接持有 RESERVED 锁，其它线程的任何写入都会在 `busy_timeout=30000` 里空等 30 秒后抛 `database is locked`。复现：

```
A raised: IntegrityError UNIQUE constraint failed: t.name
A in_transaction: True
B FAILED after 2.25s: database is locked      # timeout=2 的等价放大
B insert after A commit OK in 0.001s          # 只有 A 之后再写并 commit 才解锁
```

可达触发（都很日常）：

- `todos.create_list` 先查重再插入（`api/todos.py:157-164`），两个请求并发同名 → 一个撞 `UNIQUE(user_id,name)` → 500 + 悬挂事务。
- `sync_grants_on_move` 改名撞 `UNIQUE(user_id,path)`（见 §3-4）：更糟 —— 之前几条 UPDATE 已执行但未提交，**后续任意一次 commit 会把半套授权改写落库**（部分成功）。
- `devices.insert` 的 `token_hash` 碰撞重试路径、`shares.create` 的 token 碰撞同理。

项目自己已经踩过并只修了一处：`security.audit` 显式 `rollback()` 并留了注释「锁内带着未决写事务逸出会锁死该连接上的后续写操作」（`security.py:61-65`）。**其余 16 个写出口没有这一层**（8 个 store `_write` + photos 的 4 个批量方法 + filetools 收件箱写入 + uploads 的 3 个助手）；`settings.set_setting`、`services/albums._save_albums/mutate_albums` 是单语句 + 立即 commit，异常时同样不回滚。

**修法**：`_write` 统一 `try/except BaseException: rollback; raise`；或让 `_write` 委托 `db.transaction()`（SQLite 分支已有 rollback 语义），并把 `filetools/uploads/settings/albums` 的手写 commit 一并收口。

---

## 3. P1：迁移脚本无事务，中断即半清空生产库

**位置** `scripts/migrate_sqlite_to_pg.py:85-102`

```python
for table in TABLE_ORDER:
    rows = src.execute(f"SELECT {', '.join(cols)} FROM {table}").fetchall()
    pg.execute(f"DELETE FROM {table}")
    if rows:
        pg.executemany(f"INSERT INTO {table}(...) VALUES(...)", ...)
pg_setval_sequences(pg)
```

**_PgConn 是 autocommit=True**（`app/db.py:112`），所以每条 `DELETE`/`executemany` 立即提交，`transaction()` 也没包。任何中断（Ctrl-C、连接断、某表列类型不兼容）都会留下：**已清空但未灌入的表 + 已灌入的表 + 序列未拨正**，且脚本没有恢复点。目标库是生产 `public`（脚本不建临时 schema），这是本仓风险最高的一次性破坏路径。

同类但较轻：`pg_setval_sequences` 用 `COALESCE(MAX(id),1)`，空表 `setval(...,1)` 后下一个 id 是 2（跳号，无害）。

**修法**：整轮 `with pg.transaction():`（或给 `_PgConn` 加一个 autocommit=False 的迁移连接），失败整体回滚；`DELETE` 前先做 `pg_dump` 快照。

---

## 4. P1：`GET /api/photos` 对 guest 与非 ASCII 用户名直接 500

**位置** `app/api/photos.py:416-429, 469-470`

```python
416|    if role in (ADMIN, MEMBER) and user["username"].isascii():
417|        pushdown = True
...
424|            args: list = [own_priv]          # 只在这个分支里赋值
429|            args = [own_priv, f"incoming/{esc}/%"]
...
469|    _where = cond if pushdown else ""        # 条件表达式，安全
470|    _args = list(args)                       # ← pushdown=False 时 args 未绑定
```

**为什么危险（已复现）**：`guest` 角色、以及任何非 ASCII 用户名的 member/admin，`pushdown` 为 False，`args` 从未赋值 → `UnboundLocalError`。用真实 TestClient 复现：

```
login 200
EXC: UnboundLocalError cannot access local variable 'args' where it is not associated with a value
STATUS 500 Internal Server Error
```

guest 是正式角色（`app/roles.py`、邀请码可建），非 ASCII 用户名（中文名）在家庭场景很常见 —— 这是一个角色级别的照片墙全挂。注释 `photos.py:410` 写的是「guest 仍走 Python 过滤」，说明意图是支持 guest，纯粹是漏初始化。

**修法**：`args: list = []` 提到 `if` 之前（`cond = ""` 同理），或把 `_args` 改成 `list(args) if pushdown else []`。

---

## 5. P2/P3：事务边界与部分成功

### 5.1 `sync_grants_on_move`：docstring 说"事务内"，实际没有事务

**位置** `app/spaces.py:269-297`

```python
269|    """...（E6，事务内）。..."""
...
286|    with _db_lock:
287|        for r in _db().execute("SELECT id, path FROM grants").fetchall():
289|            if gp == ok_old:
290|                _db().execute("UPDATE grants SET path=? WHERE id=?", ...)
292|                _db().execute("UPDATE grants SET path=? WHERE id=?", ...)
294|        _db().commit()
```

三个调用点 `api/files.py:158`、`files.py:385`、`api/webdav.py:391` **都在 `transaction()` 之外**。PG autocommit 下每条 UPDATE 独立提交：中途失败 → 一部分 grant 指新路径、一部分指旧路径（fail-closed，guest 失去访问，需手工修库）。SQLite 下则叠加 §2 的悬挂事务。

### 5.2 改名流程四段各自提交

**位置** `app/services/accounts.py:57-110`

顺序：`set_username`（自 commit）→ `rename_album_owner`（自 commit）→ 私有目录 `rename` → shares/trash 路径改写（另一次 commit）。第 2→3 步之间崩溃：用户名已改、专辑归属已改、目录还是旧的；第 3→4 步之间崩溃：`shares.path`/`trash.orig_path` 仍指 `private/<旧名>/…`，分享链接 404、回收站恢复到重新 mkdir 出来的旧目录（代码注释自己列了这些后果，但只对"目录 rename 失败"做了回滚，没覆盖进程崩溃）。目录 rename 成功但第 4 步异常时，同样留下不一致。

**修法**：把 1/2/4 步（纯 DB）包进一个 `transaction()`，文件 rename 放最后并保留现有补偿；或引入"待迁移路径"表做两阶段。

### 5.3 广播：状态先落库，投递无补偿

**位置** `app/api/notify.py:161-167`

```python
162|    notif_store.create_broadcast(bid, ..., "sent", time.time(), sent_at=time.time())
166|    sent = _deliver_broadcast(bid, title, text, targets)
```

`create_broadcast` 独立提交。`_deliver_broadcast` 若抛错（DB 故障、目标解析异常），广播记录已是 `sent`：管理端显示"已发送 0 人"，`send_draft` 的 CAS 又会 409，**无法重试**。草稿发送路径有 `_deliver_with_compensation`（notify.py:242-253），这条即时发送路径没有。

### 5.4 定时广播忽略 CAS 结果 → 重复投递

**位置** `app/api/notify.py:306-311`

```python
309|            notif_store.mark_broadcast_sent(r["id"], time.time())
310|            _deliver_with_compensation(r["id"], r["title"], r["body"] or "", targets)
```

`mark_broadcast_sent` 是 CAS（`WHERE status!='sent'`，`store/notifications.py:188-191`），设计意图是"只有赢家投递"。这里 **rowcount 被丢弃**。`list_due_broadcasts` 选中记录后、mark 之前，管理员手动 `POST /draft/{bid}/send` 抢先置 sent 并投递 —— 定时循环仍然会再投一遍，全员两条相同通知。`admin_send_draft`（:266-268）是有判断的，这一处漏了。

### 5.5 [可疑/低概率] 投递提交后异常 → 补偿回 draft → 重试重复通知

**位置** `app/api/notify.py:141-148` + `242-253`

```python
141|    with _db_lock, transaction():
142|        for t in targets:
144|            if insert_notification(...): sent += 1
146|    if webhook:
147|        push_webhook(title, text)          # ← 事务外
```

站内通知已提交；若此后抛异常，`_deliver_with_compensation` 会 `revert_broadcast_to_draft`，管理员重试时上一轮已收到的人会再收一条（`notifications` 没有 `(broadcast_id,user_id)` 唯一约束，去重无处可依）。

需要说明：`push_webhook`（`services/webhook.py:110-124`）本身把网络失败吞进线程池并只记审计，**不会**抛网络异常；能走到补偿的是它之前的 `get_webhook_url()`（DB 读）或线程池 `submit` 失败。所以这是"设计上会重复"而非"网络上必然复现"。根治仍是给 `notifications` 加 `(broadcast_id,user_id)` 唯一索引 + `ON CONFLICT DO NOTHING`。

### 5.6 PG 连接回收会强关别人正在用的连接

**位置** `app/db.py:239-247, 299-308`

```python
239|        if force_close_oldest and len(keep) >= _PG_MAX_CONN:
240|            keep.sort(key=lambda x: x[2])   # 只看 last_used，不看"是否在用"
241|            conn, _, _ = keep.pop(0)
242|            try: conn.close()
```

`_PG_MAX_CONN` 默认 16，FastAPI 线程池上限 40 —— 并发 17 个请求就触发。`last_used` 只在 `_db()` 调用时刷新；一条正在执行长 SQL（大 DELETE / `executemany` 灌数据）的连接会被别的线程 `close()`。psycopg 连接不是线程安全的，这会产生"connection closed"中途异常甚至协议层错乱。同时 `_db_local.conn` 只清理**当前线程**的引用，被关线程要到下次 `_db()` 才自愈。

**修法**：登记项加 `in_use` 计数（`_db()` 借出/归还），回收只挑 `in_use==0` 且空闲超 TTL 的；`_PG_MAX_CONN` 至少不低于线程池上限。

### 5.7 `row[0]` 读 PG dict_row，配置静默失效

**位置** `app/services/cleanup.py:181-184`

```python
181|            row = _db().execute(
182|                "SELECT value FROM settings WHERE key='audit_keep_days'").fetchone()
183|            if row and row[0]:
184|                keep_days = max(30, int(row[0]))
185|        except Exception:
186|            pass
```

PG 行是 `dict_row`，`row[0]` 抛 KeyError → 被 `except Exception: pass` 吞掉 → 永远用 730 天。这正是 `console.py:77-80` 注释里警告的"测试过、生产炸"的双引擎盲区，只是这次被静默降级掩盖了。当前没有 UI 写这个 key（只能手改库），所以是 P2 而非 P1；但"审计保留期可配"在生产上是不成立的。

### 5.8 SQLite 未启用外键：schema 的 CASCADE / SET NULL 全部不生效

**位置** `app/db.py:310-312`

```python
310|            conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
311|            conn.row_factory = sqlite3.Row
312|            conn.execute("PRAGMA busy_timeout=30000")
```

全仓没有任何 `PRAGMA foreign_keys=ON`（`grep -rn foreign_keys app/ tests/ scripts/` 零命中）。SQLite 默认 `foreign_keys=0`，实测：

```
default foreign_keys = 0
children left after parent delete = 1
```

后果（SQLite 部署与全部测试）：

- `admin_delete_user`（`api/admin_users.py:189-193`）只显式删 `sessions`；`grants`、`notifications`、`device_tokens`、`todo_lists`(→`todos`)、`pomodoro_sessions` 的 `ON DELETE CASCADE` 不执行 → 删号留一堆孤儿行。
- `shares.owner_id/target_user_id` 的 `SET NULL` 不执行 → 定向分享在收件人被删后 `target_user_id` 仍指向死 uid，而 uid 不复用 ⇒ 该分享永久不可访问（admin 除外）。
- `delete_list`（`store/todos.py:101-104`）依赖 `todos` 的 CASCADE；SQLite 上清单删了、待办还在，且 `todos_visible` 的 JOIN 会把它们藏起来 —— 用户看不到也删不掉，只能等墓碑清理（永远等不到，因为 `deleted_at` 为空）。
- `todos.assignee_id/done_by` 的 SET NULL 不执行 → 已删成员仍占着指派位。

这又是"SQLite 测试绿、语义只在 PG 成立"的分叉：**级联删除这条路径在 SQLite 上从未被真正执行过**，测试覆盖是假的。

修法：SQLite 分支加 `conn.execute("PRAGMA foreign_keys=ON")`（每条连接都要设），并补测试"删用户后 grants/notifications/device_tokens 归零"。开启前要先清存量孤儿（尤其 `todos.assignee_id` 指向已删用户时，`sweep_todo_due` 会给不存在的 uid 发通知，FK 一开就会 IntegrityError），建议先跑一遍只读对账再上线。

### 5.9 `transaction()` 的 SQLite 分支不支持嵌套：内层 commit 切断外层

**位置** `app/db.py:161-190`

```python
182|        try:
183|            yield conn
184|            conn.commit()          # ← 每一层都 commit，不看 _txn_depth
185|        except BaseException:
186|            conn.rollback()
187|            raise
```

PG 分支把嵌套委托给 psycopg 的 savepoint；SQLite 分支完全不看刚维护的 `_txn_depth`，内层块正常退出就 `commit()`，把外层已做的工作一起提交。实测：

```
with transaction():
    INSERT settings('outer')
    with transaction():
        INSERT settings('inner')
    raise RuntimeError("boom after inner")
# → rows after outer rollback: ['inner', 'outer']    ← 本应一行都不剩
```

当前全部 8 个 `transaction()` 调用点都是单层（已 grep 核对），所以是**潜在**缺陷；但 `db.py` 的 docstring 与 `_PgConn.transaction` 都在讲"嵌套安全"，下一个在事务里复用某个自带 `with transaction()` 的助手就会静默丢掉外层原子性，且与 §2 的悬挂事务叠加后极难定位。

修法：SQLite 分支按深度收束 —— 只有 `_txn_depth.depth == 1` 才 `commit()/rollback()`；内层直接 `yield conn` 不做事（SQLite 无 savepoint 时这是唯一正确语义）。

---

## 6. P3：其余确认项

### 6.1 `is_unique_violation` 过宽

`app/db.py:201-206`：

```python
if isinstance(e, sqlite3.IntegrityError):
    return True
```

`sqlite3.IntegrityError` 同时覆盖 NOT NULL / CHECK / FOREIGN KEY（实测 `NOT NULL constraint failed` 也命中）。`invite_redeem`（`admin_access.py:126-131`）据此把任何完整性错误都翻译成 409「用户名已被占用」，真实故障被掩盖。应解析 `e.sqlite_errorname`（`SQLITE_CONSTRAINT_UNIQUE` / `SQLITE_CONSTRAINT_PRIMARYKEY`）。

### 6.2 LIKE 转义三处缺口（已实测）

1. `store/note_search.py:52-53`：主 WHERE 有 `ESCAPE '\'`，追加的私有路径条件**没有**：

```python
52|        sql += " AND (LOWER(path) NOT LIKE 'private/%' OR LOWER(path) LIKE ?)"
53|        args.append(f"private/{_escape_like(username.lower())}/%")
```

`_escape_like` 会插 `\`，没有 ESCAPE 时 `\` 是普通字符 → 用户名含 `_`/`%` 时**搜不到自己的私有笔记**。实测：同一 pattern，无 ESCAPE 命中 0，有 ESCAPE 命中 1。修法：给该条件补 `ESCAPE '\'`。

2. `api/console.py:163`：`action.replace("%", "")` 只去 `%`，保留 `_` 单字符通配，且无 ESCAPE → 管理端审计筛选会多匹配。admin-only，影响小。

3. `api/photos.py:450-456, 465-467`：`month + "%"`、`%{ext}`、`p + "/%"` 均未转义 `%`/`_`。参数是绑定的（无注入），Python 侧 `photos.py:486-498` 又做了精确前缀/年月复核，所以只造成 SQL 侧扫描放大（`month=%` 可拉全表），不越权。建议统一走 `_escape_like`。

### 6.3 动态 SQL 拼接点缺乏结构约束

`store/photos.py:61-70` 与 `store/todos.py:124-130` 把 `WHERE`/`ORDER BY`/列名当字符串参数接收，安全性完全依赖调用方纪律：目前 `list_for_gallery` 只有 `api/photos.py:475` 一个调用方、`update_todo` 只有 `api/todos.py:276`，键/片段都是常量，**已核对安全**。但这是全仓唯一"用户可控形状"的 SQL 入口，建议改为：

- `list_for_gallery(filters: list[tuple[col, op, value]], order: Literal["taken","added"])`，col/op 在 store 内做枚举校验；
- `update_todo` 用 `frozenset` 白名单校验键（像 `users._ALLOWED_FIELDS` 那样），目前它是裸 `fields` dict。

### 6.4 空列表 `IN ()`

`store/notifications.py:43-48,54-57`：`",".join("?" * len(target_ids))`，空列表得 `IN ()`。SQLite 容忍，PG 报语法错误。当前两个调用点都有 `if tids:` 守卫（`api/notify.py:182-183,215`），属于"下一个调用者就会踩"的隐患。建议函数内 `if not target_ids: return ...` 早退。

### 6.5 `executescript` 的原子性与切分

- `_PgConn.executescript`（`db.py:152-155`）按 `;` 裸切，逐条 `execute`；autocommit 下建表非原子（失败留半套 schema，重试幂等所以可恢复）。
- 若未来 SQL 文本在字符串字面量或 `--` 注释里出现 `;`，会被切断。当前 `_PG_SCHEMA`/`_INDEXES` 只有全角 `；`，安全。
- `init_db` 的 PG 分支没有事务包住"建表 + ALTER + 索引 + 回填"，同理。

### 6.6 `_pg_sql` 状态机盲区（实测）

`db.py:69-85` 只按 `'` 翻转字符串状态，不识别 `--` 注释、双引号标识符、`E''`。实测：

```python
_pg_sql("-- don't touch\nSELECT a FROM t WHERE b=?")
# → "-- don't touch\nSELECT a FROM t WHERE b=?"   ← '?' 未被翻译
```

PG 会把 `?` 当字面量 → 语法错误（响亮失败，不是静默错改）。当前所有 SQL 都不含"注释里有单引号"的形态，属潜在脆弱点；测试 `test_pg_sql_dialect.py` 只钉了 happy path。建议在建 SQL 时禁止注释，或让 `_pg_sql` 先剥 `--` 到行尾。

### 6.7 幂等缺口

- `POST /api/admin/notify`（`notify.py:151-168`）：双击 = 两条 `bid`、两轮 fan-out，全员收两条。无幂等键。
- `POST /api/todos`（`todos.py:224-242`）：双击 = 两条待办（无 client id / 去重键）。
- 其余写路径已确认幂等或天然唯一：`settings`/`grants`/`blobs` 是 upsert；`upload/finish` 第二次 404（会话已删）；`trash.take` 是 `DELETE ... RETURNING` 原子认领；`shares.consume_download` 是带条件的原子自增。

### 6.8 `share_prepare` 的扣次数与发票据不是原子

`api/share.py:265-274`：`consume_download` 先提交（扣一次上限），随后两次 `ttl_state.put` 各自提交。第二次 put 失败 → 配额被扣、票据没发。另外 `put` 失败会抛 500，客户端重试再扣一次。注释已声明"不包事务"是为了避免 SQLite 长写事务，属有意取舍；建议失败时补偿 `downloads=downloads-1`。

---

## 7. 已核对无问题（避免重复审）

- **schema 迁移**：SQLite 走 `PRAGMA table_info` 逐列 `ALTER`（`schema.py:390-571`），PG 走 `ADD COLUMN IF NOT EXISTS`；均幂等、无改类型、无 `DROP`。PG 历史列已核对：v3.5 初版 `_PG_SCHEMA` 已含 `users.role/is_active/quota_bytes/totp_*`、`shares.owner_id/password_plain/target_user_id`、`audit_log.username`、`upload_sessions.user_id`，后续新列（webdav/totp_rc/nickname/avatar/photos 元数据/broadcast_id）都有显式 ALTER，**不存在"存量 PG 库缺列"**。
- **`test_pg_schema_parity.py`** 只能发现"`_PG_SCHEMA` 文本缺列"，发现不了"存量库缺 ALTER"；当前 ALTER 清单完整，但这是靠人工维护的单点，建议加一条"新增列必须同时出现在 ALTER 列表"的静态断言。
- **写并发（同进程）**：`_db_lock` 是 `RLock`，所有写路径都持有；单 uvicorn worker（`server.py:24`）下这是有效的全局串行化，`busy_timeout=30000` + WAL 配置正确。
- **原子原语**：`trash.take`、`ttl_state.take`、`shares.consume_download` 的 `DELETE/UPDATE ... RETURNING` + 条件 WHERE 正确，`commit` 都放在可能 `raise` 之前（`shares.py:77-81` 有注释钉住）。
- **PG 事务嵌套**：`_PgConn.transaction()` 用 `autocommit` 状态判断外层、依赖 psycopg savepoint 处理内层，`_txn_depth` 与 `in_transaction()` 配合正确；所有 `transaction()` 调用点都同时持有 `_db_lock`（`account.py:52`、`admin_access.py:114`、`admin_users.py:67,126,189`、`console.py:236`、`notify.py:141,198,398`）。**仅 PG 分支正确，SQLite 分支见 §5.9。**
- **上传会话**：`upload_sessions` 的 init/chunk/finish 有 owner 守卫、TTL 清扫、配额三闸；`finish` 失败会销毁会话，重复 finish 幂等 404。

---

## 8. 建议的修复顺序

1. `_write` 统一 `rollback`（§2）—— 一处改动消掉 16 个写出口的锁死路径。
2. `photos.py:470` 初始化 `args`（§4）—— 一行，修一个角色级 500。
3. 迁移脚本包事务 + 迁移前 pg_dump（§3）。
4. `sync_grants_on_move` 真正包进 `transaction()`（§5.1）。
5. 广播三处：先投递后置 sent / 检查 CAS rowcount / 补偿条件收紧（§5.3-5.5）。
6. PG 连接回收加 `in_use`（§5.6）；`cleanup` 改 `row["value"]`（§5.7）。
7. SQLite 开 `foreign_keys=ON` + 存量孤儿对账（§5.8）。
8. SQLite `transaction()` 按 `_txn_depth` 收束（§5.9）。
9. 其余 P3 按需。
