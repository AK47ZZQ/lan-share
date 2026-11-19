# CHANGELOG 归档 · 2026Q4

> 从仓库根 `CHANGELOG.md` 滚动切下来的历史条目（**新到旧**，与主文件同序）。
> App 的更新历史兜底路径会拉这个文件（见 `mobile/lib/data/api/appver_api.dart`），
> 所以块格式必须与主文件完全一致：`## 日期 — 版本线 版本号：标题`。
> 由 `scripts/changelog_archive.py` 生成，**别手改**。

## 2026-11-01（深夜三）— App v1.1.23：关于页去掉内网 IP、更新历史能滑到底、加 VPN 提示

### 关于页印着家里的内网 IP

图标下面那行原来是 `家庭自包含网盘 · https://192.168.x.x:8765` —— 关于页
是最容易被截图分享的一页，把家里网段印在上面没有任何用处，只有风险。
换成项目仓库链接，点一下用浏览器打开。

### 更新历史滑不动

根因不是滚动冲突，是**一次布局了全部内容**：近 40 个版本全塞在一个 Card
的 Column 里，手机上直接卡死，表现成「页面划不动」。

改成 `ListView.builder`：只构建屏幕上看得见的那几条，滚动时按需生成。
历史标题右侧加了「共 N 个版本」，滑到底能确认自己看完了。

### 更新历史的数据源换了

原来从 GitHub Releases 的 body 拉。但 Release 是会被清理的 —— 仓库只保留
最新一版之后，历史就只剩一两条，跟「想看全」直接冲突。

改从仓库的 `CHANGELOG.md` 拉（走 `contents` API，和 Release 同一个域名，
可达性一致；不用 raw.githubusercontent.com，那个国内经常直接连不上）。
CHANGELOG 在 git 历史里，删不掉，自带完整时间线 —— 现在能显示 39 个版本。

只挑含 App 版本号的条目：CHANGELOG 里服务端和 App 是混着写的
（`v3.79 + App v1.1.22：…`），把服务端版本混进 App 的历史会让用户
以为手机装了 3.79。

### VPN 提示：两个源的诉求是相反的

- **服务端**（局域网 IP）：开 VPN 会被路由劫持 → 要**关**
- **GitHub**：国内直连经常失败 → 要**开**

原来混成一句「连不上更新源」，等于让用户猜。现在 `UpdateCheckResult`
带 `serverReachable` / `githubReachable`，按三种情况给不同建议：

- 只有服务端挂 → 说明不影响检查，不用管
- GitHub 挂 → 建议开 VPN 重试
- 两个全挂 → 建议开 VPN，同时说明开 VPN 就连不上家里服务器了

下载新版本时另有一句：「安装包从 GitHub 下载，国内直连可能很慢，开 VPN 会快很多」。

### 旧 Release 自动清理

`release.py` 发完版自动删同类型的旧 Release（只留刚发的），**tag 保留**
—— tag 很轻，CHANGELOG 里的历史链接和 issue 引用都靠它。

## 2026-11-01（深夜二）— v3.79 + App v1.1.22：修 GUI 检查更新、说明精简、历史可展开

### GUI 的「检查更新」点了没反应

两个原因叠一起：C# 读的是 `msg["action"]`，我新写的按钮发的却是 `{type:...}`，
action 取到空串，switch 落空，点击被静默忽略；同时 `RunCli` 的 `out error`
声明了没接，真失败时 UI 只能显示「命令没有输出」，真实原因全丢了。

现在错误会带回前端，`RunCli` 也记日志（实际解释器路径、完整命令行、退出码）。

### 更新说明精简

CHANGELOG 是写给未来的自己看的，Release 页面是给用户看的 —— 两千字内部复盘
贴上去，手机一屏都放不下。新增 `summarize()`：提炼「一句话标题 + 最多 5 条要点」，
丢掉段落正文、### 小节、代码块、已知限制。1965 字符 → 约 280。

已用新算法重新生成全部 14 个 Release 的说明。

### 更新历史能看全

原来把整篇 markdown 原样塞进 Text：###、**、表格符号全露出来，七个版本
加起来七八千字符。拆成 `_HistoryEntry`：清洗标记 + 超过 140 字默认折叠，
点「展开全文」看完整。

## 2026-11-01（深夜）— v3.78 + App v1.1.21：发版固化、登录页不再卡、反馈脱敏、历史改走 GitHub

这一版不修大 bug，修的是**「每次发版都要靠记性」这件事**。

一天发了两版，每版十来步全靠手走，每一步都错过：版本号散在四处改漏一处、
APK 资产名少个 `+125` 客户端就放弃整个源、忘了跑隐私扫描把真实 IP 推上去
躺了一天、发完不验证等于没发。**这些都不是逻辑写错，是漏了一步。**

### 一、`scripts/release.py`：一条命令发版

    python scripts/release.py check          # 只检查
    python scripts/release.py server 3.78    # 发服务端
    python scripts/release.py app 1.1.21     # 发 App（build 自动 +1）

