# LANShare API 输入校验与错误处理复审

日期：2026-11-17 · 范围：`app/api/` 全 20+ 路由模块 + `app/asgi.py`（全局中间件）/ `app/filetools.py` / `app/spaces.py` / `app/security.py` 的输入边界
方法：横向对比同类端点 + 用真实 `TestClient`（临时 `SHARE_ROOT`、SQLite）逐条打请求复现。
标注：**[实测]** = 已在本地实例复现出状态码/响应；**[读码]** = 逻辑确认、未构造完整触发环境。

---

## 0. 结论摘要

| # | 问题 | 位置 | 严重度 |
|---|------|------|--------|
| 1 | `ended_at` 接受 `Infinity`/`NaN`，单请求 500 + **永久**污染该用户番茄统计 | pomodoros.py:50,70-74,102-109 | **P1** |
| 2 | 配额全树扫描 / 回收站搬移 / mkdir 在**事件循环内**同步执行，可冻结全站 | uploads.py:184,377；webdav.py:308；files.py:87,247 | **P1** |
| 3 | 匿名可读反馈收件箱状态（无任何鉴权） | feedback_inbox.py:226-236 | **P2** |
| 4 | `body: dict` 裸字典端点，`{"url":123}` → 500 | admin_ops.py:74-75 | **P2** |
| 5 | 超长字符串无长度上限（JSON body 1MB 全进 DB） | auth/account/uploads/admin_access/notify 等 6 处模型 | **P2** |
| 6 | 目录列表/回收站/反馈列表**无分页**，单响应无上限 | files.py:71、trash.py:104、feedback.py:322 | **P2** |
| 7 | `/api/photos` 在请求内串行 ffprobe（≤300s/请求），可耗尽 40 线程池 | photos.py:132-136,268,406 | **P2** |
| 8 | 403/404 混用构成存在性预言机（回收站 id 可枚举） | trash.py:130,190；uploads.py:269,285；feedback.py:385 | **P2** |
| 9 | 无全局异常处理器：500 返回 `text/plain`，与全站 JSON 契约不一致 | asgi.py（全文无 handler） | **P2** |
| 10 | 同类错误 400 / 422 / 静默截断 三种口径 | 见 §4 矩阵 | **P3** |
| 11 | 枚举值不校验：静默改写默认值 vs 400 vs 422 | todos.py:155、feedback.py:194、photos.py:452 | **P3** |
| 12 | `du.total` 为 0 时三处除零 500（一处已防） | console.py:110、system.py:47、healthreport.py:174 vs healthreport.py:318 | **P3** |
| 13 | `except (sqlite3.Error, OSError)` 在 PG 生产库上捕不到 psycopg 异常 | photos.py:513 | **P3** |
| 14 | 登录失败表以原始用户名为键，1MB 用户名可长期驻留内存 | auth.py:28-30 → security.py:371,390 | **P3（可疑）** |
| 15 | `expires_hours` 无上下界（grants），负数=立刻过期、超大=永不过期 | admin_access.py:149,185 | **P3** |
| 16 | 敏感异常文本回显给客户端（admin/local 面） | admin_ops.py:104,286；local_config.py:103,199,257 | **P3** |

未发现：未认证即可打崩、可通过错误消息读到他人文件名/表结构的路径。

---

## 1. P1 —— 可持久化打崩 / 可冻结全站

### 1.1 [实测] 番茄钟 `ended_at` 接受 `Infinity` / `NaN`：500 且永久污染统计

**位置** `app/api/pomodoros.py:47-50`（模型）、`:70-74`（`int()` 转换）、`:96-109`（finish）

```python
47|class FinishBody(BaseModel):
49|    status: Literal["focus", "abandoned"]
50|    ended_at: float | None = None   # 客户端上报的权威结束时刻；缺省服务端 now

70|def _minutes_of(row) -> int:
72|    if row["status"] == "auto_closed" or row["ended_at"] is None:
73|        return int(row["focus_min"])
74|    return max(0, int((row["ended_at"] - row["started_at"]) / 60))
```

**触发**（已实测，`Content-Type: application/json` 原文发送）：

```
POST /api/pomodoro/{id}/finish
{"status":"focus","ended_at":Infinity}
```

