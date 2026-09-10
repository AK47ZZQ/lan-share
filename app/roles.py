"""角色常量的单一事实源（07/08 审查 A6）。

此前 "admin"/"member"/"guest" 字面量在服务端 10 个文件散布 38+11+7 处，
加一个角色要改多处且容易漏判。

⚠️ 不同概念、恰好同名的字符串，**不得**复用本模块常量：
  - 通知偏好组 `("share", "admin", "system")`（notify.py）——是通知分类，不是角色；
  - 保留用户名集合 `{"admin", "administrator", "root", "system"}`（spaces.py /
    auth.py）——是用户名黑名单，不是角色。
两者恰好都含 "admin" 纯属巧合，混用会造成语义漂移。
"""

ADMIN = "admin"
MEMBER = "member"
GUEST = "guest"

ALL_ROLES = (ADMIN, MEMBER, GUEST)