七步全自动：前置检查 → 写版本号 → 跑测试 → 构建 → 冒烟 → 提交 → 发布 → **发完再验一遍**。

两个关键设计：

**发版后真的去拉一次 GitHub。** `gh release create` 返回 0 只能说明「上传成功」，
不能说明「App 点检查更新能选到它」—— 那个 bug 就是这么漏过去的。所以发完会用
客户端同一套逻辑（筛选 `app-` 前缀、按版本号排）重新选一遍，对不上就报错。

**CHANGELOG 是硬门槛。** 没有对应版本那一节就不许发。写不出好文案是人的事，
脚本只负责不让你在没写文档的情况下发出去。

### 二、登录页地址下拉：不再等网络

原来 `showServerPicker` 是「先 await 网络拿公网入口 → 再弹面板」。而那个请求的
connect/receive 超时各 4 秒，服务端不可达时（开着 VPN、换了网络）**最坏空等 8 秒**，
用户点地址栏就是「点了没反应」。

改成面板立即弹出、本地历史地址第一帧就可见，公网入口区先显示一行「正在获取…」
再异步填。慢网络只影响那一小块。

### 三、更新历史：改从 GitHub 拉

原来只读服务端的 `/api/app/history`，这条路依赖**用户那台服务端的版本**。别人跑
旧版服务端时，历史永远停在旧版本，「关于」页看着像死了。

改成 GitHub 优先、服务端兜底 —— 「别人的服务端可能是旧的」这件事不该由用户承担。

### 四、反馈上报：出本机之前先脱敏

反馈链路本来就有，但上报体是**原样转发**用户手打的内容：服务器地址、内网 IP、
同事真名、文件路径，什么都可能有。用户提交反馈时根本想不到要删这些。

三处脱敏：正文洗掉 URL / 裸 IP / 绝对路径 / 邮箱；`user` 和 `device` 换成 12 位
稳定短哈希（稳定是为了同一个人的多条反馈能串起来看，哈希是为了不可逆）；
正文里手打的账号名也做字面替换。

### 五、服务端也能检查更新了

App 一直有「检查更新」，服务端没有 —— 用户想看有没有新版，只能自己上 GitHub 翻。

新增 `lanshare_cli check-update`，控制面板的「版本」卡片也接上了：

    python -m lanshare_cli check-update          # 人看
    python -m lanshare_cli check-update --json   # GUI 用

**只查不装。** 自动替换正在跑的服务目录风险太大：exe 被占用、升级中途断电、
装一半没回滚路径。而安装器已经会「覆盖安装 + 保留数据 + 保留注册表」，
把这件事交给它比自己写一个替换器可靠得多。所以查到新版只给下载地址。

**退出码是三种**：0 已是最新 / 1 有新版本 / 2 没查成。这样脚本能直接
`if lanshare_cli check-update; then`。UI 那边同样把「没查成」单独显示成
错误信息，不跟「已是最新」共用一句话。

为此安装包多了一个 `VERSION` 文件（构建时从 `lanshare.iss` 的 AppVersion
生成，不另外维护一份）。老安装没有这个文件，显示「未知版本」而不是编
一个数字出来；也因此不会误报「有新版本」。

### 六、CHANGELOG 标题补上版本号

`release.py` 要从 CHANGELOG 取发版说明，但最近两节的标题只有描述没有版本号。
补上，并且往后每一节都必须带版本号。

## 2026-11-01（晚）— v3.77 + App v1.1.20（一）：真机反馈两连修：VPN 下检测不到 + 下载太慢

1.1.19 发出去之后手机上真实用了一遍，暴露出两个问题。第一个是**我上一版
没修干净的地方**，第二个是**设计时没替其他用户想过**。

### 一、开 VPN 后点检查更新，显示「已是最新」

不是修复失效 —— 是**两个源一起挂了**：

- 服务端地址是局域网 `192.168.x`，开 VPN 后这个地址被路由劫持，请求直接失败
- GitHub 那条如果节点也不通/超时，同样是 null

然后 `check()` 返回 null，UI 一律当成「没有新版」。**「真没新版」和「压根没查成」
长着一模一样的脸** —— 用户只能猜。

改成三态（`UpdateCheckResult`：查到了 / 确实没新版 / 没查成）。没查成时明说
「连不上更新源，开着 VPN 先关掉再试」，而不是假装一切正常。

**顺手揪出一个还没被触发的同类 bug**：`check()` 在两个源之间选版本时，
排序又是按 build 数字——和 `isNewer` 犯的同一个错。碰上 1.1.18 的推算值
10118 会排到 1.1.20（127）前面，**选出一个更旧的版本**。一并改成按版本号排。

### 二、下载太慢 —— 而且我的第一版方案只修了自己能用

原来的做法是 302 跳到 GitHub CDN，国内下 100 MB 几分钟起步。

第一版改动是「本地有同一份就直发」——但这有个**致命的视角问题**：那份文件是
我手动放进自己 `App安装包/` 的。**别人搭一套服务端时目录是空的**，这条分支对
他们等于死代码，永远走 302。等于只优化了作者本人的体验。

正确做法是**服务端自动镜像**：