`json.loads` 默认接受 `Infinity`/`NaN`，pydantic v2 的 `float` 默认 `allow_inf_nan=True`，故不会被 422 拦下；`ended_at < started_at` 对 inf 为假，`pomodoros_store.finish()` 先把 **inf 提交进库**，随后 `_minutes_of` 里 `int(inf - t)` 抛 `OverflowError`。

**实测结果**

```
pomodoro start:                    200 {"ok":true,"id":1,...}
pomodoro finish(Infinity):         500 Internal Server Error
pomodoro summary AFTER poison:     500 Internal Server Error     ← 之后每次都 500
pomodoro finish(NaN):              500 Internal Server Error
```

**为什么是 P1**：写入已提交，`/api/pomodoro/summary` 每次都遍历近 30 天行并调用 `_minutes_of`，于是该账号的统计接口**在该行落出 30 天窗口前永久 500**，用户无法自助恢复（没有删除会话的端点）。这是"一个合法请求 → 永久不可用状态"，不是一次性 500。

**修法**：模型加 `Field(ge=0, le=<now+1d>)` 或 `condecimal(allow_inf_nan=False)`；`_minutes_of` 对非有限值 fail-closed 为 0；`finish` 前做一次 `math.isfinite` 检查。

---

### 1.2 [读码] 阻塞调用留在 `async def` 里：配额扫描 / 大目录删除 / mkdir

同一批端点对"慢 IO 不能进事件循环"的处理**内部就不一致**：`/api/copy` 特意写成同步 `def` 让 FastAPI 丢线程池（`files.py:168` 注释"同步 def 跑线程池：copytree 大目录耗时，不阻塞事件循环"），而下面三处是 `async def` 里直接调阻塞函数：

| 位置 | 代码 | 代价 |
|------|------|------|
| `uploads.py:159,184` | `async def upload_init` → `enforce_quota(_user, dest, ...)` | `spaces.py:256-264` 对 `private/<user>` **全树 rglob + 逐文件 stat**。5 万文件的私人目录 → 每次 init 数百 ms~数秒 |
| `uploads.py:340,377` | `async def upload_finish` → `enforce_quota(owner, dest, ...)` | 同上，且 finish 是最热的收尾路径 |
| `webdav.py:268,308` | `async def _dav_put` → `enforce_quota(...)` | 同上；挂载盘同步一次 = 每个文件一次全树扫描 |
| `files.py:80-87` | `async def create_directory` → `target.mkdir(parents=True)` | 网络卷/杀软拦截时可挂数十秒 |
| `files.py:238-247` | `async def delete_file` → `move_to_trash(target, ...)` | `filetools.py:208-218`：`os.replace` 失败时退化为 `shutil.move`（**整树复制**）。删一个 50GB 目录且目标被占用时 = 事件循环冻结数分钟 |

单进程 uvicorn（README 明示单进程），事件循环一卡，SSE、登录、所有 API 一起停。触发条件是"有个大私人目录 / 删个大目录"，属日常操作。

**修法**：`enforce_quota` / `move_to_trash` / `mkdir` 统一 `await run_in_threadpool(...)`（同文件里已有此写法，见 `_session_usage`、`_select_all_trash`）。

---

## 2. P2 —— 信息泄漏与无上限

### 2.1 [实测] 匿名可读反馈收件箱状态（无鉴权）

**位置** `app/api/feedback_inbox.py:226-236`

```python
226|@router.get("/api/feedback/inbox/status")
227|def inbox_status():
228|    """给管理面板看：收件箱开没开、收了多少。"""
```

同文件 `POST /api/feedback/inbox` 要 Bearer token、`/api/admin/feedback-relay` 要 `require_admin`，唯独这个读端点没有 `Depends`。

**实测**：未登录 `GET /api/feedback/inbox/status` → `200 {"enabled":false,"sources":{},"total":0}`。

**泄漏面**：`enabled` 暴露本机是否配置了收件箱 token；`sources` 是**其它部署上报时的 source 名**（人类可读标识，常含姓名/单位）。低敏但明确的横向不一致。

**修法**：`_admin=Depends(require_admin)`。

### 2.2 [实测] 裸 `body: dict`：`{"url":123}` → 500

