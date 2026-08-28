# v3 设计 · 空间与授权模型（PERMISSIONS）

> **本文是 M3 的安全关键件。** 全项目最易出洞的位置就是"按用户解析路径"。
> 开工铁律：本文件的解析规则与边界用例清单未定稿，M3 不动 `safe_path` 相关代码。

## 1. 角色模型（三档，不引入更多）

| 角色 | 能力 |
|------|------|
| `admin` | 全部空间读写 + 授权管理 + 成员管理 + 审计/配额管理；**不隐含读取成员私人空间**（见 §7 边界 E8） |
| `member` | 私人空间读写 + 共享空间中"被授权目录"读写 + 其余共享目录读 |
| `guest` | 仅管理员显式授予的目录（只读为主），有失效时间，到期自动失效 |

- 角色是用户属性（单字段），不做角色继承链。
- 授权（grant）与角色分离：**角色管粗粒度，授权管例外**，与 v2 头脑风暴一致。

## 2. 空间模型（目录布局）

文件系统仍然唯一事实来源，布局约定：

```
data/
├── private/<username>/...     私人空间：仅本人（rwx），admin 仅管理权不读内容
├── shared/                    共享空间根
│   ├── everyone/...           全员可读写
│   ├── <name>/...             具名目录，默认全员只读，子目录可授权读写
│   └── incoming-<username>/   各成员手机直传落地（本人 rwx + admin 读）
├── apk/ documents/ media/ 照片/   ← v2 既有目录迁移为 shared/ 下的具名目录
├── incoming/                  v2 单管理员直传目录，迁移后废弃（数据搬入 private/）
└── .trash/.thumbs/.chunks/.zips/  系统目录，规则不变
```

迁移策略（M3 的一次性脚本）：v2 既有共享目录搬入 `shared/`，管理员私人
数据搬入 `private/<admin>/`；迁移脚本可重复执行（幂等），迁移前自动快照
`data/` 目录清单。

## 3. 授权规则表（grants）

```sql
CREATE TABLE grants (
  id          SERIAL PRIMARY KEY,
  user_id     INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  path        TEXT NOT NULL,        -- 虚拟路径，规范形如 'shared/photos'，无尾斜杠
  can_read    BOOL NOT NULL DEFAULT TRUE,
  can_write   BOOL NOT NULL DEFAULT FALSE,
  created_by  INT NOT NULL REFERENCES users(id),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at  TIMESTAMPTZ,          -- guest 到期即失效；NULL=永久
  UNIQUE (user_id, path)
);
```

规则：

1. **路径粒度只到目录**，文件级授权不支持（需求出现时再议，防过度设计）。
2. 授权**不递归取消**：对 `/shared/photos` 的授权覆盖其全部子树（继承向上
   匹配前缀）；同名路径只允许一条 grant（UNIQUE 约束）。
3. **默认语义（M3 审查定稿，消除歧义）**：`shared/` 全树对 member **默认
   只读**（无需 grant）；写权必须显式 grant。因此 `can_read` 字段**仅对
   guest 生效**（member 对 shared 恒可读，该字段对 member 不参与判定）；
   `can_write` 对 member/guest 都生效。不引入"shared 下私有子目录"需求，
   出现时再议。
4. grant 仅 admin 可创建/修改/撤销；管理面板操作，全量记审计。
5. guest 必须至少有一条有效 grant，否则登录后为空壳（只看到自己无可浏览
   内容的空界面），不得回退到任何默认读权限。

## 4. 虚拟路径与解析算法（安全核心）

### 4.1 虚拟路径（VPath）

用户在 UI/API/WebDAV 中永远操作**虚拟路径**：

```
private:<path>     → 物理 data/private/<username>/<path>
shared:<path>      → 物理 data/shared/<path>
```

API 不再接受裸物理相对路径（v2 形态）；v2 的 `?path=documents/xx` 在 v3
映射为 `shared:documents/xx` 或迁移后的 `private:documents/xx`。这消除
"同一字符串在不同上下文指向不同物理位置"的歧义，是收窄攻击面的关键一步。

### 4.2 解析算法 `resolve(user, vpath, need)`