- 本地没有对应版本 → 先 302 让用户从 GitHub 拿（文件总得从某处来）
- **同时在后台把这个 APK 拉一份到本地**
- 从第二个人开始，局域网下载就是本地 IO

几个约束都写进注释了：原子落盘（先 `.part` 再 rename，否则半截文件会被下一个
用户当成正常 APK 发出去）、防重复下载（多用户同时点更新不会并发拉四份）、
大小校验（对不上 GitHub 报的 size 就丢弃）、失败静默（用户这时已经拿到 302 了）。

判定「是不是同一份」用**字节数**而不是文件名或版本号 —— 后者都可能被手改。

### 测试

- Dart：`update_github_test.dart` 从 12 个扩到 21 个，新增「版本号更高就算新
  哪怕 build 更小」「同版本才退回比 build」「远端版本更低不能因为 build 大就提示」
- Python：`test_appver_github.py` 从 14 个扩到 19 个，新增本地优先、镜像原子性、
  防重复、size 不符丢弃

## 2026-11-01（下午）— v3.77 + App v1.1.20（二）：更新源统一到 GitHub：修掉「检查更新没反应」

手机端反馈「点检查更新没有检测到新包」。查下来是两个独立 bug 叠在一起，
**任何一个都足以让这条链路静默失败**——不报错、不崩、日志里也没异常，
表现永远只是「已是最新」。

### bug 1：请求了 /releases/latest，拿回服务端那个 Release

这个仓库同时发两种 Release：`server-v3.76`（安装包）和 `app-v1.1.19`（APK）。
GitHub 的 `/releases/latest` 只返回**发布时间最晚**的一个，跟标签前缀无关。
服务端通常发得晚，于是 App 拿到的是服务端 Release —— 里面根本没有 APK。

改成拉 `/releases?per_page=30` 自己筛 `app-` 前缀。

### bug 2：取不到 build 号就整个丢弃这个源

解析时从资产文件名用 `\+(\d+)` 取 build。但早期 Release 的资产叫
`LANShare-App-1.1.18.apk` —— 没有 `+125`，原代码直接 `return null`，
把这个源整个放弃了。即使 bug 1 修好，这一条仍然会让更新检查失败。

改成三级兜底：文件名 `+N`/`_N` → tag `+N` → 用版本号推算
（`major*10000 + minor*100 + patch`）。

### 判断新旧改成优先比版本号

仓库里同时存在两种编号体系：真实 versionCode（126）和推算值（10119），
量级差两个数量级。只比数字会把「已装 1.1.18」的用户判成落后（10118 > 125），
反复提示更新。

所以 `isNewer` 改成**先比 version 字符串，相同再退回 build 数字** ——
version 才是版本先后的唯一真相，build 只用来区分同版本重新打包。

### 服务端同步改造：GitHub 主源 + 本地兜底

`appver.py` 原来只读管理员手动维护的 `App安装包/version.json`。发版时忘了
同步，App 就永远推不出新版（这次就是：GitHub 上 1.1.18 发了，那边还停在
1.1.17/124）。

现在改成：

- **GitHub Release 是主源** —— 发布动作本身就是推送动作，不存在「忘了同步」
- **本地 `version.json` 兜底** —— GitHub 不可达（公司网络、限流、断网）时照样能推
- **下载走 302** 跳到 Release 资产，不占自家带宽
- **更新日志取 Release body** —— 发版时写的那段说明直接成为更新历史，不用再单独维护 versions.json

限流是必须处理的：未认证的 GitHub API 只有 60 次/小时，检查和下载都走这条路。
加 5 分钟 TTL 缓存 + 线程锁，正常使用一小时最多 12 次。

### 测试

新增 `tests/test_appver_github.py`（12 个用例）钉住「选哪个 Release」
「build 三级兜底」「按 version 而不是 build 排序」「GitHub 挂了要回退本地」。

`conftest.py` 加了 autouse fixture，默认把 GitHub 源打成不可达 —— 测试不能
依赖外网：CI 无网会全红，有网也会因为仓库真发过版而让断言漂移（今天能过、
明天发个版就挂）。要测 GitHub 分支的用例显式恢复真实函数再 mock 网络层。

**踩过一个测试自身的问题**：第一版把 `_github_app_releases` 整个替换成
lambda，把过滤/解析/排序逻辑全跳过了，测试变成自说自话。正确做法是只 mock
`urllib.request.urlopen`，让真实逻辑照跑。

### 资产命名统一

新发的 APK 一律 `LANShare-App-<版本>+<build>.apk`。这次两个 bug 都和资产命名
有关（`LANShare-App-1.1.18.apk` 缺 build 号），靠人记不住，以后发版脚本强制。

## 2026-11-01 — v3.73~v3.76：托盘没进包、控制面板换 WebView2、证书信任

这四版连着发，起因是「装完没有桌面图标、托盘也不启动」。顺着查下去，
发现是一串各自独立的问题叠在一起，每一个单独看都像没事。

### v3.73 / v3.74 — 托盘为什么不在安装包里