**位置** `app/api/admin_ops.py:73-75`

```python
73|@router.post("/api/admin/webhook")
74|def admin_set_webhook(body: dict, request: Request, _admin=Depends(require_admin)):
75|    url = (body.get("url") or "").strip()
```

**实测**

```
POST /api/admin/webhook {"url":123}  → 500 Internal Server Error
POST /api/admin/webhook [1,2]        → 422 dict_type（FastAPI 的 dict 形状校验兜住了数组形态）
```

`{"url": 1}`、`{"url": {"a":1}}`、`{"url": ["x"]}` 全部 500（`int.strip()` → AttributeError）。

**这是全项目已修过同一个 bug 后漏掉的一处**：同文件 `admin_ops.py:40-48` 的 `PublicUrlBody` 注释白纸黑字写着"旧实现 body: dict，`{url:123}` 会在 strip() 上炸成 500。Pydantic 模型让类型错直接 422"，而下面 30 行的 webhook 端点仍是裸 dict。

`app/api/healthreport.py:351` 也是 `body: dict`，但只做 `body.get()` + `str()`，实测 `[1,2]` → 422、`{"time":"abc"}` → 400，不会 500 —— 属同类反模式，风险低。

**修法**：`class WebhookBody(BaseModel): url: str = ""`。

### 2.3 [实测+读码] 超长字符串：只有 3 个模块设了 `max_length`

同一类"用户提交字符串"，各模块口径完全不同：

| 端点 | 模型字段 | 上限 |
|------|----------|------|
| `todos.py:39,44,49` | `name` / `title` / `notes` | `Field(max_length=60/200/2000)` ✅ |
| `feedback_inbox.py:45-57` | 全部字段 | `Field(max_length=...)` ✅ |
| `feedback_inbox.py:239-249` | relay 配置 | `Field(max_length=...)` ✅ |
| `auth.py:28-30` `AuthBody` | `username` / `password` | **无** ❌ |
| `account.py:24-27` `PasswordBody` | `old_password` / `new_password` | **无** ❌ |
| `account.py:64-67` `UsernameBody` | `new_password` 之外的 `password` | **无** ❌ |
| `uploads.py:83-88` `UploadInitBody` | `dest_dir` / `filename` / `sha256` | **无** ❌ |
| `admin_access.py:82-86` `RedeemBody` | `code` / `username` / `password` | **无** ❌ |
| `admin_users.py:92-94` `AdminCredBody` | `value` | **无** ❌ |
| `notify.py:120-123` `NotifyBody` | `title` / `body` | **无**（存前 `[:80]/[:500]` 截断，无 DB 压力） |
| `albums.py:135-138` `AlbumBody` | `title` / `items` | 端点内 `len(title) > 40` → 400 ✅ |

**实测**：`POST /api/upload/init {"dest_dir":"d"*500000,"filename":"a.bin","size":10}` → **200**，500KB 原样写进 `upload_sessions.dest_dir`（`uploads.py:257-258` 只对 `filename` 做了 `clean_filename`，`dest_dir` 原值入库）。

单请求上限受中间件 1MB 约束（`asgi.py:164`），所以不是"一次打爆"，而是**可累积的 DB 膨胀**：8 个并发会话上限 × 1MB = 8MB/用户，3 天 TTL 滚动。`AuthBody.username` 1MB 还有额外后果（见 §3.3）。

**修法**：给上述字段统一 `Field(max_length=...)`；`dest_dir` 入模前先 `len` 校验（正常路径不可能超过 1024）。

### 2.4 [读码] 无分页的列表端点

| 端点 | 位置 | 上限 |
|------|------|------|
| `GET /api/files/{path}` | `files.py:71` → `filetools.py:137-168` | **无**，`sorted(path.iterdir())` 全量 + 逐条 stat，整包返回 |
| `GET /api/trash` | `trash.py:104` → `store/trash.py:16` `SELECT * ORDER BY deleted_at DESC` | **无** |
| `GET /api/feedback` | `feedback.py:322` → `_list_all()` 递归读**全部** md | **无**，且见下 |
| `GET /api/shares` | `share.py:334` | 无（家庭规模可接受） |
| `GET /api/photos` | `photos.py:475,507` | `limit≤500`，但**先全表取回再切片**，`offset` 无上限（Python 切片，安全但空转） |
| `GET /api/admin/large-files` | `console.py:121` | `limit≤200` ✅（遍历全树但不进响应） |