```
def resolve(user, vpath, need):        # need ∈ {read, write}
  1. 解析 vpath → (space, rel)；先把分隔符统一（'\\' → '/'，Windows 路径
     两种斜杠都合法，不归一则后续按段匹配可被绕过），rel 再过 v2 既有
     清洗（NTFS 保留名/冒号/尾点空格）
  2. normpath 后拒绝逃逸：任何 ".." 分量、绝对路径、盘符 → 403（记审计 debug 事件）
  2b. 大小写折叠：后续所有路径段比较（space 首段、grant 前缀）一律在
     normcase 折叠后进行——Windows 文件系统大小写不敏感，DB/代码里的
     字符串比较若区分大小写即存在绕过面（见 E5/E14）
  3. 判定：
     a. space=private：
        - rel 首段 == user.username → 授权（admin 同样只在自己的 private 内 rwx）
        - 否则 403（admin 也不例外，除非走 §7 E8 的显式管理动作）
     b. space=shared：
        - user.role==admin → 通过（读写皆可）
        - need=read → 若 rel 前缀命中任一 can_read grant（含 everyone 默认读）→ 通过
        - need=write → 仅当存在 can_write=TRUE 且未过期的前缀命中 grant
        - guest：无命中即 403（无默认读）
  4. 物理落点 = SHARE_ROOT / 物理路径，再过一遍 v2 的 safe_path 物理边界
     （normcase + os.path.realpath 校验仍在 storage/local.py，双保险）
  5. 返回 (物理路径, 权限)；失败一律 403 + 审计（不区分 404/403 防探测：
     对无读权路径返回 403 而非 404）
```

实现落点：`app/storage/local.py::resolve`；api 层依赖 `deps.resolve_or_403`。
**所有**文件入口（Web API、`/files/*`、WebDAV、share、`/up`、zip、缩略图）
必须经过它，不允许存在绕过 resolve 直接拼 `SHARE_ROOT / path` 的代码——
用 grep 门禁（CI 检查 `SHARE_ROOT` 仅可在 storage 层出现）兜底。

## 5. 各入口接入清单（M3 验收范围）

| 入口 | v2 行为 | v3 变化 |
|------|---------|---------|
| Web UI `/api/*` | 全局根 | vpath + resolve；前端按角色/授权显隐按钮 |
| `/files/*` 直连 | authcheck 仅判登录 | authcheck 判"该用户对该路径可读"（§6） |
| WebDAV `/dav/` | 全局根，管理员 Basic | Basic 对应某成员；挂载根 = 其 vpath 全集；写操作过 resolve(write) |
| 分享外链 | 指定物理相对路径 | 创建者只能分享**自己可读**的 vpath；票据校验时不再验创建者权限（文件在回收站/被删则票据失效，同 v2） |
| 手机直传 `/up` | 落全局 `incoming/` | 落 `shared:incoming-<username>/`（写权内建） |
| zip 打包/缩略图/一次性 URL | 全局根 | 逐项过 resolve(read)；zip 一次性 URL 绑定 user+路径+过期（HMAC），authcheck 识别票据并对路径 resolve(read) |
| 移动 `/api/move` | 全局根 | 源过 read，目标过 write；跨空间移动仅 admin（member 不可把 shared 文件拖进自己 private——防"搬走共享资产"，如需副本用复制） |

## 6. `/files/*` forward_auth 的 v3 方案（定稿）

v2：Caddy `forward_auth` → `/authcheck` 只回答 200/401（登录与否）。
v3：Caddy 先 `forward_auth` 携带原始路径 → 后端校验会话 + resolve(read) →
通过则在响应头签发 `X-Send-Token`（HMAC(SECRET_KEY, path+exp)，60s 有效）
→ Caddy 用原生 `forward_auth` 的 copy_headers 把 token 传给 file_server？——
**原生 Caddy 无法在 file_server 侧再校验自定义 token**，故定稿方案为：

- 保持 forward_auth 判定"可读"，但 authcheck 返回后 Caddy 直连存在
  "判读与下载之间路径被换"的 TOCTOU 窗口。收窄方式：authcheck 校验的是
  Caddy 传来的完整下载路径本身（原始 URI query 含 path），下载请求与
  authcheck 请求路径一致才放行——即**按每次下载逐次 authcheck**（现状即
  如此，天然无窗口），v3 只是让判定从"登录"升级为"登录+可读此路径"。