`build.ps1` 第 322 行报 `Test-Path : 路径中具有非法字符`，构建在打包
PostgreSQL 那步就中断了，后面复制托盘的 Step 10 根本没执行。

源头是一行字符串里混进了 **0x0B 垂直制表符**：

    "bin\u000bcruntime140.dll"     ← 本意是 vcruntime140.dll

而 `lanshare.iss` 里那条 source 带 `skipifsourcedoesntexist`，源目录空着
也照样编译成功、包体积正常、装上还能用 —— 全套检查全绿，就是没托盘。

修的时候反斜杠又被吃掉一次（`binvcruntime140.dll`），得用 patch 补回。
这类不可见字符建议构建脚本开头统一做一次字节检查。

同时补了两处：`[Icons]` 加桌面快捷方式（之前只有开始菜单三项，桌面
从来没写过）；托盘启动行去掉 `postinstall` 标志（否则只在完成页勾选时
才跑，而 3.72 报错时完成页不出现，等于永不执行）。

### v3.75 — 重装三连坑

**服务名查错**：`sc query lan-share-api` 报 1060「服务未安装」，
实际前缀是 `lanshare`（无连字符）。三个服务其实都 RUNNING。

**「服务配置失败」是误报**：`cli.py` 里 API 探针用 `http://`，Web 探针
也写死了 `http://127.0.0.1:{web_port}/healthz`，而 8765 从 3.72 起就是
HTTPS —— 探针必然失败返回 1，安装器弹错，服务其实全好。

**重装必然锁库**：卸载删掉了注册表 `PgPassword`，但数据目录 `pgdata`
留着 → 重装生成新密码，PG 角色还是旧密码 → 卡在「PG 在 30 秒内没有
接受连接」。改成密码锚定数据目录（`.lan*` 密码文件），注册表只是副本，
删了还有兜底。

**重装后 TLS 握不上**：`schannel: using IP address, SNI is not supported`
+ `SEC_E_ILLEGAL_MESSAGE`。Caddy 的 storage 落在 SYSTEM 账户的 AppData
（`C:\Windows\system32\config\systemprofile\...\Caddy`），残留着上一次
安装的内部 CA，新证书签不出来。全局块加 `storage file_system "<install>
\certs\caddy"`，把 CA 锚到安装目录，卸载重装就不串了。路径含空格会被
Caddy 按空格切 token，所以加引号并统一转正斜杠。

顺带把 `[3/10] 检查是否已安装`（含 `--force` 停旧服务）提到 `[4/10]
检查端口占用` 之前 —— 顺序反了的话重装时旧服务占着 8766/8765/5433，
端口检查直接失败，`--force` 那段覆盖逻辑永远轮不到。

### v3.76 — 控制面板换成 WebView2

WinForms 手绘面板的维护成本压不住了：每个控件手算坐标，150% DPI 下
还有「缩放前/缩放后」两套坐标系，光「关闭局域网服务」一个按钮就改了
四轮（宽度不够被裁 → 加宽跑出卡片 → 用错基准值 → 在 Scale 之后设坐标
又错位）。

界面改成外置 HTML（`installer/tray/ui/index.html`），flexbox 排版、
DPI 交给浏览器、改样式不用重新编译；**右键菜单和 MessageBox 保持原生**，
没有跟着换成网页控件。C# 侧（`WebPanel.cs`）每 4 秒跑一次
`lanshare_cli lan status` 喂数据，按钮点击回传 C# 执行。

**为什么不直接 fetch**：`app/` 里没有任何 CORS 中间件，`file://` 页面
请求 `http://127.0.0.1:8766` 会被浏览器拦。走 `PostWebMessageAsJson`
桥接既不动后端也不受同源策略限制。

四个坑，都是「看起来像 CSS 写错了」：

- **DPI 双重缩放**：`ClientSize=900` 在 DPI-aware 进程里是物理像素，
  150% 下只等于 600 个 CSS px，卡片挤成一条、地址框只露得出
  `https://192.168.`；`AutoScaleMode` 默认值又会在 manifest 处理完 DPI
  之后再缩一次。要按 `GetDpiForWindow` 折算 + `AutoScaleMode.None`。
- **WebView2 用户数据目录**默认建在 exe 同级，实测 73 MB 缓存。改到
  `LOCALAPPDATA` 并在 build 时 `/XD` 排除，否则安装包凭空大 73 MB。
- **多帧 ICO 不能直接 ToBitmap()**：抛「请求的范围扩展超过了数组的
  结尾」，指定帧尺寸也一样。后来改成内联 SVG，这个问题不存在了。
- **文件缓存**：WebView2 缓存 `file://` 页面，改了 HTML 不加时间戳参数
  看到的还是旧内容 —— 调样式时会以为是 CSS 没生效。

WebView2 Runtime 缺失时自动回退到原 WinForms 面板，代码一行没删。
（Runtime 148.0.3967.96 在 Win10/11 一般都有，LTSC/精简版可能没有。）

### v3.76 — 让浏览器信任本地证书