`GET /api/files/<大目录>` 是唯一能"一次请求产生数十 MB 响应"的（10 万条目 × ~300B ≈ 30MB），且每次重新 stat 全目录。对比 `photos.py` 明明加了 `limit≤500` —— 同一产品里"文件页"和"照片页"的分页口径不一致。

**额外**：`GET /api/feedback` 不只是列表，它**两次**全树扫描并写盘：

```python
322|    for mm in _list_all():          # 第一次：读全部 md
326|    _regen_kanban()                 # 第二次：_regen_kanban() 内又 _list_all()（:273）+ 写 看板.md
```

一个 GET 请求 = 2×O(N) 文件读 + 1 次文件写。用户反馈按 20/天/人累积，一年后每次刷新列表读上万文件；Web 首页若轮询该接口，线程池即被打满。**GET 带写副作用**本身也违反 HTTP 语义（任何预取/爬虫都会触发写盘）。

### 2.5 [读码] 403 / 404 混用：存在性预言机

| 端点 | 不存在 | 无权限 | 后果 |
|------|--------|--------|------|
| `POST /api/trash/{id}/restore` | 404（`trash.py:130`） | 403（`trash.py:84`） | **`trash_id` 是自增整数 → 可枚举**：成员能从 1 开始扫，得知他人删了多少条、哪些 id 有效（403 = 存在但非我，404 = 不存在） |
| `POST /api/upload/state/{sid}`、`/finish` | 404 | **403**（`uploads.py:35-49` `_session_owner_guard`） | 与 `uploads.py:279` 的 docstring"owner-only（**他人 404** 不泄露存在性）"**自相矛盾**；实际返回 403，等于承认该 sid 存在 |
| `GET /api/feedback/{fid}` | 404 | 403（`feedback.py:385`） | 存在性预言机（fid 含 8 位 hex，不可暴力枚举，只有拿到截图/id 时才有意义） |
| 其余（albums、devices、shares、todos） | 404 | 404 | ✅ 正确：`albums.py:111`、`todos.py:78`、`deps.py:41` 都是"不存在与无权一律 404" |

回收站那条是**唯一可实际枚举**的（整数主键）。修法：`_ensure_can_touch` 不通过时也返回 404；或把 trash 列表 id 换成不透明 token。

### 2.6 [实测] 没有全局异常处理器：500 的形状与全站不一致

`app/asgi.py`、`app/main.py` 全文无 `@app.exception_handler` / `add_exception_handler`（grep 零命中）。所有未捕获异常落到 Starlette 默认处理器：

- **响应体**：`text/plain` 的 `Internal Server Error`（实测），而全站其余错误都是 `application/json` `{"detail": ...}`（含中间件自己返回的 413/429）。客户端按 `detail` 解析的代码路径在 500 上直接失效。
- **不记审计/指标**：`metrics.record()` 只记状态码，无 traceback 关联；`audit()` 完全不走（`audit_log` 里看不出"谁触发了 500"）。
- **不泄漏堆栈**（`debug=False`），这一点是安全的 —— 但代价是这 8 个 500 全靠翻 NSSM 日志才能定位。

**修法**：注册一个 `Exception` handler，返回 `{"detail":"服务器内部错误","ref":"<8位随机>"}` 并 `log.exception` 带 ref；顺带把 `RequestValidationError` 也统一成 `{"detail": "..."}` 字符串（目前是 pydantic 的错误数组，与其它错误的字符串 detail 类型不一致 —— 客户端得写两套解析）。

---

### 2.7 `/api/photos` 在请求线程内跑最多 20 次 `ffprobe`，单请求可占线程 ~300s

`photos.py:406` → `_scan_photos(force=refresh)` → `:268` `_probe_video_durations()`：

```python
132|    for rel in todo[:cap]:                 # cap=20
136|        dur, taken, codec = _ffprobe_meta(f)
 97|        r = subprocess.run([...], capture_output=True, timeout=15)
```