- 结论：不引入签名 token 机制；SECRET_KEY 用于会话与 CSRF 场景。

## 7. 边界用例清单（M3 测试集的最低范围）

| # | 用例 | 期望 |
|---|------|------|
| E1 | `..`、`..\\`、URL 编码 %2e%2e、Unicode 归一化伪装逃逸 | 403 + 审计 |
| E2 | member 读他人 private | 403（不泄露存在性） |
| E3 | member 对 shared 只读目录 PUT/DELETE/MOVE | 403 |
| E4 | guest grant 过期后访问 | 403；其会话不吊销但空间为空 |
| E5 | 大小写变体（Windows 不区分大小写！）：`SHARED`、`Private/ADMIN` | resolve 前 normcase 折叠后判定，杜绝绕过 |
| E6 | 目录重命名/移动后，指向该路径（或其子前缀）的记录失配 | rename/move 在**同一事务**内同步全部携带路径的表：`grants.path`、`shares.vpath`、`file_refs.path`、`photos.path`（v2 中 share 指向路径、移动后分享失效的同族问题，v3 一并闭环） |
| E7 | 删除成员账号 | grants CASCADE 清除；其 `private/<username>/` **立即改名为 `.orphaned/<uid>-<username>/`**（防此后邀请同名新用户时读到前任数据）；动态流留名"已注销" |
| E8 | admin 读取成员 private | **默认 403**；提供显式"管理动作"接口（二次确认 + 审计 `admin_private_access`），满足"孩子误删全家照片要找回"场景 |
| E9 | 跨空间移动（shared → private） | member 403；admin 放行 |
| E10 | E5 变体：`shared:photos` vs `shared:photos2` 前缀误匹配 | 前缀匹配必须按路径段（`path + '/'`），不得用裸 startswith |
| E11 | 用户名与保留名冲突（`shared`、`private`、系统名）或含路径分隔符/盘符 | 邀请建号时拒绝；用户名清洗沿用 v2 规则 + 新增保留词表 |
| E12 | 并发：grant 撤销与进行中的下载 | 票据/直连不追溯（v2 语义），新请求立即 403 |
| E13 | 反斜杠绕过：`shared:photos\\..\\..\\private`、`shared:photos\\x` 绕过按 `/` 段的前缀匹配 | resolve 第 1 步已统一分隔符后判定；测试用反斜杠变体打全 |
| E14 | 大小写绕过：`shared:PHOTOS`、`private:John`（用户 john）在 Windows 大小写不敏感 FS 上指向同一物理目录 | 全部路径段比较在 normcase 折叠后进行（2b 步）；集成测试必须跑真实 NTFS，不能只跑 mock |

E5 与 E10 是本模型最容易写错的两个点，测试必须包含真实 Windows 文件系统
（大小写不敏感行为）的集成用例，不能只跑 mock。

## 8. 配额（与空间模型同落地）

- `users.quota_bytes`（NULL = 不限）+ `used_bytes` 由 `stats.py` 按 NTFS
  文件索引去重统计（复用 v2 算法）。
- **归属口径（M3 审查定稿，与 `file_refs.owner_id` 一致）**：归属在
  **创建/上传时**确定并写入 `file_refs.owner_id`，移动不改归属；
  admin 可在管理面板改判单个文件的归属。废弃原"最后修改者归属"设想
  （与表结构矛盾且不可解释）。
- **配额按逻辑字节计**：`used = Σ file_refs 所属文件的 size`——秒传命中
  不新增物理空间但照常计配额（多用户下必须如此，否则去重可被用来
  无限囤积）。
- 超额行为：上传/移动入 = 拒绝（409 + 明确文案）；不锁读。
- shared 空间的"人均占用"视图是近似统计（按 owner_id 分桶），文档明确
  声明即可，家用场景不追求精确分摊。

## 9. TOTP（认证增强，M3 顺带）

- 可选启用：`users.totp_secret`（加密存储）、启用后登录表单第二框。
- 防爆破与登录限速共用 v2 机制（按账号 5 次锁 15 分钟），TOTP 错误同样计数。
- 恢复码 8 组一次性，启用时展示一次，哈希入库。