用户反馈「打开网盘点了没反应，界面压根打不开」。实际是调用了
`Process.Start` 打开浏览器，但看到的是满屏红色警告页
`ERR_CERT_AUTHORITY_INVALID`。

Caddy 的 `tls internal` 用自己的内部 CA 签发证书，这个 CA 不在 Windows
信任链里。Caddy 在 Linux/macOS 上会自己装进系统库，Windows 上要往
`LocalMachine\Root` 写、而它跑在服务里（SYSTEM）拿不到写权限，日志里
只留一行 `The request is not supported` 就过去了 —— 装一百遍也发现不了。

三处改动：安装器新增第 10 步 `trust_local_ca`（服务起来、证书已生成
之后才装，顺序不能反）；面板顶部加证书状态提示 + 一键修复（提权
`certutil -addstore`）；失败都不中断安装。

状态检测踩了两个坑：**不能用 `HttpWebRequest`** —— 托盘启动时装了个
全局 `ServicePointManager.ServerCertificateValidationCallback`，对
127.0.0.1 一律放行，检测永远返回「可信」（实测删掉证书后界面照样不
提示）；改用 `SslStream`（另一套 API，不吃这个回调）后报
`调用 SSPI 失败` —— 不显式指定 `SslProtocols.Tls12` 就按系统默认走，
老系统只有 SSL3/TLS1.0，Caddy 直接断连。

### v3.76 — 顶栏品牌标改成内联 SVG

反馈「左上角小图标太丑」，不是画得不够细，是三个尺寸问题叠加：256px
PNG 缩到 46 CSS px（0.27 倍插值，边缘必柔）；云朵只占块边长 35%（512
母版的比例，46px 下只剩 16px，描边压到不足一像素糊成灰边）；图里还画
了层白色 25% 圆角块做底衬（两层圆角半径不一致，46px 下就是一块脏色差）。

换成内联 SVG + 去掉内层白块 + 云朵放大到 65%。顺手补上 `brandmark.py`
里一直没实现的 33~47px 分支：文档写着「云朵占比略微放大」，代码里恒用
0.35。

**46px 下描边镂空留不住细节，实心块的高对比才是正确解** —— 和托盘
16~24px 用实心云是同一个判断。

## 2026-10-31（晚）— 三轮 review 与对抗性测试：修掉 6 个问题


发版前的最后一道筛子。三轮分别看不同东西：静态审查找危险模式、对抗性
测试想办法把它弄坏、沙箱验证干净系统上的完整闭环。

### 静态审查发现的

**注册表键名校验用黑名单，漏了两个口子**

原来只挡引号、分号、空格：

    if any(c in key for c in "'\"; "): raise ValueError

对抗性测试一跑，两条直接穿过去：

    "LANShare
Remove-Item ..."   → 换行在 PowerShell 里等于另起一条命令
    "LANShare$(calc)"             → $() 会真的执行

改白名单 `[A-Za-z0-9_-]{1,64}`。注册表键名本来就只需要这些字符，
限定范围比穷举危险字符可靠。cli.py 和 lan.py 两处。

**Caddyfile 主机名能注入配置**

主机名直接拼进站点地址行，`hosts=["192.0.2.14 {}"]` 能闭合站点块，
后面随便塞指令。加 `_safe_host()` 白名单（字母数字点连字符）。

**托盘的两个 Process.Start 没校验**

    OpenUrl(url)     对任意 scheme 照单全收，file:/// 和 ms-settings:
                     会直接执行本地程序，不是"打开浏览器"
    OpenFolder(path) "explorer.exe" + 引号拼接，路径含 " 会截断参数

前者只放行 http/https，后者剥掉双引号。

**收件箱 token 比较不是恒定时间**

`got != want` 在第一个不同字符处提前返回，响应时间差异理论上能逐字符
爆破。改 `hmac.compare_digest`。

**并发安装没锁**

双击两次安装包，或者安装器还在跑时又手动跑 CLI —— 两个进程同时 initdb
同一目录、抢同一组端口、nssm install 同名服务，结果一半失败一半成功，
留下半死的安装还得手工清。加 msvcrt 文件锁，第二个进程直接报错。

### 对抗性测试

新增 `tests/test_adversarial.py`，41 项，专挑刁钻输入：

    鉴权      空 Bearer / 前缀 / 后缀 / 无 Bearer / Basic / 相似字符冒充
    路径穿越  ../../etc、..\..\Windows、C:\Windows、UNC、超长、null 字节
    配置注入  主机名含换行/花括号/分号/超长
    键名注入  引号分号空格、换行、反引号、$()、大小写混合
    并发      同一编号 5 线程同时提交

两个用例本身写得不对，顺手修正了：

- 全角字符冒充 token 不成立 —— HTTP header 只允许 ASCII，httpx 在客户端
  就拒了，根本到不了服务端
- 主机名尾部空格不该拒 —— 实现会 strip 后接受，是合理宽容

### 测试

139 项相关测试全过，ruff 干净。

---

## 2026-10-31 — Windows 一键安装器：让程序自己装自己

### 为什么推翻重来