`list_photos` 是同步 `def`（线程池，不冻结事件循环 ✅），但**一个请求最多会串行等 20 × 15s = 300s**。触发条件：库里有 20 个 `duration IS NULL` 的视频行、且 ffprobe 对它们超时（畸形容器/超大 mkv 即可）。`list_probe_missing` 每次只返回未探测过的行，所以攻击者用"上传→覆盖"循环把 20 行重新置 NULL，再并发发几个 `/api/photos` 就能长期占住默认 40 线程池里的大部分槽位 —— 全站列表/预览一起排队（与 `files.py:391-410` 注释里记载的"线程池被占满，表现是全站变慢"同一失效模式）。限速只按 IP 计 120/min，挡不住"少请求、长占用"。

**修法**：把补扫移出请求路径（`thumb_warmup` 已有同类后台循环），或整轮预算降到 1-2 条；至少给 `/api/photos` 的补扫加"每轮总时长"上限。

---

## 3. P3 —— 一致性 / 低危

### 3.1 同类错误三种状态码

| 错误类别 | 422 | 400 | 静默截断/改写 |
|---|---|---|---|
| 字段过长 | `todos` / `feedback` / `profile` / `photos` / `notify` / `feedback_inbox` | `albums.py:62,149`（标题>40）、`files.py:105`（>500 项）、`share.py:54` | `notify.py:153-154`（`[:80]`/`[:500]`）、`feedback.py:237`（body `[:2000]`）、`notify.py:348`（公告）、`admin_users.py` 无 |
| 枚举非法 | `photos.py:395`（`sort` 用 `pattern=` → 422） | `uploads.py:446`（`on_dup` → 400）、`todos.py:234`（priority → 400） | `todos.py:155`（`kind` 非法 → `"personal"`）、`feedback.py:194`（`type` 非法 → `"other"`）、`photos.py:452,497`（`media_type` 非法 → 静默忽略）、`console.py:229`（`scope` 非法 → `"all"`） |
| 路径不存在 | — | `files.py:108`（目标目录不存在 404）、`albums.py:192`（404） | `spaces.resolve` 无权 → 403（`spaces.py:161`） |

影响：客户端无法用一套规则判断"我传错了"。`media_type=bogus` 返回全量照片（200），`sort=bogus` 返回 422 —— 同一个查询参数列表里两种语义。

`photos.py:449-451`：`month` 无 `pattern` 校验直接进 SQL：

```python
449|    if month:
450|        extra_parts.append("taken_at LIKE ?")
451|        extra_args.append(month + "%")
```

`month=%` 会退化成"匹配所有月份"（`%` 未被转义，与 `note_search.py:32` 统一 ESCAPE 的做法相反）。非安全问题，但同一代码库里 LIKE 转义策略不一致。

### 3.2 `du.total` 为 0 时除零

- `console.py:110` `round(du.used / du.total * 100, 1)`
- `system.py:47` `/healthz` 同样
- `healthreport.py:174` `gather_health` 同样
- `healthreport.py:318` **已防**：`round(...) if du.total else None`

`shutil.disk_usage()` 在断连的网络盘/异常卷上会返回 `total=0`。届时 `/healthz` 500（探针失灵）、管理台总览 500、健康日报采集 500 —— 而 `local_health` 没事。三处漏、一处防，正是"同一个量、各写一遍"的典型。

### 3.3 登录失败表以原始用户名为键

`auth.py:28-30` `AuthBody.username` 无长度上限 → `security.py:371-375` `_lock_key()` 只做 `strip().lower()` → `_login_fails[key]` 直接以 1MB 字符串为键：

```python
374|    u = username.strip().lower()   # 小写化：防大小写变体绕过锁定
375|    return f"{u}@pub" if public else u
390|def record_login_fail(username: str, public: bool = False):
391|    with _rate_lock:
392|        key = _lock_key(username, public)
394|        _login_fails.setdefault(key, []).append(now)
397|        if len(_login_fails) > 4096:
```

清扫只在 **>4096 键**时触发，且只删"末次失败早于 15 分钟"的键。按 `/api/login` 的 10 次/分/IP、每请求 1MB body 计：约 7 小时可持续堆积 ~4000 × 1MB ≈ 4GB 常驻内存（进程不重启就不释放）。

