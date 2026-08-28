# 回滚说明

如需回滚到 v1（无认证版本），按以下方式操作：

## backup/v1/ 内容

- `server.py.bak` — v1 完整原版（无认证、分享存内存）
- `Caddyfile.bak` — v1 完整原版（无 forward_auth、:8767 全站 basic_auth）
- `share.html.bak` — v1 完整原版（GET + query 密码，注意该方式密码会进 Caddy 日志）

## index.html 还原方法

v2 对 index.html 只做了两处增量修改，反向应用即可：

1. **api() 函数**：删除 401 跳转两行和 logout() 函数：
   - 删 `if(r.status===401){location.href='/login';throw new Error('请先登录')}`
   - 删除整个 `async function logout(){...}`
2. **header 图标**：删除 `🔑 修改密码` 和 `⏻ 退出登录` 两个 header-icon，以及
   `showPasswordModal()`/`doChangePassword()` 函数。

## 回滚后注意

- v1 的分享令牌存内存，与 v2 的 SQLite 分享表不互通
- 删除 `C:/lan-share/data/.meta.db` 即可清掉 v2 的账户数据（不影响文件）