第一版是「Inno Setup 直接拼 nssm 命令」：`[Run]` 段 14 条
`nssm install / set AppDirectory / set AppEnvironmentExtra`，每条都是一次字符串进出。
踩了 9 个坑，其中 7 个是引号和转义：

- `nssm install` 不带 exe 参数会弹 GUI 配置窗口，安装器的 `waituntilterminated`
  就一直等下去（实测卡死 271 秒）
- `C:\Program Files\...` 在 AppParameters 里被空格切断成 `C:\Program`
- 多个环境变量拼成一个字符串，NSSM 当成单个变量，Caddy 永远读到默认端口

单个都能修，但每加一个配置项就是一次新的引号风险，而且**没法单测、没法调试，
出错只能翻安装日志猜**。所以换路线。

### 新架构：三层

```
安装器（Inno，264 行）   解压文件 → 调 CLI → 开浏览器
lanshare_cli（1,015 行）   服务注册 / Caddyfile 生成 / 端口检查 / 健康检查 / 回滚
托盘（C# 503 行）        状态指示灯 + 常用操作
```

核心是把「指挥外部命令」从脚本搬进 Python：参数以 **list** 传给 subprocess，
Windows 自己处理转义，引号问题从根上消失；而且能 `--dry-run`、能单测。

### lanshare_cli

```bash
python -m lanshare_cli install     # 8 步，每步 OK/FAIL，失败自动回滚
python -m lanshare_cli uninstall   # 停服务删服务，数据保留
python -m lanshare_cli status      # 服务状态 + HTTP 探针
python -m lanshare_cli doctor      # 环境自检，只读

# 常用开关
--dry-run   只检查不执行
--api-port / --web-port / --data-dir / --python / --service-prefix
```

`--service-prefix` 是做隔离测试用的：装成 `e2etest-api` / 端口 9911，跟生产互不影响。

34 个单测，锁死三个历史坑：环境变量必须是独立参数、`install` 必须带 exe、
带空格路径要走 `list2cmdline`。

`NssmService.install()` 失败时会自己清理半成品服务 —— 服务条目建了但后续 `set`
失败，不留垃圾。对上层保证「要么完整成功，要么不留痕迹」。

### 托盘

C# WinForms，用 Win10 自带的 `csc.exe` 编译成 **16 KB** 单文件，不需要 Visual
Studio 或 .NET SDK。三色状态灯（绿=正常/黄=异常/红=没跑），双击开网盘，右键有
复制局域网地址、打开数据/日志文件夹、重启服务（UAC 提权）。

状态用 **HTTP 探针**而不是查服务状态 —— 实测见过服务显示 PAUSED 但端口没监听，
只有真请求 `/healthz` 才算数。5 秒一轮，状态变化才写日志（超 1MB 轮转）。

### 三个「只在别人机器上炸」的坑

**embeddable Python 的 `_pth` 少了 `..`** → `No module named lanshare_cli`。
`_pth` 是隔离模式，不读 PYTHONPATH、不读 cwd，`.` 指 runtime 目录而非安装根。
**这个坑在开发机上永远测不出来**（开发机用 venv，没有 `_pth`）。

**`AppParameters` 用 `" ".join()`** → `can't open file 'C:\Program'`。
NSSM 把整串当命令行喂给程序，不加引号就被空格切。改用 `subprocess.list2cmdline()`。

**默认服务名 `LANShare-api` 会撞生产 `lan-share-api`** —— Windows 服务名比较
**不区分大小写**。默认值改成 `lanshare`，代码里写了注释说明不能改回去。

### 验证

隔离环境完整跑通（服务名 `e2etest`、端口 9911/9912、目录 `LANShareE2E`）：

```
装      exit 0 → 2/2 服务 Running → 探针 200 → 首页 307
托盘    Unknown → Healthy → Down → Healthy（停服务 19 秒后抓到，恢复自动回来）
卸      exit 0 → 服务清除 → 目录删净 → 数据保留
生产    lan-share-api/caddy/postgresql 全程零影响
```

安装包 **34 MB**（LZMA2 压缩，未压缩 136 MB）。门禁 594 项全过。

### 已知限制

- **纯 HTTP，不走 TLS。** Caddy 的 `tls internal` 对无主机名站点（`:端口` 写法）
  无法签证书，客户端握手一律 `TLSV1_ALERT_INTERNAL_ERROR`（实测）。家用内网
  HTTP 够用，也不用点「继续访问」。要 HTTPS 得有域名或固定证书文件。
- 还没在**没有 Python / 没有 .NET 4.8** 的干净机器上验证过。
- 托盘开机自启（HKA Run 注册表项）没做重启验证。
- 安装向导页（数据目录选择等）只在静默模式下跑过，图形界面没点过。

## 2026-10-30 — App v1.1.17+124：双更新源 + 登录页不再预填地址

### 检查更新改成双源（服务端 + GitHub）

原实现只查 `/api/app/latest`，读的是服务端 `App安装包/version.json`——**靠人工
在发版时同步**。发 1.1.16 时就漏了这步：GitHub Release 传了，服务端目录还是
1.1.15，用户在 App 里点检查更新永远看不到新版。