**[可疑]**：机制确认（代码路径无长度闸），量级未实测。同一路径还有 `users_store.get_by_username(1MB)` 的 DB 参数。修法：`AuthBody` 加 `max_length=64/128`，`_lock_key` 内 `[:64]`。

注：`/dav` 的 Basic 用户名走同一张表，但 HTTP 头受 h11 16KB 限制，放大倍数低得多。

### 3.4 `expires_hours` 无上下界（grants）

`admin_access.py:144-149,185`：

```python
149|    expires_hours: int | None = None
185|    expires = time.time() + body.expires_hours * 3600 if body.expires_hours else None
```

- `expires_hours=-1` → 授权落库即过期，界面显示"已授权"但 guest 立刻 403（**静默失效**）
- `expires_hours=10**18` → 约 1.1 亿年后过期（等于永久）

对比 `share.py:51-56,404-407` 对同一个字段做了 `1 <= h <= 8760` 且注释写明原因（过大会让 `datetime.fromtimestamp` 抛 OSError）。同一个"有效期"概念，一处严格一处放任。

### 3.5 异常文本回显

| 位置 | 代码 | 去向 |
|------|------|------|
| `admin_ops.py:104` | `detail=f"推送失败：{str(e)[:200]}"` | admin；webhook 异常里含目标 URL/内网 IP/DNS 错误 |
| `admin_ops.py:286` | `detail=f"保存失败：{exc}"` | admin；DB 异常文本（表名/约束名） |
| `local_config.py:103,199` | `{"error": f"读取配置失败：{exc}"}` | 本机 |
| `local_config.py:257` | `detail=f"保存失败：{exc}"` | 本机 |

都要求 admin / 环回，不构成对成员的泄漏；但与"500 不泄漏堆栈"的整体口径不一致，且 `str(exc)` 可能带绝对路径。低位收口即可（固定文案 + 日志）。

### 3.6 `except (sqlite3.Error, OSError)` 在 PG 上捕不到

`photos.py:510-516`：

```python
513|    except (sqlite3.Error, OSError) as e:
514|        # 只吞 DB 瞬时故障并留痕；其它异常照常上抛（别把故障降级成假 200）
515|        logging.warning("photo uploader nick map failed: %s", e)
```

生产库是 PostgreSQL（`store/pomodoros.py`、`devices.py:70-73` 的注释都写明 PG 抛的是 psycopg 异常，`devices` 那里特意用了裸 `Exception` 并注明原因）。这里只捕 `sqlite3.Error`，PG 抖动时昵称映射失败会直接把整个 `/api/photos` 打成 500 —— 恰恰是注释想避免的"把故障降级成假 200"的反面。

### 3.7 低危杂项

- `files.py:359-388` `rename`：`OSError` → **500**（`detail="重命名失败（目标被占用或系统错误）"`）。实测超长名 → 500。用户输入错误应是 400/409。
- `files.py:79-91` `mkdir`：完全无 `OSError` 收口。实测 `POST /api/mkdir/<200a>/<200a>` → **500**，且 `parents=True` 会**先建出第一层再失败**（临时根下留下了 `aaaa…` 空目录）。同文件 `filetools.py:252-257` 对 `save_upload` 有显式 MAX_PATH 判定并返回 400 带可操作文案 —— mkdir 没有。
- `notify.py:226,235-238` `scheduled_at: float | None` 无界：`Infinity` 存入后 `list_due_broadcasts(now)` 永不命中 = 定时广播静默永不发送；`NaN` 同理。
- `share.py:47-48` `max_downloads: Optional[int] = Form(None)` 只验下界，无上界（存 BIGINT，无实际危害，但与 `expires_hours` 的严格口径不一致）。
- `share.py:42` `password: Optional[str] = Form(None)` 无长度上限 → `hash_password`（scrypt）对 1MB 口令求值；`account.py` 同理。CPU 放大 ~常数倍，非 DoS。

---

## 4. 横向对比：同类端点的校验矩阵