现在两路并发，取 build 号大的那个：

- **服务端源** `/api/app/latest` —— 自家分发，完全不依赖外网
- **GitHub 源** `api.github.com/repos/AK47ZZQ/lan-share/releases/latest`

任一失败静默跳过，两个都不通才返回 null（UI 说「已是新版」而不是抛红字错误）。
GitHub 挂了回落服务端，服务端忘了同步还有 GitHub 兜着。

三个解析细节：
- `tag_name` 是 `app-v1.1.16`，**没有 build 号**，得从 asset 文件名
  `lanshare-v1.1.16+123.apk` 里抠出 123 来比大小
- `assets[].digest` 是 `sha256:...` 格式，剥掉前缀留 hex，与服务端下发的
  sha256 对得上（实测同一个包，值完全一致）
- 用 `api.github.com` 而不是 `raw.githubusercontent.com`：后者国内常被污染，
  且只有 API 才给 digest 字段——而客户端是 sha256 缺失即拒装的 fail-closed 策略

**下载路径分两种**：GitHub 源用 `browser_download_url` 直链，走独立 Dio；
**绝不复用 ApiClient**，否则会把 Bearer token 一并发给 github.com，等于把
登录凭据交出去。

### 登录页不再预填服务器地址

`Prefs.addresses()` 首用时会塞 `https://192.0.2.14:8765` 当示例。问题：
App 是公开分发的，任何写死的地址都会被当成「作者家的服务器」，而且装作
「已经连上了」，新手看不出是占位符。

现在：首用地址框**留空**，服务器条显示「点击设置服务器地址」，状态标签显示
「未设置」，占位提示改成 `https://你的服务器IP:8765` + 一句「局域网和公网都用
8765 端口」。

`lastAddress()` 相应允许返回空串——只在「装了从没登录过」出现，已登录路径
（api_client / transfer / backup / about）读到的都是有值的历史地址。

### 验证新装行为

**覆盖安装看不到这个变化**：`SharedPreferences` 里存着上次用的地址
（`server_addresses` / `server_last`），会照常预填。要验默认行为得卸载重装
或清 App 数据。

### 顺带

- 新增 `test/update_github_test.dart` 14 项：正常解析 + 六种边界返回 null +
  digest 缺失的降级处理 + isNewer 比较
- `flutter analyze` 0 issue / `flutter test` 254 项（原 240 + 新 14）

## 2026-10-30 — App v1.1.16+123：依赖维护版（零功能改动）

- 本版**没有功能变更**，起因是仓库转 public 后开启了 Dependabot。它十分钟内
  扫出 **14 个漏洞（2 critical / 3 high / 5 medium / 4 low）**，全部修完：
  - `dompurify 3.4.14 → 3.4.17`（critical）：`SAFE_FOR_TEMPLATES` 绕过，
    影响前端 Markdown 的 XSS 净化面。
  - `anyio 4.12.1 → 4.14.2`（critical）：TLSStream 用 IDNA 2003 编码主机名，
    可能导致 TLS 证书校验被绕过。starlette 的传递依赖。
  - 前端：`vite 6.4.x → 8.3.5`、`vue 3.5.44`、`vue-router 5.4.0`、
    `source-map-js 1.2.2`、`brace-expansion 2.1.7`
  - 服务端：`qrcode 7.4.2 → 8.2`、`tzdata 2025.3 → 2026.5`、pip 侧 10 个包
  - App：`package_info_plus 8.3.1 → 9.0.1`、`archive 4.2.0 → 4.5.2`
- **TypeScript 停在 5.9.3**：Dependabot 一条 PR 里把 `typescript 5.7 → 7.0`、
  `vite 6 → 8`、`vue-router 4 → 5` 一起提。TS 7 是 Go 重写版、把 `lib/tsc`
  从 `exports` 移除，`vue-tsc 3.3.13` 尽管 peerDeps 声明 `>=5.0.0` 却直接
  `ERR_PACKAGE_PATH_NOT_EXPORTED`。已回退 TS，其余主版本实测可用。
- **CI 三处补强**（本轮真正的收尾）：
  - `web-tests` 增加 `npm ci` + `npm run build`：此前只跑 `node:test`，
    完全不碰 `vue-tsc` / `vite`，所以 TS 7 那个 PR 上 CI 全绿、合并后才炸。
  - 新增 `mobile` job：`flutter 3.47.1` 钉死 + `analyze`（0 issue）+ `test`。
    此前 `mobile/` 一行都不测，`local_auth 2.3 → 3.0` 那种破坏性升级
    （`AuthenticationOptions` 类被移除，analyze 报 7 个 error）在 PR 上显示为绿。
  - `concurrency`：PR 上取消旧 run，main 上保留完整记录。
- **Dependabot 常规版本 PR 已停**：删除 `dependabot.yml`，只留仓库级
  `security updates`（有 CVE 才提）。版本锁定不受影响——`requirements.txt`
  23 项全 `==`，`package-lock.json` 与 `pubspec.lock` 都在，CI 用 `npm ci`。
- 验证：pytest 560 项、`flutter analyze` 0 issue、`flutter test` 240 项、
  `npm run build` + 19 项前端单测、APK 签名构建通过。
- **已知上游风险**：Flutter 3.47.1 编译时报

  ```
  WARNING: plugins that apply Kotlin Gradle Plugin (KGP):
    file_picker, mobile_scanner, package_info_plus, share_plus
  Future versions of Flutter will fail to build if your app uses plugins that apply KGP.
  ```

  这四个插件还没迁到 Built-in Kotlin。当前 3.47.1 能正常编译（APK 已产出），
  但未来升 Flutter 会直接失败。属于上游生态问题，本地无法修，只能等插件作者
  更新。这也是 CI 把 Flutter 版本钉死在 3.47.1 的另一个理由。

## 2026-10-24（晚·发版）— App v1.1.15+122：复测基线版（零功能改动）

- 本版无功能变更：照片详情「局域网失败」的修复在服务端（v3.69.1 限速
  误伤 240→600），App 侧加载失败的「重试」按钮上一版就有。发这版的目的：
  ①让更新历史里能看到服务端两批修了什么；②请主人**借本版在家连局域网
  复测照片详情**（反馈 2305）——复测通过即销案。配套服务端 v3.70。
- 分发物当晚已完成**模拟器装包冒烟**（镜像修复后补做）：安装 Success
  （versionCode=122）→ 冷启动自动发现服务器 → TOFU 指弹窗与 cert.pem
  三方一致 → 配对码登录 → 主页五页签 + 真实数据（详见 STATUS-v3 当日段）。

## 2026-10-24（晚）— 服务端 v3.70：观测跨重启存活 + 预发环境 + 巡检 v2 + 依赖安全升级

- **限速观测不再每天清零**：`/api/metrics` 的限速拒绝累计此前随进程重启归零
  ——而这台服务器每天随开关机重启，仪器几乎看不到完整的晚上。现在历史累计
  持久化（每小时落盘、重启续算），并新增**启动史**（此前 10 月停机 15 天
  只能靠数据库日志考古才能还原）。
- **预发环境**：`python scripts/dev_stack.py start` 一条命令起隔离全栈
  （临时 PG + 独立端口 9766 + 独立数据目录，healthz 自检失败自动清场）
  ——E2E 先打预发，不再弹主人手机。
- **巡检 v2**（中午 12:00）：每天必写心跳文件（巡检活着的证据）；异常时
  给全家 App/Web 发**站内通知**（不再只是没人看的本地文件）；反馈超过
  48 小时无人领会提醒。
- **依赖安全升级**：pillow 12.3.0→13.0.0（**三条 CVE**，图片解析面）；
  fastapi 0.133.1→0.162.0；pip/setuptools 工具链 advisory 归零。
- **文档守卫**：新增 `check_docs_baseline.py` 门禁——AGENTS 基线数字与现实
  不符就红（首秀即抓到四项过期数字）；新增一页纸《主人简报》（docs/主人简报.md）；
  新功能排期冻结至照片第二份拷贝完成首次真实备份。
- 门禁：SQLite **560** / PG **557+3** 全绿；ruff/mypy/文档守卫/UI 分层/
  方言守卫全过；重启后生产库实证 boot_history 与持久化键就位。

## 2026-10-24 — 服务端 v3.69.1：修复「局域网看照片详情失败」+ 首次 Tier 2 恢复演练 + 巡检重建

- **修复「公网正常、局域网点开照片详情失败」**（反馈 20261022002929-2305；
  根因已实证、线上 260 连发验证通过，**主人真机复测尚未做**——结论以复测为准）：
  根因是 LAN 侧媒体限速桶 240/分——经 Caddy 反代后全家共享同一个 127.0.0.1
  桶，照片墙每批 120 张缩略图，两批 + 详情高清即触顶 429（App 显示「加载失败」）；
  公网桶宽 1200 且批量减半所以无感。**LAN 媒体桶 240→600/分**。请主人在局域网
  复测照片详情；若仍偶发，把 Web「运行状态」里的限速拒绝计数（media:lan）
  反馈给值守会话。线上验证：重启后 260 次连续探针零 429。
- **Tier 2 恢复演练首次执行**（此前从未做过）：用项目自带 zonky 起一次性临时
  集群，把当日元数据备份全量恢复，行数与生产逐项一致（users=3 / photos=5352 /
  22 张表）——元数据备份的可恢复性自此有实证；月度复演方法与坑见 OPERATIONS
  4.1。**照片原图零备份（单拷贝）仍待主人拍板第二份拷贝。**
- **每日巡检重建**：原 01:00 cron 巡检自 09-19 起静默停摆（隧道日志探针实证），
  已重建为每日 12:00 只读巡检（healthz 双端点 + 磁盘水位 + 备份状态检查），
  异常写 `logs/patrol/ALERT-*.md`；新巡检不 import app 代码、不再锁文件。
- 门禁：SQLite **557** / PG **554+3**（改动后当日复跑全绿）；web 19 /
  flutter 240 同日复验绿。