| 维度 | 做得对 | 做错/漏 |
|------|--------|---------|
| Pydantic 模型 | albums / todos / pomodoros / feedback_inbox / notify / devices / share(edit) | **admin_ops.webhook**、healthreport（裸 dict） |
| 字符串上限 | todos、feedback_inbox、albums(title)、feedback(title 80/body 2000) | auth、account、uploads、admin_access、admin_users、share(password) |
| 枚举校验 | `photos.sort`（pattern）、`uploads.on_dup`（400）、`todos.priority/status`（400）、`share.expires_hours`（400） | `todos.kind`、`feedback.type`、`photos.media_type`、`console.scope`（静默改写） |
| 分页上限 | photos（le=500）、console（le=200/500）、feedback_inbox | **files 列表、trash、feedback、shares** |
| 数值范围 | share.expires_hours（1..8760）、invite.hours（1..720）、pomodoro.focus_min（1..180）、pomodoro.days（1..30） | **pomodoro.ended_at**（无界）、grants.expires_hours（无界）、admin quota_bytes（只防负）、max_downloads（无上界） |
| 无权返回 404 | albums、todos、devices、shares、deps.get_share_or_404 | **trash（403）、uploads（403，且 docstring 写 404）、feedback（403）** |
| 慢 IO 进线程池 | `_select_all_trash`、`_propfind_body`、`copytree`/`copy2`、`_do_transfer`、`webhook_send`、`hash_password` | **enforce_quota（uploads/webdav）、move_to_trash（files.delete）、mkdir（files）** |
| 磁盘余量预检 | upload_chunk（`ensure_disk_room`）、dav_put（同）、zip（`archive.reserve_bytes`）、admin_export | files.mkdir / files.copy（`copytree` 前无余量预检——`enforce_quota` 只管 private 且只算配额不算磁盘） |
| 500 兜底 | — | **全站统一无 handler** |

---

## 5. 已核对、判定为安全（避免重复投入）

- **路径穿越**：`feedback._find_file`（`[^0-9A-Za-z-]` 白名单 + `[:64]`）、`appver._safe_apk_name` + `_apk_path.relative_to`、`trash._trash_fs_path`（`.trash` containment）、`profile.get_avatar`（`.avatars` containment）、`spaces.safe_path`（点前缀/`..`/8.3 短名）均正确。
- **上传分片**：`upload_chunk` 的 `index` 上下界、`expected` 字节数、超发即断（`uploads.py:300-326`）正确；`finish` 的 `len(parts) != total` + sha 校验 + 失败销毁会话正确。
- **SQL**：全部走参数绑定；唯一的动态 SQL 是 `todos.update_todo` 的列名（键来自 API 层白名单，非用户输入）。
- **LIKE 转义**：`note_search._escape_like` + `ESCAPE '\'` 正确（`photos.month` 是唯一漏点，见 §3.1）。
- **像素炸弹**：`previews._reject_pixel_bomb`（60MP）+ avatar 同款上限，`/preview` 的 `w` 量化到固定阶梯且 clamp 到 320..3840。
- **限速/防爆破字典**：`_rate` / `_login_fails` 均有 >4096 键清扫（残留问题只是键长，见 §3.3）。
- **未认证面**：`/healthz`（公网 Host 兜底拦截）、`/api/auth/state`、`/api/setup`（有 `user_count()` 双检）、`/api/invites/redeem`、`/api/feedback/inbox`（token）、`/local/*`（`_require_local` 双判）均符合设计。

---

## 6. 建议修复顺序

1. `pomodoros.FinishBody.ended_at` 加有限值校验 + `_minutes_of` fail-closed（一行改动，止损"永久 500"）。
2. `enforce_quota` / `move_to_trash` / `mkdir` 进线程池（防全站冻结）。
3. `feedback_inbox.inbox_status` 加 `require_admin`。
4. `admin_set_webhook` 换 Pydantic 模型。
5. 注册全局异常处理器（JSON + ref + log.exception）。
6. `/api/photos` 的视频补扫移出请求路径（或整轮预算降到 1-2 条）——防线程池被长占用。
7. 给 §2.3 的 6 个模型补 `max_length`；给 `files` 列表 / `trash` 加 `limit` 参数（默认 500，`le=2000`）。
8. `trash` / `uploads` 的无权分支改 404；`feedback_list` 去掉 GET 里的 `_regen_kanban()`（移到写路径或后台 sweep）。
