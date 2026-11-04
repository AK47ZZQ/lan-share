// WebView2 版控制面板：界面是 ui/index.html，C# 只负责喂数据和执行动作。
//
// 为什么换掉 WinForms 手绘版
// ─────────────────────────────────────────────────────────────
// 旧版每个控件手算坐标，DPI 150% 下还有「缩放前/缩放后」两套坐标系，
// 光「关闭局域网服务」一个按钮就调了四轮：宽度不够被裁 → 加宽跑出卡片
// → 用错基准值 → 在 Scale 之后设坐标导致位置错乱。
//
// 换成 HTML 之后：flexbox 自动排版、浏览器自己处理 DPI、改样式不用重编译。
// 而且能直接复用手机 App / Web 端的品牌语言（同一套渐变和圆角）。
//
// 架构
// ─────────────────────────────────────────────────────────────
//   C# 每 4 秒跑一次 lanshare_cli status → 拼成 JSON → PostWebMessageAsJson
//   HTML 收到 message 就渲染；点按钮就 postMessage 回来，C# 执行。
//
// 为什么不直接在 HTML 里 fetch：后端没开 CORS，file:// 页面拿
// http://127.0.0.1:8766 会被浏览器拦。走 host 桥接既不动后端，
// 也不受同源策略影响。
//
// 回退
// ─────────────────────────────────────────────────────────────
// WebView2 Runtime 不是所有机器都有（LTSC、精简版可能没装）。
// 拿不到运行时版本就抛 WebView2Unavailable，由 LanShareTray 回退到
// 原来的 WinForms 面板 —— 那边代码一行没删。

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Text;
using System.Threading.Tasks;
using System.Windows.Forms;
using System.Web.Script.Serialization;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace LanShare
{
    /// <summary>WebView2 运行时不可用（没装 Runtime / 加载失败）。调用方应回退。</summary>
    internal sealed class WebView2Unavailable : Exception
    {
        public WebView2Unavailable(string msg) : base(msg) { }
    }

    internal sealed class WebPanel : Form
    {
        [System.Runtime.InteropServices.DllImport("user32.dll")]
        private static extern uint GetDpiForWindow(IntPtr hWnd);

        private readonly InstallConfig _cfg;
        private readonly CliRunner _cli;

        private WebView2 _web;
        private System.Windows.Forms.Timer _timer;
        private bool _pageReady;
        private bool _busy;
        private DateTime _lastPush = DateTime.MinValue;
        private string _lastQrSrc;
        // 二维码对应的地址。IP 变了（换网卡/换 WiFi）就得重画 —— 只按“空不空”
        // 判断缓存会让二维码一直指向旧地址，扫出来是全屋谁都连不上的 IP。
        private string _lastQrUrl;
        // 上次取二维码的时间。取失败（API 没起来）时也要退避 ——
        // FetchQrFor 是同步 HTTP（4 秒超时）且跑在 UI 线程，不留退避的话
        // 每 4 秒的刷新都会卡一次，面板又变成「点不动」。
        private DateTime _lastQrAt = DateTime.MinValue;
        // 上一次完整状态（CLI 结果），用于首帧沿用、避免地址和二维码闪没
        private Dictionary<string, object> _lastPayload;
        // 状态查询是否在途，防止定时器和 force 刷新叠加出两个 Python 进程
        private bool _stateInFlight;

        public WebPanel(InstallConfig cfg, CliRunner cli)
        {
            _cfg = cfg;
            _cli = cli;

            Text = "LAN Share";
            FormBorderStyle = FormBorderStyle.Sizable;
            MaximizeBox = true;
            MinimumSize = new Size(720, 520);
            StartPosition = FormStartPosition.CenterScreen;
            BackColor = Color.FromArgb(0xF5, 0xF7, 0xFA);
            Icon = Theme.AppIcon;
            // AutoScaleMode.None 是和旧面板对齐的关键一条。
            //
            // 默认的 AutoScaleMode（Font/Inherit）会在 PerMonitorV2 清单已经在
            // 加载器层面处理过 DPI 之后，再按字体尺寸缩放一次 —— 结果是窗口
            // 逻辑 900 宽，WebView2 拿到的 CSS 视口只有 600，卡片全挤成一条、
            // 地址框只露得出 https://192.168.。旧面板早就用 None 绕开了这个坑，
            // 新面板漏了这条，表现出来像「CSS 写错了」，实际是缩放叠加了两遍。
            AutoScaleMode = AutoScaleMode.None;
            ClientSize = new Size(900, 620);

            // DPI-aware 进程里 ClientSize 是物理像素，900 物理在 150% 下
            // 只等于 600 个 CSS px —— WebView2 就按 600 排版，卡片挤成一条。
            // 想要 900 个 CSS px 就得给 900×缩放 的物理尺寸，和旧面板
            // ApplyDpiScale 里 Scale(k, k) 是同一个道理。
            ApplyDpiScale();

            // 主屏太小就收一收，别让窗口跑出屏幕
            var wa = Screen.FromPoint(Cursor.Position).WorkingArea;
            if (Width > wa.Width || Height > wa.Height)
            {
                Size = new Size(
                    Math.Min(Width, wa.Width - 20),
                    Math.Min(Height, wa.Height - 20));
            }
        }

        /// <summary>按系统 DPI 放大客户区，让 CSS 视口回到设计值。</summary>
        private void ApplyDpiScale()
        {
            uint dpi = 96;
            try
            {
                IntPtr h = Handle;          // 触碰 Handle 会强制创建窗口句柄
                if (h != IntPtr.Zero) dpi = GetDpiForWindow(h);
            }
            catch (Exception) { }
            if (dpi == 0) dpi = 96;
            float k = dpi / 96f;
            if (k > 1.01f) Scale(new SizeF(k, k));

            // 构造期就把「这台机器有没有 WebView2」问清楚。
            //
            // 为什么不在 OnLoad 里检测：OnLoad 是 async void，抛出的异常会
            // 直接崩进程，调用方（LanShareTray.CreatePanel）根本接不住，
            // 回退逻辑等于白写。这里同步查一次运行时版本，拿不到就立刻抛
            // WebView2Unavailable，让上层干净地退回 WinForms 面板。
            string ver = null;
            try { ver = CoreWebView2Environment.GetAvailableBrowserVersionString(); }
            catch (Exception ex)
            {
                throw new WebView2Unavailable("WebView2 运行时未安装：" + ex.Message);
            }
            if (string.IsNullOrEmpty(ver))
            {
                throw new WebView2Unavailable("WebView2 运行时未安装（版本为空）");
            }
        }

        protected override async void OnLoad(EventArgs e)
        {
            base.OnLoad(e);

            string indexPath = Path.Combine(AppDomain.CurrentDomain.BaseDirectory,
                                            "ui", "index.html");
            if (!File.Exists(indexPath))
            {
                throw new WebView2Unavailable("找不到界面文件：" + indexPath);
            }

            _web = new WebView2 { Dock = DockStyle.Fill };
            Controls.Add(_web);

            try
            {
                // 用户数据目录必须显式指定，否则 WebView2 默认建在
                // exe 同级的 "<exe名>.WebView2" 里 —— 那是浏览器缓存、
                // GPU cache、代码缓存，实测 73 MB。跟着安装目录跑意味着
                // 安装包会带上它、卸载还得删、用户看到莫名其妙的大文件夹。
                string udf = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                    "LANShare", "WebView2");
                Directory.CreateDirectory(udf);
                var env = await CoreWebView2Environment.CreateAsync(null, udf);
                await _web.EnsureCoreWebView2Async(env);
            }
            catch (Exception ex)
            {
                // 走到这里说明构造期检测通过、但真正初始化失败（极少见，
                // 比如运行时文件损坏）。没法再回退了，给用户一个说得清的提示。
                MessageBox.Show(
                    "界面初始化失败，将关闭这个窗口。" +
                    Environment.NewLine + Environment.NewLine + ex.Message,
                    "LAN Share", MessageBoxButtons.OK, MessageBoxIcon.Error);
                Close();
                return;
            }

            // 调试件：禁掉右键菜单、状态栏、缩放，让它像个正经应用窗口
            var s = _web.CoreWebView2.Settings;
            s.AreDefaultContextMenusEnabled = false;
            s.IsStatusBarEnabled = false;
            s.AreDevToolsEnabled = false;
            s.IsZoomControlEnabled = false;

            // 150% DPI 下 WebView2 会自己再放大一遍，结果 CSS 视口被压到 300px 左右，
            // 卡片全挤成一条、地址框只露得出 https://192.168. —— 看起来像 CSS 写错了，
            // 其实是缩放。显式钉死 1.0，让 1 CSS px 严格等于 1 个 DIP。
            _web.ZoomFactor = 1.0;

            _web.CoreWebView2.WebMessageReceived += OnWebMessage;
            // 加时间戳强制重新加载：WebView2 会缓存 file:// 页面，
            // 改了 index.html 之后不加这个，看到的还是上一次的内容（调了半天样式没反应）。
            string url = new Uri(indexPath).AbsoluteUri
                       + "?v=" + DateTime.Now.Ticks;
            _web.CoreWebView2.Navigate(url);

            // 每 4 秒推一次状态。比旧版的 5 秒略快一点，
            // 因为现在推送是纯内存操作（图片走缓存），成本低。
            _timer = new System.Windows.Forms.Timer { Interval = 4000 };
            _timer.Tick += delegate { PushStateAsync(false); };
            _timer.Start();

            // 首帧：页面 ready 之后马上推一次，别让用户对着「正在读取」等 4 秒
            _web.CoreWebView2.NavigationCompleted += async delegate
            {
                await Task.Delay(120);
                PushStateAsync(true);
            };
        }

        protected override void OnFormClosed(FormClosedEventArgs e)
        {
            if (_timer != null) { _timer.Stop(); _timer.Dispose(); _timer = null; }
            if (_web != null) { _web.Dispose(); _web = null; }
            base.OnFormClosed(e);
        }

        // ──────────────────────────────────────────────────────
        // 推状态
        // ──────────────────────────────────────────────────────

        private void PushStateAsync(bool force)
        {
            if (!_pageReady) return;
            // 正在跑后台动作（开局域网/检查更新）时不要叠刷新 —— 但绝不能因此
            // 把刷新永久挡住，见下面 finally 里的复位。
            if (_busy) return;
            if (_stateInFlight) return;
            if (!force && (DateTime.Now - _lastPush).TotalSeconds < 5) return;
            _lastPush = DateTime.Now;

            // 🔴 状态刷新完全不碰 CLI。
            //
            // 2026-11-02 用户实测「状态更新很卡很慢，局域网状态的更新根本没有」。
            // 上一版虽然加了本地首帧，后面仍然异步发了 `lan status`（超时 60 秒），
            // 而 _stateInFlight 期间所有刷新一律 return —— 于是 CLI 哪天慢，
            // 界面就几十秒不动，看起来像卡死。
            //
            // 现在全是本机数据：
            //   服务状态   → ServiceController 查 SCM（毫秒）
            //   局域网开关 → 8765 端口监听地址（LanListeningOnAll）
            //   局域网地址 → 网卡枚举（LocalLanIp）
            //   二维码     → 本地 /local/qr 接口
            // 只有点按钮时才真正调 CLI（那时用户在等，慢也认）。
            _stateInFlight = true;
            try
            {
                Send("state", BuildLocalPayload());
            }
            catch (Exception ex)
            {
                AppendLog("推送状态失败：" + ex.Message);
            }
            finally
            {
                // 同步完成，立刻放开。之前放在异步 ContinueWith 的 finally 里，
                // 而 Send 本身在 try 外 —— Send 一抛异常，_stateInFlight 就永远
                // 是 true，界面从此再不刷新（表现和「点按钮没反应」一样）。
                _stateInFlight = false;
            }
        }

        /// <summary>
        /// 只靠本机信息拼一份状态：服务行查 SCM，局域网/二维码沿用上一次
        /// CLI 的结果。用来让界面第一帧就有内容，不用干等 CLI。
        /// </summary>
        private Dictionary<string, object> BuildLocalPayload()
        {
            var p = new Dictionary<string, object>
            {
                { "apiPort", _cfg.ApiPort },
                { "webPort", _cfg.WebPort },
                { "dataPath", _cfg.DataFolder },
                { "services", BuildServiceRows(null) },
                { "healthy", false },
                { "lanOn", false },
                { "lanUrl", "" },
                { "lanHint", "正在读取局域网状态…" },
                { "wanUrl", "" },
                { "qr", null },
                { "certTrusted", true },
                { "backup", FetchBackupState() },
                { "certDaysLeft", CertDaysLeft() },
            };

            // 局域网地址不要等 CLI —— 直接问网卡。
            //
            // 2026-11-01 用户反馈「点开局域网服务有成功弹窗，但地址和二维码
            // 不显示、按钮状态也不变」。查下来是 lan status 那次调用失败了
            // （用户机器上 HTTPS 自检慢，20 秒超时 → dict 为 null →
            // addresses 空 → lanUrl 空 → 地址和二维码一起消失），
            // 而 enable 是 30 秒超时所以成功弹窗照出。
            // 两件事用了不同的超时，表现就成了「说开了但看不见」。
            //
            // 地址本来就是本机网卡属性，读一下就有，几毫秒。
            string ip = LocalLanIp();
            if (ip != null)
            {
                p["lanHint"] = "正在确认监听状态…";
                p["__localIp"] = ip;
            }

            // 局域网开关：本地判断，不等 CLI
            bool? listeningAll = LanReachable();
            if (listeningAll == true)
            {
                p["lanOn"] = true;
                p["healthy"] = true;
                p["lanHint"] = ip != null
                    ? "同一个 WiFi 下的手机、平板都能访问"
                    : "已开启，但没检测到有网关的网卡";
                if (ip != null)
                {
                    string url = "https://" + ip + ":" + _cfg.WebPort + "/";
                    p["lanUrl"] = url;
                    // 二维码也别等 CLI —— 直接从本地 API 端点取，顺便把结果缓存住
                    // 缓存按地址校验（换网卡/IP 就得重画）；失败退避 30 秒，
                    // 否则 API 没起来时每帧都卡一次同步 HTTP（UI 线程）
                    bool needQr = _lastQrSrc == null || _lastQrUrl != url;
                    if (needQr && (DateTime.Now - _lastQrAt).TotalSeconds >= 30)
                    {
                        _lastQrSrc = FetchQrFor(url);
                        _lastQrUrl = url;
                        _lastQrAt = DateTime.Now;
                    }
                    if (!string.IsNullOrEmpty(_lastQrSrc))
                        p["qr"] = _lastQrSrc;
                }
            }
            else if (listeningAll == false)
            {
                p["lanOn"] = false;
                p["lanHint"] = "现在只有这台电脑能访问。开启后同一个 WiFi 下的手机也能用";
            }
            else
            {
                // 刚装完 / 刚重启的那几秒，Caddy 还在起，这里如实说「正在启动」
                // 而不是「没在运行」—— 后者会让人以为装坏了
                p["lanHint"] = "服务正在启动或暂时不可用（刚装完等几秒）；一直这样点「重启服务」";
            }

            // 上一次的完整结果只用来补「本地判断不出来」的字段。
            //
            // 🔴 以前这里把 lanOn / lanUrl / qr 也一起覆盖了回去。
            //
            // 上面的代码刚根据真实监听状态算出「局域网开着 / 地址是 xxx  /
            // 二维码已取到」，紧接着就被上一次的 false / "" / null 盖掉。
            // 只要曾经有过一次 CLI status 超时（那条路径里 lanOn 恒为 false），
            // 这个假状态就永久粘在 _lastPayload 上 ——
            // 表现正是「服务明明起来了，GUI 状态一直不变、二维码也不生成」
            // （2026-11-02 实机）。
            //
            // 现在：局域网四件套一律以本地实时判断为准，只有本地判断不出来
            // （listeningAll == null，端口没在监听）时才回落旧值，
            // 既不会再粘住假状态，也保住了「第一帧不把地址闪没」的初衷。
            if (_lastPayload != null)
            {
                foreach (var key in new[]
                {
                    "wanUrl", "certTrusted", "lanBlocked", "noNetwork",
                })
                {
                    if (_lastPayload.ContainsKey(key)) p[key] = _lastPayload[key];
                }

                if (listeningAll == null)
                {
                    foreach (var key in new[]
                    {
                        "healthy", "lanOn", "lanUrl", "lanHint", "qr",
                    })
                    {
                        if (_lastPayload.ContainsKey(key)) p[key] = _lastPayload[key];
                    }
                }
            }
            return p;
        }

        /// <summary>
        /// 本机最适合展示给局域网用的 IPv4 地址（有网关、metric 最小；虚拟网卡靠后）。
        /// 拿不到返回 null。
        /// </summary>
        private static string LocalLanIp()
        {
            // 先问路由表：「手机该连哪个 IP」的答案就是本机默认路由的出口地址。
            //
            // UDP Connect 不会真的发包，只是让系统按路由表选出口并绑定本地地址。
            // 2026-11-02 实机：机器上同时挂着以太网 192.168.89.118 和另一块
            // 带网关的网卡（Clash/VPN 一类），按 metric 挑会挑到后者 ——
            // 地址框和二维码就指向一个手机根本连不上的 IP。
            try
            {
                using (var sock = new System.Net.Sockets.Socket(
                    System.Net.Sockets.AddressFamily.InterNetwork,
                    System.Net.Sockets.SocketType.Dgram,
                    System.Net.Sockets.ProtocolType.Udp))
                {
                    sock.Connect("223.5.5.5", 53);
                    var ep = sock.LocalEndPoint as System.Net.IPEndPoint;
                    if (ep != null)
                    {
                        string routed = ep.Address.ToString();
                        if (!string.IsNullOrEmpty(routed)
                            && routed != "0.0.0.0"
                            && !routed.StartsWith("127.")
                            && !routed.StartsWith("169.254."))
                        {
                            return routed;
                        }
                    }
                }
            }
            catch (Exception) { /* 没有默认路由（纯内网/没联网）就退回网卡枚举 */ }

            try
            {
                string best = null;
                long bestMetric = long.MaxValue;
                foreach (var ni in System.Net.NetworkInformation.NetworkInterface.GetAllNetworkInterfaces())
                {
                    if (ni.OperationalStatus != System.Net.NetworkInformation.OperationalStatus.Up)
                        continue;
                    if (ni.NetworkInterfaceType == System.Net.NetworkInformation.NetworkInterfaceType.Loopback)
                        continue;
                    var props = ni.GetIPProperties();
                    bool hasGateway = false;
                    foreach (var gw in props.GatewayAddresses)
                    {
                        if (gw.Address != null &&
                            gw.Address.AddressFamily == System.Net.Sockets.AddressFamily.InterNetwork)
                        {
                            hasGateway = true;
                            break;
                        }
                    }
                    if (!hasGateway) continue;

                    long metric = 0;
                    foreach (var ipInfo in props.UnicastAddresses)
                    {
                        if (ipInfo.Address.AddressFamily != System.Net.Sockets.AddressFamily.InterNetwork)
                            continue;
                        // 169.254.x.x 是自动分配的无效地址
                        if (ipInfo.Address.GetAddressBytes()[0] == 169) continue;
                        try { metric = props.GetIPv4Properties().Index; } catch { metric = 0; }
                        if (metric < bestMetric)
                        {
                            bestMetric = metric;
                            best = ipInfo.Address.ToString();
                        }
                    }
                }
                return best;
            }
            catch (Exception)
            {
                return null;
            }
        }

        /// <summary>
        /// 拿某个地址对应的二维码，返回 data URI；失败返回 null。
        /// 单独抽出来是因为「本地首帧」和「CLI 返回」两条路径都要用。
        /// </summary>
        private string FetchQrFor(string targetUrl)
        {
            try
            {
                string qrUrl = "http://127.0.0.1:" + _cfg.ApiPort
                             + "/local/qr?format=png&text="
                             + Uri.EscapeDataString(targetUrl);
                using (var img = _cli.FetchQrPng(qrUrl, 4000))
                {
                    if (img == null) return null;
                    using (var ms = new MemoryStream())
                    {
                        img.Save(ms, System.Drawing.Imaging.ImageFormat.Png);
                        return "data:image/png;base64," + Convert.ToBase64String(ms.ToArray());
                    }
                }
            }
            catch (Exception)
            {
                return null;
            }
        }

        /// <summary>
        /// 本地根 CA 还有多久到期（天）。拿不到返回 -1。
        ///
        /// 为什么需要：Caddy 的 internal CA 根证书默认有效期 10 年。它到期那天，
        /// 所有已经信任它的设备（家里每台手机、平板、电脑）会同时开始报
        /// 证书错误，而 Caddy 不会提前告诉你，也不会自己换根 —— 换根意味着
        /// 所有客户端要重新信任一次。
        ///
        /// 对一个打算长期跑下去的系统，这件事必须提前一年就开始提醒，
        /// 而不是到期当天才发现全家的设备连不上。
        /// </summary>
        private int CertDaysLeft()
        {
            try
            {
                string root = Path.Combine(_cfg.InstallFolder, "certs", "caddy",
                                           "pki", "authorities", "local", "root.crt");
                if (!File.Exists(root)) return -1;
                var cert = new System.Security.Cryptography.X509Certificates.X509Certificate2(root);
                var left = (cert.NotAfter - DateTime.Now).TotalDays;
                return left > 0 ? (int)left : 0;
            }
            catch (Exception)
            {
                return -1;
            }
        }

        // 备份状态的缓存。
        //
        // 🔴 不能在这里同步发 HTTP。BuildLocalPayload 是在 UI 线程上跑的，
        // ApiGet 的超时是 5 秒 —— API 服务没起来（或正在重启）时，每次刷新
        // 都会把界面冻住 5 秒。那正是 2026-11-02 修掉的「卡死」，从另一个
        // 门口再进来一次。
        //
        // 所以：永远返回缓存，过期了就在后台线程异步刷，UI 线程一次都不等。
        private Dictionary<string, object> _backupCache;
        private DateTime _backupCacheAt = DateTime.MinValue;
        private volatile bool _backupFetching;

        private Dictionary<string, object> FetchBackupState()
        {
            if (!_backupFetching && (DateTime.Now - _backupCacheAt).TotalSeconds > 10)
            {
                _backupFetching = true;
                Task.Run(() =>
                {
                    try
                    {
                        var fresh = ApiGet<Dictionary<string, object>>("/local/backup", d => d, null);
                        if (fresh != null && fresh.Count > 0)
                        {
                            _backupCache = fresh;
                            _backupCacheAt = DateTime.Now;
                        }
                    }
                    catch (Exception)
                    {
                        // 拿不到就继续用上一次的，卡片不会闪空
                    }
                    finally
                    {
                        _backupFetching = false;
                    }
                });
            }
            return _backupCache ?? new Dictionary<string, object>();
        }

        /// <summary>
        /// 保存公网访问地址。
        ///
        /// 2026-11-02 用户反馈「公网访问没有配置栏、没法填 URL」—— 查下来这个
        /// 输入框从 WebView2 面板第一版起就是 readonly，一直没有写入口。
        /// 之前能填的地方是网页端的管理页，不是托盘面板。
        /// </summary>
        private void SaveWanUrl(string url)
        {
            url = (url ?? "").Trim();
            if (url.Length > 0
                && !url.StartsWith("http://", StringComparison.OrdinalIgnoreCase)
                && !url.StartsWith("https://", StringComparison.OrdinalIgnoreCase))
            {
                SendToast("地址要以 http:// 或 https:// 开头");
                return;
            }
            string json = "{\"url\": " + Quote(url) + "}";
            if (!ApiPost("/local/public-url", json))
            {
                SendToast("保存失败：服务没有响应");
                return;
            }
            AppendLog("公网地址已保存: " + (url.Length > 0 ? url : "(清空)"));
            SendToast(url.Length > 0 ? "公网地址已保存" : "公网地址已清除");
            PushStateAsync(true);
        }

        /// <summary>
        /// 一键接入公网：把穿透工具的域名写进 Caddyfile，让这个域名真能被访问。
        ///
        /// 🔴 为什么光填地址不够：Caddy 只对站点块里写明的**主机名**签证书。
        /// 穿透工具给的是陌生域名，不在名单里 —— 客户端用这个域名访问时
        /// TLS 握手就被直接掐断（2026-11-03 实测：HTTP 000 /
        /// SEC_E_INTERNAL_ERROR），连 HTTP 层都到不了。
        ///
        /// 所以这里要：改配置 → 重启 Caddy（重签证书）→ 自检 → 自动填地址。
        /// </summary>
        private void AttachWan(string host)
        {
            host = (host ?? "").Trim();
            if (host.Length == 0)
            {
                SendToast("先填穿透工具给的地址，比如 cn-bj.frp.one:35123");
                return;
            }

            RunBusy("正在接入公网域名（改配置 + 重签证书，约 20 秒）…", () =>
            {
                string err;
                var ret = _cli.RunLan("wan", 90000, out err, host);

                string dump;
                try
                {
                    dump = ret == null
                        ? "null"
                        : new JavaScriptSerializer { MaxJsonLength = int.MaxValue }
                            .Serialize(ret);
                }
                catch (Exception ex) { dump = "<序列化失败：" + ex.Message + ">"; }
                if (dump.Length > 1200) dump = dump.Substring(0, 1200) + "…(截断)";
                AppendLog("WanAttach: addr=" + host + " err=" + (err ?? "(null)") + " data=" + dump);

                if (ret == null) return "接入失败：" + (err ?? "命令没有返回");

                bool ok = false;
                try { ok = Convert.ToBoolean(ret["success"]); } catch (Exception) { }
                string pub = ret.ContainsKey("public_url") ? Str(ret, "public_url") : "";

                if (!ok)
                {
                    // 把失败那一步点名 —— 「接入失败」四个字帮不了用户
                    string detail = "";
                    bool sawFail = false, onlyPublicFailed = true;
                    foreach (var s in List(ret, "steps"))
                    {
                        var d = s as Dictionary<string, object>;
                        if (d == null) continue;
                        bool sok = false;
                        try { sok = Convert.ToBoolean(d["ok"]); } catch (Exception) { }
                        if (sok) continue;
                        sawFail = true;
                        string nm = Str(d, "name");
                        if (detail.Length == 0) detail = nm + "：" + Str(d, "detail");
                        if (nm != "公网连通性") onlyPublicFailed = false;
                    }

                    // 「配置写对了、只是公网还没通」要和「配置就失败了」分开对待。
                    //
                    // 前者最常见的原因是隧道类型建错了（建成 HTTPS/HTTP 类型，
                    // 由穿透服务端接管 TLS、需要上传证书），改一下隧道类型就好 ——
                    // 这时候 Caddyfile 和证书都已经就位，把地址存下来，
                    // 用户改完不用回来再点一次「一键接入」。
                    bool hasPub = ret.ContainsKey("public_url") && Str(ret, "public_url").Length > 0;
                    if (sawFail && onlyPublicFailed && hasPub)
                    {
                        string j2 = "{\"url\": " + Quote(Str(ret, "public_url")) + "}";
                        if (ApiPost("/local/public-url", j2))
                            AppendLog("WanAttach: 地址已保存，等公网打通 " + Str(ret, "public_url"));
                        return "地址已保存，但公网还不通 —— " + detail;
                    }
                    return "接入失败" + (detail.Length > 0 ? "（" + detail + "）" : "");
                }

                // 成功就地保存，省掉用户再点一次「保存」
                if (pub.Length > 0)
                {
                    string json = "{\"url\": " + Quote(pub) + "}";
                    if (ApiPost("/local/public-url", json))
                        AppendLog("WanAttach: 已自动写入公网地址 " + pub);
                    else
                        AppendLog("WanAttach: 自动保存失败（接口没响应），地址请手工点保存");
                }
                return "公网地址已接入：" + pub;
            });
        }

        /// <summary>
        /// 让用户选备份目录，然后写进服务端配置。
        ///
        /// 为什么必须让用户能改：默认备份落在数据目录里（同一块盘），
        /// 盘坏的时候源和备份一起没。这是备份这个功能存在的全部意义的反面。
        /// </summary>
        private void PickBackupDir()
        {
            using (var dlg = new System.Windows.Forms.FolderBrowserDialog())
            {
                dlg.Description = "选择备份保存到哪个文件夹（建议选一块和数据盘不同的硬盘）";
                dlg.ShowNewFolderButton = true;
                var curSt = FetchBackupState();
                string cur = curSt != null && curSt.ContainsKey("dir") ? Str(curSt, "dir") : "";
                if (!string.IsNullOrEmpty(cur) && Directory.Exists(cur))
                    dlg.SelectedPath = cur;

                if (dlg.ShowDialog() != System.Windows.Forms.DialogResult.OK) return;
                string chosen = dlg.SelectedPath;

                // Quote() 自带首尾引号且已经转义反斜杠，不要再包一层。
                string json = "{\"dir\": " + Quote(chosen) + "}";
                if (!ApiPost("/local/backup/dir", json))
                {
                    SendToast("设置备份目录失败，看日志");
                    return;
                }
                AppendLog("备份目录已设置: " + chosen);
                SendToast("备份目录已改为 " + chosen);
                PushStateAsync(true);
            }
        }

        /// <summary>立即备份一次（后台跑，备份完刷新卡片）。</summary>
        private void RunBackupNow()
        {
            RunBusy("正在备份…", () =>
            {
                AppendLog("手动备份开始");
                // 备份可能要几秒到几十秒（取决于数据量），超时给足
                var resp = ApiPostJson("/local/backup/run", "{}", 300000);
                if (resp == null)
                {
                    AppendLog("手动备份：接口无响应");
                    return "备份失败：服务没有响应";
                }
                // 响应本身就是最新的完整状态，直接写回缓存，卡片立刻更新
                _backupCache = resp;
                _backupCacheAt = DateTime.Now;

                object lastRaw;
                if (resp.TryGetValue("last", out lastRaw))
                {
                    var last = lastRaw as Dictionary<string, object>;
                    if (last != null)
                    {
                        bool ok = false;
                        object okObj;
                        if (last.TryGetValue("ok", out okObj)) ok = okObj is bool && (bool)okObj;
                        string detail = last.ContainsKey("detail") ? Str(last, "detail") : "";
                        AppendLog("手动备份结果 ok=" + ok + " detail=" + detail);
                        return ok ? ("备份完成" + (detail.Length > 0 ? "：" + detail : ""))
                                  : ("备份失败：" + detail);
                    }
                }
                return "备份已执行";
            });
        }

        // 局域网可达性缓存。
        //
        // 🔴 为什么必须缓存（第一轮 review 自己抓到的）：
        // `BuildLocalPayload()` 跑在 **UI 线程**（Send 的那一帧），而
        // LanReachable() 现在要做真实 TCP 连接 —— 服务挂着时最坏要等
        // 1.2s（本地）+ 2.5s（局域网），足以让面板每 4 秒冻一次。
        // 这正是「GUI 卡死」的老病从另一个门又进来了
        // （AGENTS/备份卡片那段注释里记过一次，同一类错误）。
        //
        // 所以：UI 线程只读缓存；过期就让后台线程去探，探完下个刷新周期生效。
        // 需要「立刻要答案」的地方（切换按钮的轮询）走 LanReachableNow()，
        // 那个本来就在 Task.Run 里跑，不在 UI 线程。
        private int _lanReachState;          // 0=未知 1=可连 2=不可连
        private DateTime _lanReachAt = DateTime.MinValue;
        private int _lanReachBusy;           // 0=空闲 1=在探，防重入

        /// <summary>局域网能不能连到（UI 线程安全：只读缓存，后台刷新）。</summary>
        private bool? LanReachable()
        {
            if ((DateTime.Now - _lanReachAt).TotalSeconds >= 4
                && System.Threading.Interlocked.CompareExchange(ref _lanReachBusy, 1, 0) == 0)
            {
                Task.Run(delegate
                {
                    try { LanReachableNow(); }
                    catch (Exception) { }
                    finally { System.Threading.Interlocked.Exchange(ref _lanReachBusy, 0); }
                });
            }

            int st = System.Threading.Interlocked.CompareExchange(ref _lanReachState, 0, 0);
            if (st == 1) return true;
            if (st == 2) return false;
            return null;                     // 还没探出来
        }

        /// <summary>
        /// 同步探测并刷新缓存。**不要在 UI 线程调用**（会卡）。
        /// 切换按钮在 Task.Run 里轮询用这个，因为那时用户正在等结果。
        /// </summary>
        private bool? LanReachableNow()
        {
            bool? result = LanReachableProbe();
            System.Threading.Interlocked.Exchange(
                ref _lanReachState, result == null ? 0 : (result.Value ? 1 : 2));
            _lanReachAt = DateTime.Now;
            return result;
        }

        /// <summary>
        /// 局域网能不能连到。
        /// true=能连（开），false=连不上（关），null=服务本身没在跑。
        ///
        /// 🔴 判据演进（2026-11-02 两次实机）：
        ///
        /// 第一版只认「监听地址是 0.0.0.0 或 ::」。但 3.90 起站点块逐 IP 绑定，
        /// Caddy 给每个 IP 单独建 listener，永远不会出现通吃地址 —— 于是永远
        /// 判 false，界面报「10 秒内没切到局域网模式」。
        ///
        /// 第二版改成「有非回环地址在监听就算开」，逻辑对了，但**仍然依赖
        /// 端口枚举**。第三台机器上直接翻车：Caddy 明明在正常服务（healthz
        /// 每 5 秒 200、手机扫码能登录），可 GetActiveTcpListeners() 和
        /// netstat 都拿不到 8765 的 LISTENING 行 —— netstat 只给了一条
        ///   0.0.0.0:8765 → 0.0.0.0:0 CLOSED
        /// 于是托盘判「端口没监听」→ 界面显示「服务好像没在运行」、
        /// 二维码一闪就没。典型的「判断功能好没好」的部分坏了。
        ///
        /// 现在：服务活着时，**直接从局域网 IP 连一次 8765**。连得上就是开，
        /// 这个判据不依赖任何本机 socket 查询，也正是用户真正关心的问题
        /// （手机能不能连）。
        /// </summary>
        private bool? LanReachableProbe()
        {
            try
            {
                // ① 服务本身活着吗？连不上 = 服务没跑，交给上层显示「正在启动/未运行」
                if (!TcpConnects("127.0.0.1", _cfg.WebPort, 1200)) return null;

                // ② 有非回环地址在监听 → 直接就是开着（快路径，常见情况）
                foreach (var ep in System.Net.NetworkInformation.IPGlobalProperties
                             .GetIPGlobalProperties().GetActiveTcpListeners())
                {
                    if (ep.Port != _cfg.WebPort) continue;
                    if (!IsLoopbackAddress(ep.Address)) return true;
                }

                // ③ 枚举不到非回环监听 ≠ 没开（见方法注释）。
                //    直接连一次真实的局域网地址，这是最终判据。
                string ip = LocalLanIp();
                if (ip != null && TcpConnects(ip, _cfg.WebPort, 2500)) return true;

                // ④ 连不上 → 确实是关的（服务活着但只对本机开放）
                return false;
            }
            catch (Exception)
            {
                return null;
            }
        }

        /// <summary>能不能建立到 host:port 的 TCP 连接（带超时，不抛异常）。</summary>
        private static bool TcpConnects(string host, int port, int timeoutMs)
        {
            try
            {
                using (var tcp = new System.Net.Sockets.TcpClient())
                {
                    var ar = tcp.BeginConnect(host, port, null, null);
                    if (!ar.AsyncWaitHandle.WaitOne(timeoutMs)) return false;
                    tcp.EndConnect(ar);
                    return tcp.Connected;
                }
            }
            catch (Exception)
            {
                return false;
            }
        }

        /// <summary>回环地址判断（含 IPv4-mapped 的 ::ffff:127.0.0.1）。</summary>
        private static bool IsLoopbackAddress(System.Net.IPAddress addr)
        {
            if (addr == null) return false;
            if (System.Net.IPAddress.IsLoopback(addr)) return true;
            string s = addr.ToString();
            return s == "::ffff:127.0.0.1" || s == "::1";
        }

        /// <summary>把 CLI 的 status 字典整理成前端要的形状。</summary>
        private Dictionary<string, object> BuildPayload(Dictionary<string, object> d, string error)
        {
            var p = new Dictionary<string, object>
            {
                { "apiPort", _cfg.ApiPort },
                { "webPort", _cfg.WebPort },
                { "dataPath", _cfg.DataFolder },
                { "services", new List<object>() },
                { "healthy", false },
                { "lanOn", false },
                { "lanUrl", "" },
                { "lanHint", "" },
                { "wanUrl", "" },
                { "qr", null },
                { "certTrusted", true },
            };

            if (d == null)
            {
                // CLI 没回来（超时/服务名对不上），但网卡信息是本机就有的。
                // 地址照常显示，提示语说清楚状态没确认 —— 总比一片空白强。
                p["lanHint"] = (error ?? "读不到服务状态") + "（地址仍可尝试）";
                p["services"] = BuildServiceRows(null);
                string localIp = LocalLanIp();
                if (localIp != null)
                {
                    p["lanUrl"] = "https://" + localIp + ":" + _cfg.WebPort + "/";
                }
                _lastPayload = p;
                return p;
            }

            string state = Str(d, "state");
            bool lanOn = state == "on" || state == "blocked";
            p["lanOn"] = lanOn;
            p["lanBlocked"] = state == "blocked";
            p["noNetwork"] = state == "no_network";
            p["healthy"] = lanOn;

            // 局域网地址：3.72 起是 HTTPS
            string lanUrl = "";
            var addrs = List(d, "addresses");
            if (addrs.Count > 0)
            {
                var first = addrs[0] as Dictionary<string, object>;
                if (first != null)
                {
                    lanUrl = "https://" + Str(first, "ip") + ":" + Int(d, "web_port") + "/";
                }
            }
            // CLI 没给地址（比如它自己探活失败）时，用本机网卡兜一层。
            // 用户要的是「手机扫哪个地址」，这个答案不该依赖 CLI 是否顺利。
            if (lanUrl.Length == 0)
            {
                string localIp2 = LocalLanIp();
                if (localIp2 != null)
                    lanUrl = "https://" + localIp2 + ":" + _cfg.WebPort + "/";
            }
            p["lanUrl"] = lanOn ? lanUrl : "";

            // 证书信任状态：直接用严格校验去连一次。
            //
            // 这是「浏览器会不会报警告页」最直接的答案 —— 查证书库再比对
            // 指纹要绕一大圈（PEM 解析、store 遍历、Subject 匹配），而这个
            // 请求本身就是浏览器要做的事。TrustFailure 就是不受信。
            if (lanOn)
            {
                p["certTrusted"] = CheckCertTrusted(_cfg.WebPort);
            }


            if (lanOn)
            {
                p["lanHint"] = state == "blocked"
                    ? "已开启但自检失败，检查 Caddy 服务是否正常"
                    : "同一个 WiFi 下的手机、平板都能访问";
            }
            else if (state == "no_network")
            {
                p["lanHint"] = "这台电脑没有连接任何网络，插上网线或连上 WiFi 后再试";
            }
            else
            {
                p["lanHint"] = "现在只有这台电脑能访问。开启后同一个 WiFi 下的手机也能用";
            }

            // 服务行
            p["services"] = BuildServiceRows(d);

            // 二维码：只在局域网开着时才取，取不到就不显示（不阻断其它信息）
            if (lanOn && lanUrl.Length > 0)
            {
                // 缓存要和地址绑定：地址变了（换网卡）就重画
                if (_lastQrSrc != null && _lastQrUrl == lanUrl)
                {
                    p["qr"] = _lastQrSrc;
                }
                else
                {
                    try
                    {
                        string qrUrl = "http://127.0.0.1:" + _cfg.ApiPort
                                     + "/local/qr?format=png&text="
                                     + Uri.EscapeDataString(lanUrl);
                        // FetchQrPng 返回 Image，转成 data URI 给 <img src>
                        using (var img = _cli.FetchQrPng(qrUrl, 4000))
                        {
                            if (img != null)
                            {
                                using (var ms = new MemoryStream())
                                {
                                    img.Save(ms, System.Drawing.Imaging.ImageFormat.Png);
                                    _lastQrSrc = "data:image/png;base64,"
                                               + Convert.ToBase64String(ms.ToArray());
                                    _lastQrUrl = lanUrl;
                                    p["qr"] = _lastQrSrc;
                                }
                            }
                        }
                    }
                    catch (Exception) { /* 拿不到就不显示，别影响整体 */ }
                }
            }
            else
            {
                _lastQrSrc = null;
                _lastQrUrl = null;
            }

            // 公网地址：走本地 HTTP 端点。
            // CLI 的 `lan` 子命令只有 status/enable/disable/diagnose，
            // 根本没有 public-url —— 之前按 CLI 调，argparse 直接报错，
            // 界面永远显示「未配置」。
            try
            {
                p["wanUrl"] = ApiGet<string>("/local/public-url", d2 =>
                    Str(d2, "url"));
            }
            catch (Exception) { }

            _lastPayload = p;
            return p;
        }

        private List<object> BuildServiceRows(Dictionary<string, object> d)
        {
            var rows = new List<object>();
            var defs = new[]
            {
                new { Label = "API",    Name = _cfg.ApiServiceName, Port = _cfg.ApiPort },
                new { Label = "Caddy",  Name = _cfg.CaddyServiceName, Port = _cfg.WebPort },
                new { Label = "数据库", Name = _cfg.DbServiceName, Port = _cfg.PgPort },
            };
            foreach (var def in defs)
            {
                bool exists = ServiceControl.ServiceExists(def.Name);
                string status = "未安装";
                if (exists)
                {
                    try
                    {
                        using (var sc = new System.ServiceProcess.ServiceController(def.Name))
                            status = sc.Status.ToString();
                    }
                    catch (Exception) { status = "未知"; }
                }
                rows.Add(new Dictionary<string, object>
                {
                    { "label", def.Label },
                    { "exists", exists },
                    { "status", status },
                    { "detail", exists
                        ? ("127.0.0.1:" + def.Port + "  ·  " + status)
                        : "服务未安装" },
                });
            }
            return rows;
        }

        /// <summary>判断系统信不信这张证书（等价于「浏览器会不会报警告页」）。</summary>
        private static bool CheckCertTrusted(int webPort)
        {
            // 为什么不用 HttpWebRequest：
            //
            // LanShareTray 在启动时装了一个**全局**的
            // ServicePointManager.ServerCertificateValidationCallback，
            // 对 127.0.0.1 一律放行（本机自签是家用场景的常态，不放行就
            // 永远连不上）。那个回调会让这里永远拿到「可信」——
            // 实际测过：把证书从系统库里删掉，界面照样不提示。
            //
            // SslStream 是另一套 API，不吃 ServicePointManager 的回调。
            // 回调里只把 SslPolicyErrors 记下来、照样返回 true 完成握手，
            // 然后拿记下的错误码判断 —— 那个值和浏览器看到的完全一致。
            try
            {
                using (var tcp = new System.Net.Sockets.TcpClient())
                {
                    tcp.Connect("127.0.0.1", webPort);

                    System.Net.Security.SslPolicyErrors captured =
                        System.Net.Security.SslPolicyErrors.RemoteCertificateChainErrors;

                    // 回调必须真的把 sslPolicyErrors 记下来 —— 只是 return true
                    // 的话 captured 永远是初始值，检测等于没做（初版就是这么写的，
                    // 表现是无论证书在不在都报「不受信」）。
                    System.Net.Security.RemoteCertificateValidationCallback grab =
                        delegate(object snd, System.Security.Cryptography.X509Certificates.X509Certificate c,
                                 System.Security.Cryptography.X509Certificates.X509Chain ch,
                                 System.Net.Security.SslPolicyErrors err)
                        {
                            captured = err;
                            return true;     // 只为拿证书，不在这儿阻断
                        };

                    using (var ssl = new System.Net.Security.SslStream(
                               tcp.GetStream(), false, grab))
                    {
                        // 必须显式给协议版本。
                        //
                        // 不传的话 SslStream 走系统默认（老系统可能只有
                        // SSL3/TLS1.0），而 Caddy 只接 TLS1.2+ —— 服务端
                        // 直接断连，客户端报 "调用 SSPI 失败"。
                        // ServicePointManager.SecurityProtocol 是另一套
                        // API 的开关，管不到 SslStream，别指望它。
                        ssl.AuthenticateAsClient(
                            "localhost", null,
                            System.Security.Authentication.SslProtocols.Tls12, false);
                    }

                    return captured == System.Net.Security.SslPolicyErrors.None;
                }
            }
            catch (Exception ex)
            {
                // 检测失败按「不受信」处理（宁可多提示一次，也别让用户
                // 毫不知情地撞上浏览器的红色警告页），但要把原因记下来，
                // 否则排查时只能看到「就是false」。
                try
                {
                    File.AppendAllText(
                        Path.Combine(InstallConfig.Load().LogFolder, "tray.log"),
                        DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss")
                        + "  [WebPanel] CheckCertTrusted 异常: " + ex.GetType().Name
                        + " " + ex.Message + Environment.NewLine, Encoding.UTF8);
                }
                catch (Exception) { }
                return false;
            }
        }

        /// <summary>把本地 CA 装进系统受信任根证书库（需要管理员）。</summary>
        private void TrustCert()
        {
            string crt = Path.Combine(_cfg.InstallFolder, "certs", "caddy",
                                      "pki", "authorities", "local", "root.crt");
            if (!File.Exists(crt))
            {
                crt = Path.Combine(_cfg.InstallFolder, "certs", "cert.pem");
            }
            if (!File.Exists(crt))
            {
                SendToast("找不到本地证书文件");
                return;
            }

            try
            {
                var psi = new ProcessStartInfo
                {
                    FileName = "certutil.exe",
                    Arguments = "-addstore -f Root \"" + crt + "\"",
                    UseShellExecute = true,
                    Verb = "runas",          // 写 LocalMachine\Root 必须提权
                    WindowStyle = ProcessWindowStyle.Hidden,
                };
                using (var proc = Process.Start(psi))
                {
                    // 45 秒而不是 20：开局域网要改 Caddyfile + 重启 Caddy + 局域网自检，
                    // 慢一点的机器上 20 秒不够（实测超时就显示「命令超时（20 秒）」）。
                    proc.WaitForExit(45000);
                }
                SendToast("证书已装好，浏览器不会再报警告页");
                PushStateAsync(true);
            }
            catch (System.ComponentModel.Win32Exception)
            {
                // 用户在 UAC 框点了「否」
                SendToast("需要管理员权限才能修改系统证书库");
            }
            catch (Exception ex)
            {
                SendToast("装证书失败：" + ex.Message);
            }
        }

        private void AppendLog(string line)
        {
            try
            {
                File.AppendAllText(Path.Combine(_cfg.LogFolder, "tray.log"),
                    DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + "  " + line
                    + Environment.NewLine, Encoding.UTF8);
            }
            catch (Exception) { }
        }

        private void Send(string type, object payload)
        {
            if (_web == null || _web.CoreWebView2 == null) return;
            var msg = new Dictionary<string, object> { { "type", type }, { "payload", payload } };
            string json = new JavaScriptSerializer().Serialize(msg);
            _web.CoreWebView2.PostWebMessageAsJson(json);
        }

        private void SendToast(string text)
        {
            if (_web == null || _web.CoreWebView2 == null) return;
            var msg = new Dictionary<string, object> { { "type", "toast" }, { "text", text } };
            _web.CoreWebView2.PostWebMessageAsJson(new JavaScriptSerializer().Serialize(msg));
        }

        private void SendBusy(bool on, string text)
        {
            if (_web == null || _web.CoreWebView2 == null) return;
            var msg = new Dictionary<string, object>
            {
                { "type", "busy" }, { "on", on }, { "text", text ?? "" }
            };
            _web.CoreWebView2.PostWebMessageAsJson(new JavaScriptSerializer().Serialize(msg));
        }

        // ──────────────────────────────────────────────────────
        // 收动作
        // ──────────────────────────────────────────────────────

        private void OnWebMessage(object sender, CoreWebView2WebMessageReceivedEventArgs e)
        {
            Dictionary<string, object> msg;
            try
            {
                msg = new JavaScriptSerializer()
                    .Deserialize<Dictionary<string, object>>(e.WebMessageAsJson);
            }
            catch (Exception) { return; }
            if (msg == null) return;

            string action = msg.ContainsKey("action") ? Convert.ToString(msg["action"]) : "";
            switch (action)
            {
                case "ready":
                    _pageReady = true;
                    PushStateAsync(true);
                    // 更新地址不是状态的一部分（它是配置），单独推一次
                    PushUpdateSource();
                    break;

                case "copy":
                    CopyAddress(Convert.ToString(msg.ContainsKey("which") ? msg["which"] : "lan"));
                    break;

                case "restartService":
                    {
                        int idx = 0;
                        try
                        {
                            idx = msg.ContainsKey("index")
                                ? Convert.ToInt32(msg["index"]) : 0;
                        }
                        catch (Exception) { idx = 0; }
                        RestartOne(idx);
                    }
                    break;

                case "restartAll":
                    RunBusy("正在重启服务…", () =>
                    {
                        string err = ServiceControl.Restart(_cfg);
                        return err == null ? "服务已重启" : ("重启失败：" + err);
                    });
                    break;

                case "stopAll":
                    StopAll();
                    break;

                case "exportDiag":
                    ExportDiag();
                    break;
                case "setWanUrl":
                    SaveWanUrl(msg.ContainsKey("url") ? Str(msg, "url") : "");
                    break;

                case "wanAttach":
                    AttachWan(msg.ContainsKey("host") ? Str(msg, "host") : "");
                    break;
                case "pickBackupDir":
                    PickBackupDir();
                    break;
                case "runBackup":
                    RunBackupNow();
                    break;
                case "toggleLan":
                    ToggleLan(Convert.ToBoolean(msg.ContainsKey("on") ? msg["on"] : false));
                    break;

                case "openWeb":
                    OpenUrl(_cfg.WebUrl);
                    break;

                case "openLogs":
                    OpenFolder(_cfg.LogFolder);
                    break;

                case "wanHelp":
                    ShowWanHelp();
                    break;

                case "wanTest":
                    TestWan();
                    break;

                case "wanClear":
                    ClearWan();
                    break;

                case "trustCert":
                    TrustCert();
                    break;

                case "checkUpdate":
                    CheckUpdate();
                    break;

                case "downloadUpdate":
                    DownloadUpdate();
                    break;

                case "applyUpdate":
                    ApplyUpdate();
                    break;

                case "saveUpdateSource":
                    SaveUpdateSource(msg);
                    break;
            }
        }

        // ──────────────────────────────────────────────────────
        // 自定义更新地址（3.96）
        //
        // 存注册表 HKLM\SOFTWARE\LANShare 的 UpdateUrls（多行文本）。
        // 读写都走 CLI —— 只有一份实现，GUI 和命令行不会各存一套配置。
        // ──────────────────────────────────────────────────────

        /// <summary>把已保存的更新地址推给界面（面板打开、保存成功后各一次）。</summary>
        private void PushUpdateSource()
        {
            if (_web == null) return;

            Task.Run(() =>
            {
                string error;
                string json = RunCli("update-source --json", 20000, out error);
                return string.IsNullOrEmpty(json) ? "" : json;
            }).ContinueWith(t =>
            {
                if (IsDisposed || _web == null) return;

                var payload = new Dictionary<string, object>();
                var urls = new List<string>();
                try
                {
                    var ser = new JavaScriptSerializer { MaxJsonLength = int.MaxValue };
                    var d = ser.Deserialize<Dictionary<string, object>>(t.Result);
                    if (d != null && d.ContainsKey("urls") && d["urls"] != null)
                    {
                        foreach (var x in (System.Collections.IEnumerable)d["urls"])
                            urls.Add(Convert.ToString(x) ?? "");
                    }
                }
                catch (Exception ex)
                {
                    AppendLog("读取更新地址失败：" + ex.Message);
                }
                payload["urls"] = urls;
                Send("updateSource", payload);
            }, TaskScheduler.FromCurrentSynchronizationContext());
        }

        /// <summary>
        /// 给命令行参数加引号。更新地址里可能带 &amp; ? 空格这些 cmd 特殊字符，
        /// 不包起来会被 cmd 吃掉一半 —— 表现是「保存了但存进去的是前半段」。
        ///
        /// 刻意**不**转义反斜杠：网上流传的「\\ → \\\\」是给 C 风格解析器用的，
        /// cmd 不认，照着写会把 URL / 路径里的 \ 变成俩。这里只处理双引号。
        /// </summary>
        private static string QuoteArg(string s)
        {
            if (string.IsNullOrEmpty(s)) return "\"\"";
            var sb = new StringBuilder("\"");
            // 尾部反斜杠必须成对加倍（2026-11-04 审查）。
            //
            // Windows 命令行解析规则：引号前连续 N 个反斜杠，其中
            // N/2 对是字面反斜杠，剩下的用来转义引号。所以参数以反斜杠
            // 结尾时，如果不额外补一个，收尾那个引号会被转义掉 ——
            //   QuoteArg(@"https://a/b\")  →  "https://a/b\"
            //   CommandLineToArgvW 解析成：https://a/b" --clear   ← 后一个
            //   开关整个被并进同一个参数，静默丢失。
            // 用户把更新源粘成以 \ 结尾（UNC 路径、手滑）就会撞上，
            // 表现是「保存了但没生效」，很难联想到引号。
            int trailing = 0;
            for (int i = s.Length - 1; i >= 0 && s[i] == '\\'; i--) trailing++;
            for (int i = 0; i < s.Length - trailing; i++)
            {
                char c = s[i];
                if (c == '"') sb.Append("\\\"");
                else sb.Append(c);
            }
            sb.Append('\\', trailing * 2);   // 每个尾部反斜杠加倍
            sb.Append('"');
            return sb.ToString();
        }

        /// <summary>
        /// 保存自定义更新地址。传空列表 = 清除，恢复「只走 GitHub」。
        ///
        /// 命令行参数统一走 QuoteArg：地址里可能有 & ? 这些 cmd 特殊字符，
        /// 拼错一次就是「保存了但存进去的是前半段」。
        /// </summary>
        private void SaveUpdateSource(Dictionary<string, object> msg)
        {
            var urls = new List<string>();
            if (msg != null && msg.ContainsKey("urls") && msg["urls"] != null)
            {
                foreach (var x in (System.Collections.IEnumerable)msg["urls"])
                {
                    string s = (Convert.ToString(x) ?? "").Trim();
                    if (s.Length > 0) urls.Add(s);
                }
            }

            RunBusy("正在保存更新地址…", () =>
            {
                var sb = new StringBuilder("update-source --json");
                if (urls.Count == 0)
                {
                    sb.Append(" --clear");
                }
                else
                {
                    sb.Append(" --save");
                    foreach (var u in urls)
                        sb.Append(' ').Append(QuoteArg(u));
                }

                string error;
                string json = RunCli(sb.ToString(), 30000, out error);
                if (string.IsNullOrEmpty(json))
                    return "保存失败：" + (error ?? "命令没有输出");

                try
                {
                    var ser = new JavaScriptSerializer { MaxJsonLength = int.MaxValue };
                    var d = ser.Deserialize<Dictionary<string, object>>(json);
                    object ok;
                    if (d != null && d.TryGetValue("ok", out ok)
                        && ok is bool && !(bool)ok)
                    {
                        return "保存失败：" + Str(d, "error");
                    }
                }
                catch (Exception ex)
                {
                    return "保存失败：" + ex.Message;
                }

                AppendLog("更新地址已保存：" + urls.Count + " 条");
                // 立刻刷新界面上的列表（保存是后台线程，这里只是发起推送）
                PushUpdateSource();
                return urls.Count == 0
                    ? "已恢复默认：以后只从 GitHub 检查更新"
                    : "已保存 " + urls.Count + " 条更新地址（上面的优先）";
            });
        }

        // ──────────────────────────────────────────────────────
        // 检查更新
        //
        // 「只查不装」—— 自动替换正在跑的服务目录风险太大（exe 被占用、
        // 升级中途断电、装一半没回滚）。查到新版就把下载地址给用户，
        // 由安装器覆盖安装，它本来就会保留数据和注册表。
        // ──────────────────────────────────────────────────────

        /// <summary>上一次查到的新版下载地址（用户点「下载」时用）。</summary>
        private string _updateUrl;
        // 一键更新要用的两样东西：校验值（下完对不上就不许装）和版本号（拼文件名）
        private string _updateSha256;
        private string _updateVersion;
        private long _updateSize;

        private void CheckUpdate()
        {
            // 更新正跑到一半：这时候查到的「当前版本」还是旧的，一查就会
            // 说「有新版本」，于是用户再点一次、又装一遍（2026-11-04 实机
            // 装了两次就是这么来的）。直接告诉界面「在装」比给个错误的
            // 「有新版本」好得多。
            if (UpdateMarker.IsPending())
            {
                AppendLog("检查更新已跳档：有一个更新正在进行中");
                var busyP = new Dictionary<string, object>();
                busyP["installing"] = true;
                busyP["notes"] = "正在安装更新，请稍候…";
                Send("update", busyP);
                return;
            }

            Send("updateState", new Dictionary<string, object> { { "busy", true } });

            Task.Run(() =>
            {
                string error;
                string json = RunCli("check-update --json --timeout 10", 25000, out error);
                if (string.IsNullOrEmpty(json) && !string.IsNullOrEmpty(error))
                {
                    // 把真实的 CLI 错误带回去 —— 否则用户只看到「命令没有输出」，
                    // 而真正的原因（找不到 python / 超时 / 没这个子命令）全丢了
                    return "{\"error\": " + Quote(error) + "}";
                }
                return json;
            }).ContinueWith(t =>
            {
                if (IsDisposed || _web == null) return;

                var p = new Dictionary<string, object>();
                string json = t.Result;
                AppendLog("检查更新：CLI 返回 " +
                          (string.IsNullOrEmpty(json) ? "(空)" : json.Length + " 字符"));
                if (string.IsNullOrEmpty(json))
                {
                    p["error"] = "检查失败：命令没有输出";
                    Send("update", p);
                    return;
                }

                try
                {
                    var ser = new JavaScriptSerializer { MaxJsonLength = int.MaxValue };
                    var d = ser.Deserialize<Dictionary<string, object>>(json);
                    if (d == null)
                    {
                        p["error"] = "检查失败：返回内容不是 JSON";
                        Send("update", p);
                        return;
                    }
                    // CLI 用 error 字段区分「没查成」和「确实是最新」，
                    // 这两件事绝不能长同一张脸 —— App 端栽过一次。
                    p["current"] = Str(d, "current");
                    p["latest"] = Str(d, "latest");
                    object hu;
                    p["hasUpdate"] = d.TryGetValue("has_update", out hu)
                        && hu is bool && (bool)hu;
                    p["url"] = Str(d, "url");
                    p["notes"] = Str(d, "notes");
                    p["published"] = Str(d, "published");
                    p["error"] = Str(d, "error");
                    // stale = 这是缓存结果（GitHub 限流/断网时的降级），
                    // 前端要标出来，否则用户会以为刚查的是最新状态
                    object st;
                    p["stale"] = d.TryGetValue("stale", out st)
                        && st is bool && (bool)st;
                    p["staleHours"] = Int(d, "stale_hours");
                    // 更新源相关（3.96）：结果是谁给的、地址是什么、
                    // 自定义源为什么没成 —— 回落必须在界面上说清楚，
                    // 否则用户以为「我的源里没有新版」。
                    p["source"] = Str(d, "source");
                    p["sourceUrl"] = Str(d, "source_url");
                    p["sourceError"] = Str(d, "source_error");
                    object hist;
                    p["history"] = d.TryGetValue("history", out hist)
                        ? hist : new List<object>();
                    _updateUrl = Str(d, "url");
                    _updateSha256 = Str(d, "sha256");
                    _updateVersion = Str(d, "latest");
                    object sz;
                    _updateSize = d.TryGetValue("size", out sz) ? Convert.ToInt64(sz) : 0L;
                }
                catch (Exception ex)
                {
                    p["error"] = "检查失败：" + ex.Message;
                }
                Send("update", p);
            }, TaskScheduler.FromCurrentSynchronizationContext());
        }

        /// <summary>
        /// 导出诊断包并打开所在文件夹。
        ///
        /// 用户出故障时不用再教他开 PowerShell 跑哪几条命令 —— 点一下，
        /// 得到一个能直接发出来的 txt。2026-11-02 定位 PG 起不来时，
        /// 就是这么来回问了三轮。
        /// </summary>
        private void ExportDiag()
        {
            RunBusy("正在收集诊断信息…", () =>
            {
                string err;
                string json = RunCli("diagnose --json", 90000, out err);
                if (string.IsNullOrEmpty(json))
                    return "诊断包生成失败：" + (err ?? "命令没有输出");

                string path = "";
                try
                {
                    var d = new JavaScriptSerializer()
                        .Deserialize<Dictionary<string, object>>(json);
                    if (d != null && d.ContainsKey("path"))
                        path = Str(d, "path");
                    if (d != null && d.ContainsKey("ok"))
                    {
                        object ok;
                        if (d.TryGetValue("ok", out ok) && ok is bool && !(bool)ok)
                            return "诊断包生成失败：" + Str(d, "error");
                    }
                }
                catch (Exception ex)
                {
                    return "诊断包解析失败：" + ex.Message;
                }
                if (string.IsNullOrEmpty(path) || !File.Exists(path))
                    return "诊断包生成失败：文件没落地";

                AppendLog("诊断包已导出: " + path);
                try
                {
                    // /select 打开资源管理器并选中该文件，用户直接拖进聊天窗口
                    System.Diagnostics.Process.Start("explorer.exe", "/select,\"" + path + "\"");
                }
                catch (Exception) { /* 打不开文件夹不算失败，路径已在 toast 里 */ }
                return "诊断包已生成并打开文件夹：" + path;
            });
        }

        /// <summary>
        /// 一键更新（3.99）：下载 → 校验 sha256 → 静默安装 → 自己退出。
        ///
        /// 为什么托盘必须退出：安装器要覆盖 tray\LANShareTray.exe 和
        /// runtime\*.dll，而这些文件正被我们占着。与其让 Inno 去请求关闭
        /// （会弹一堆「请关闭以下程序」），不如主动退干净，让安装器一路
        /// 覆盖到底。装完 iss 的 [Run] 段会把托盘重新拉起来。
        ///
        /// 用户看到的就是：点一下 → 托盘消失几秒 → 新版本自己回来。
        /// </summary>
        private void ApplyUpdate()
        {
            if (string.IsNullOrEmpty(_updateUrl))
            {
                SendToast("还没有查到更新");
                return;
            }
            // 地址来自更新源清单，理论上可信，但「理论上」不该是唯一防线
            if (!_updateUrl.StartsWith("https://", StringComparison.OrdinalIgnoreCase))
            {
                SendToast("更新地址必须是 https");
                return;
            }
            // fail closed：没有校验值就不许一键更新。
            //
            // 这条链路本质是「下载一个 exe 然后静默安装」，而托盘是提权进程。
            // 之前 GitHub 源固定给空 sha256，于是唯一的默认来源永远跳过校验 ——
            // 代码里写着「sha256 必须对得上」，实际一次都没对过。
            // 现在 GitHub 的 assets[].digest 会被取出来；只有老 Release 没有，
            // 那种情况下让用户走「打开发布页」手动下载，不在这里静默放行。
            if (string.IsNullOrEmpty(_updateSha256))
            {
                SendToast("这个版本没有校验值，已停止一键更新。请点「打开发布页」手动下载安装。");
                return;
            }

            SendBusy(true, "正在下载更新…");

            // 进后台线程前把要用的字段拷成局部变量。
            //
            // _updateUrl / _updateSha256 / _updateVersion 由 UI 线程的
            // CheckUpdate 随时改写，而下载要跑几十秒。中间用户再点一次
            // 「检查更新」（或定时刷新）就会出现：下载的是 A 版本的包、
            // 校验用的是 B 版本的 sha256 → 必然报「校验失败，已丢弃」，
            // 而文件其实是好的。查这种偶发问题极其费劲。
            string wantUrl = _updateUrl;
            string wantSha = _updateSha256;
            string wantVer = _updateVersion;

            // 版本号来自更新源清单，不能直接拼进文件路径。
            //
            // 托盘是提权进程，Path.Combine 遇到 "..\..\" 会真的穿出临时目录
            // —— 也就是「更新源里写一个带路径的版本号，就能把任意 exe 落到
            // 系统目录并执行」。App 端早就做了同样的清洗（safeVer），
            // 这边一直漏着。
            string ver = string.IsNullOrEmpty(wantVer) ? "new" : wantVer;
            ver = System.Text.RegularExpressions.Regex.Replace(ver, @"[^0-9A-Za-z._-]", "_");
            string dir = Path.Combine(Path.GetTempPath(), "LANShareUpdate");
            string file = Path.Combine(dir, "LANShare-Setup-" + ver + ".exe");

            Task.Run(() =>
            {
                // 返回 null = 成功，否则是给人看的错误
                try
                {
                    Directory.CreateDirectory(dir);
                    if (File.Exists(file)) File.Delete(file);

                    using (var wc = new System.Net.WebClient())
                    {
                        // 自签证书：这台机器上装过我们的根证书，系统信任库认它；
                        // WebClient 走 schannel，读的就是系统信任库。
                        wc.DownloadFile(new Uri(wantUrl), file);
                    }

                    var info = new FileInfo(file);
                    if (info.Length == 0)
                        return "下载失败：文件是空的";

                    // sha256 必须对得上。更新链路上「差不多就行」等于装一个
                    // 来路不明的 exe —— 这个校验是最后一公里。
                    if (!string.IsNullOrEmpty(wantSha))
                    {
                        string actual;
                        using (var sha = System.Security.Cryptography.SHA256.Create())
                        using (var fs = File.OpenRead(file))
                        {
                            var hash = sha.ComputeHash(fs);
                            var sb = new StringBuilder(hash.Length * 2);
                            foreach (byte b in hash) sb.Append(b.ToString("x2"));
                            actual = sb.ToString();
                        }
                        if (!string.Equals(actual, wantSha.Trim(),
                                           StringComparison.OrdinalIgnoreCase))
                        {
                            try { File.Delete(file); } catch (Exception) { }
                            return "下载的安装包校验失败（sha256 不一致），已丢弃";
                        }
                    }
                    AppendLog("更新包已下载并校验通过: " + file + " (" + info.Length + " 字节)");
                    return null;
                }
                catch (Exception ex)
                {
                    AppendLog("下载更新失败: " + ex.Message);
                    return "下载失败：" + ex.Message;
                }
            }).ContinueWith(t =>
            {
                string err = (t.Status == TaskStatus.RanToCompletion) ? t.Result
                                                                     : "下载任务异常";
                if (err != null)
                {
                    SendBusy(false, null);
                    SendToast(err);
                    return;
                }

                SendBusy(false, null);

                // 先把「我要去更新了」写下来，再启动安装器。
                //
                // 托盘这一退要 40 秒才回来（安装器静默跑），期间屏幕上没有任何
                // 东西。没有这个标记，托盘下次启动根本不知道自己是被更新关掉的，
                // 用户看到的就是「点了没反应、然后图标自己回来了」——
                // 2026-11-04 实机反馈原话：「直接就程序退出了，然后没有任何动静」。
                UpdateMarker.Write(wantVer);

                try
                {
                    // /VERYSILENT：全静默；/NORESTART：不重启机器。
                    // 安装器自己会停服务、覆盖、再起服务（iss 的 ssInstall /
                    // ssPostInstall 两段），这里不需要额外动作。
                    Process.Start(new ProcessStartInfo(file)
                    {
                        Arguments = "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART",
                        UseShellExecute = true
                    });
                    AppendLog("更新安装器已启动，托盘即将退出");
                }
                catch (Exception ex)
                {
                    UpdateMarker.Take();          // 起不来就没有「进行中」这回事了
                    SendToast("启动安装程序失败：" + ex.Message);
                    return;
                }

                // 明确告诉用户接下来会发生什么：托盘会消失，这是正常的。
                SendToast("正在安装 " + (string.IsNullOrEmpty(wantVer) ? "新版本" : "v" + wantVer)
                          + "，约 40 秒。托盘会自动回来，不用管它。");

                // 3 秒：900 毫秒时提示还没被看见窗口就没了，等于没提示
                var timer = new Timer { Interval = 3000 };
                timer.Tick += delegate
                {
                    timer.Stop();
                    timer.Dispose();
                    Application.Exit();
                };
                timer.Start();
            }, TaskScheduler.FromCurrentSynchronizationContext());
        }

        private void DownloadUpdate()
        {
            if (string.IsNullOrEmpty(_updateUrl)) return;
            try
            {
                // 只放行 https —— 这个地址来自 GitHub API 响应，理论上可信，
                // 但「理论上」不该成为 Process.Start 的唯一防线
                if (!_updateUrl.StartsWith("https://", StringComparison.OrdinalIgnoreCase))
                    return;
                Process.Start(new ProcessStartInfo(_updateUrl) { UseShellExecute = true });
            }
            catch (Exception ex)
            {
                AppendLog("打开下载页失败：" + ex.Message);
            }
        }

        /// <summary>
        /// 把一个字符串转成 JSON 字符串字面量（含引号）。
        /// C# 5 没有现成的，JavaScriptSerializer 也能做，但为了一个字段
        /// new 一个序列化器不值当。
        /// </summary>
        private static string Quote(string s)
        {
            if (s == null) return "null";
            var sb = new StringBuilder("\"");
            foreach (char c in s)
            {
                switch (c)
                {
                    case '"': sb.Append("\\\""); break;
                    case '\\': sb.Append("\\\\"); break;
                    case '\n': sb.Append("\\n"); break;
                    case '\r': sb.Append("\\r"); break;
                    case '\t': sb.Append("\\t"); break;
                    default:
                        if (c < ' ') sb.Append("\\u").Append(((int)c).ToString("x4"));
                        else sb.Append(c);
                        break;
                }
            }
            sb.Append("\"");
            return sb.ToString();
        }

        /// <summary>
        /// 跑任意 lan-share CLI 子命令，返回 stdout。
        /// 和 RunLan 的区别：那个固定拼 `lan`，这个是给别的子命令用的。
        /// </summary>
        private string RunCli(string args, int timeoutMs, out string error)
        {
            error = null;
            string python = _cfg.PythonPath;
            if (string.IsNullOrEmpty(python) || !File.Exists(python))
            {
                error = "找不到 Python 运行时：" + (python ?? "(空)");
                AppendLog("RunCli 失败：" + error);
                return null;
            }

            var psi = new ProcessStartInfo
            {
                FileName = python,
                Arguments = "-m lanshare_cli " + args,
                WorkingDirectory = _cfg.InstallFolder,
                UseShellExecute = false,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                CreateNoWindow = true,
                StandardOutputEncoding = Encoding.UTF8,
                StandardErrorEncoding = Encoding.UTF8,
            };
            // 必须显式告诉 Python 用 UTF-8 输出。
            //
            // 中文 Windows 上 sys.stdout 默认是 GBK，而上面两行按 UTF-8 解码
            // —— 字节对不上，界面上的中文全成乱码。2026-11-01 用户反馈
            // 「检查更新显示的字体是乱码」就是这个。
            //
            // 光靠上面两行不行：那是「我按什么解码」，不是「你按什么编码」。
            // PYTHONIOENCODING 才是给子进程下的指令。
            psi.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
            psi.EnvironmentVariables["PYTHONUTF8"] = "1";

            try
            {
                using (var proc = Process.Start(psi))
                {
                    var outTask = proc.StandardOutput.ReadToEndAsync();
                    var errTask = proc.StandardError.ReadToEndAsync();
                    if (!proc.WaitForExit(timeoutMs))
                    {
                        try { proc.Kill(); } catch { }
                        error = "命令超时";
                        return null;
                    }
                    string stdout = outTask.Result;
                    string stderr = errTask.Result;
                    if (string.IsNullOrWhiteSpace(stdout))
                    {
                        error = string.IsNullOrWhiteSpace(stderr) ? "没有输出" : stderr.Trim();
                        AppendLog("RunCli 空输出（exit " + proc.ExitCode + "）："
                                  + python + " -m lanshare_cli " + args
                                  + " | " + (error.Length > 300 ? error.Substring(0, 300) : error));
                        return null;
                    }
                    // 成功也留一条精简痕：出问题时能看出「命令跑过了、
                    // 退出码多少、回了多长」，而不是什么都没有。
                    if (stderr.Trim().Length > 0)
                    {
                        string se = stderr.Trim();
                        AppendLog("RunCli stderr（exit " + proc.ExitCode + "，"
                                  + args + "）："
                                  + (se.Length > 400 ? se.Substring(0, 400) : se));
                    }
                    return stdout.Trim();
                }
            }
            catch (Exception ex)
            {
                error = ex.Message;
                AppendLog("RunCli 抛异常：" + ex.Message);
                return null;
            }
        }

        // ──────────────────────────────────────────────────────
        // 动作实现
        // ──────────────────────────────────────────────────────

        private void CopyAddress(string which)
        {
            try
            {
                string text = "";
                if (which == "wan")
                {
                    text = ApiGet<string>("/local/public-url", d2 => Str(d2, "url"));
                }
                if (which == "backup")
                {
                    var bkc = FetchBackupState();
                    text = bkc != null && bkc.ContainsKey("dir") ? Str(bkc, "dir") : null;
                }
                if (which == "lan")
                {
                    // 用本机网卡算，不再调 CLI —— 复制地址是点一下就期待有反应的
                    // 操作，等 Python 冷启动好几秒太离谱。
                    string ip = LocalLanIp();
                    if (ip != null)
                        text = "https://" + ip + ":" + _cfg.WebPort + "/";
                }
                if (string.IsNullOrEmpty(text))
                {
                    SendToast("没有可复制的地址");
                    return;
                }
                Clipboard.SetText(text);
                SendToast("地址已复制");
            }
            catch (Exception ex) { SendToast("复制失败：" + ex.Message); }
        }

        private void RestartOne(int index)
        {
            string[] names = { _cfg.ApiServiceName, _cfg.CaddyServiceName, _cfg.DbServiceName };
            string[] labels = { "API", "Caddy", "数据库" };
            if (index < 0 || index >= names.Length) return;
            string name = names[index], label = labels[index];

            RunBusy("正在重启 " + label + "…", () =>
            {
                string err = ServiceControl.RestartOnePublic(name);
                return err == null ? (label + " 已重启") : (label + "：" + err);
            });
        }

        private void StopAll()
        {
            var NL = Environment.NewLine;
            var r = MessageBox.Show(
                "确定要关闭网盘服务吗？" + NL + NL +
                "• 三个服务会停止，局域网和公网都访问不了" + NL +
                "• 数据不会丢，服务只是停了" + NL +
                "• 重新打开托盘或点「重启服务」就能恢复",
                "关闭服务", MessageBoxButtons.OKCancel, MessageBoxIcon.Warning);
            if (r != DialogResult.OK) return;

            RunBusy("正在关闭服务…", () =>
            {
                string err = ServiceControl.StopAll(_cfg);
                return err == null ? "服务已关闭" : ("部分服务停不掉：" + err);
            });
        }

        private void ToggleLan(bool turnOn)
        {
            RunBusy(turnOn ? "正在开启局域网访问…" : "正在关闭局域网访问…", () =>
            {
                string err;
                // 子命令只认 enable / disable（写 lan-on 会 argparse 报错）
                string sub = turnOn ? "enable" : "disable";
                AppendLog("ToggleLan: " + sub + " 开始");
                var ret = _cli.RunLan(sub, 30000, out err);

                // 🔴 把 CLI 的真实返回记下来。
                //
                // 2026-11-02 用户报「局域网启动失败」，而 tray.log 里只有
                // 「CLI 返回 err=(null)」—— 完全看不出 CLI 是成功了没生效、
                // 还是根本没返回数据。诊断信息不足本身就是缺陷。
                string dump;
                try
                {
                    dump = ret == null
                        ? "null"
                        : new JavaScriptSerializer { MaxJsonLength = int.MaxValue }
                            .Serialize(ret);
                }
                catch (Exception ex) { dump = "<序列化失败：" + ex.Message + ">"; }
                if (dump.Length > 1200) dump = dump.Substring(0, 1200) + "…(截断)";
                AppendLog("ToggleLan: CLI 返回 err=" + (err ?? "(null)") + " data=" + dump);

                // 🔴 不能只看 err。
                //
                // CLI 返回 0 只代表「命令跑完了」，Caddy 重启要几秒，端口监听
                // 状态这期间没变。用户点完按钮瞬间看到的还是旧状态，就以为
                // 「点了没效果」。2026-11-02 笔记本实测反馈的正是这个。
                //
                // 这里等状态真的切过来（最多 10 秒），并把结果如实说出来。
                bool wantAll = turnOn;

                // 先看一次现状。CLI 有时判断「已经是目标状态」就直接返回，
                // 这时候本来就不用等 —— 否则用户白等满 10 秒，而且这 10 秒里
                // 整个面板是禁用的（_busy 挡着），体感就是卡死。
                // 这里在 RunBusy 的 Task.Run 里，不在 UI 线程 —— 可以同步探
                if (LanReachableNow() == wantAll)
                {
                    AppendLog("ToggleLan: 已经是目标状态，无需等待");
                    return turnOn ? "局域网已开启" : "局域网已关闭";
                }

                // 切换动作刚做完，缓存里还是动作前的值 —— 标记过期，
                // 让轮询第一轮就触发后台重探
                _lanReachAt = DateTime.MinValue;
                bool switched = false;
                for (int i = 0; i < 20; i++)
                {
                    // 🔴 这里必须读缓存（LanReachable），不能用 LanReachableNow。
                    //
                    // Now 每次最坏 1.2s(本地) + 2.5s(局域网超时) = 3.7 秒，
                    // 20 次轮询就是 74 秒 —— 开启失败时用户要对着冻结的面板
                    // 等一分多钟（_busy 期间什么都不刷）。第一版就是这么写的，
                    // 第二轮 review 抓到的。
                    //
                    // 读缓存：每 4 秒后台刷一次，10 秒窗口里能刷到 2 次，
                    // 判断切换是否完成完全够用。
                    if (LanReachable() == wantAll) { switched = true; break; }
                    System.Threading.Thread.Sleep(500);
                }
                AppendLog("ToggleLan: 监听状态切换 switched=" + switched);

                if (!switched)
                {
                    return err != null
                        ? ("操作失败：" + err)
                        : (turnOn
                            ? "命令执行了，但服务 10 秒内没切到局域网模式，点「重启服务」再试"
                            : "命令执行了，但服务 10 秒内没切回仅本机模式");
                }
                return turnOn ? "局域网已开启" : "局域网已关闭";
            });
        }

        /// <summary>跑一个后台动作，期间遮罩，结束后 toast + 刷新。</summary>
        private DateTime _busySince = DateTime.MinValue;

        // 世代号：强制放弃一个卡住的操作后，旧任务的 ContinueWith 仍然会跑完。
        // 如果它傻乎乎地去清 _busy，就会把**新**任务的状态一起清掉 —— 表现为
        // 界面提前解禁、按钮可以重复点。所以每个任务记下自己是第几代，
        // 收尾时只在「我还是最新一代」的情况下才动共享状态。
        private int _busySeq = 0;

        private void RunBusy(string hint, Func<string> work)
        {
            // 🔴 这里原来只有一句 `if (_busy) return;`。
            //
            // 后果：上一个操作一旦因为异常/极慢没走完，_busy 永远是 true，
            // 面板上所有按钮从此**静默失效** —— 点下去毫无反应，也没有任何
            // 提示。2026-11-02 用户报「单独点服务重启，GUI 界面没反应，
            // 貌似也没正确执行重启」，就是这个。
            //
            // 现在：超过 2 分钟强制复位（自愈），没超时就明确告诉用户
            // 「上一个操作还在跑」，而不是装作没听见。
            if (_busy && (DateTime.Now - _busySince).TotalSeconds > 120)
            {
                AppendLog("上一个操作超过 120 秒未结束，强制放弃并放行新的操作"
                          + "（旧任务的进程可能仍在跑，它的结果会被丢弃）");
                _busy = false;
            }
            if (_busy)
            {
                SendToast("上一个操作还在进行中，请等它结束");
                return;
            }
            _busy = true;
            _busySince = DateTime.Now;
            int mySeq = ++_busySeq;
            SendBusy(true, hint);

            Task.Run(() =>
            {
                try { return work(); }
                catch (Exception ex) { return "出错：" + ex.Message; }
            }).ContinueWith(t =>
            {
                // 只有最新一代才有权改共享状态。被强制放弃的旧任务跑完时
                // _busySeq 已经变了，它只留一条日志，不碰任何界面。
                bool isCurrent = (mySeq == _busySeq);
                try
                {
                    if (!isCurrent)
                    {
                        AppendLog("RunBusy 旧任务结束（已被放弃），结果丢弃");
                        return;
                    }
                    string msg = (t.Status == TaskStatus.RanToCompletion && t.Result != null)
                        ? t.Result
                        : "操作已结束（没有返回信息）";
                    SendToast(msg);
                    PushStateAsync(true);
                }
                catch (Exception ex)
                {
                    AppendLog("RunBusy 收尾失败：" + ex.Message);
                }
                finally
                {
                    if (isCurrent)
                    {
                        // 放开 —— 这是「所有按钮突然失灵」的唯一开关
                        _busy = false;
                        SendBusy(false, null);
                    }
                }
            }, TaskScheduler.FromCurrentSynchronizationContext());
        }

        private void ShowWanHelp()
        {
            var NL = Environment.NewLine;
            MessageBox.Show(
                "公网访问 = 内网穿透：把一个公网地址映射到这台电脑的 8765 端口。" + NL + NL +
                "【以樱花 FRP 为例】" + NL +
                "1. 注册 sakurafrp.com，下载客户端并登录" + NL +
                "2. 建隧道：" + NL +
                "     · 类型：TCP" + NL +
                "     · 本地地址：127.0.0.1" + NL +
                "     · 本地端口：8765（就是本机 Web 端口）" + NL +
                "     · 远程端口：随便挑一个没被占的，比如 35123" + NL +
                "3. 启动隧道，记下节点域名，拼起来形如：" + NL +
                "     cn-bj.frp.one:35123" + NL + NL +
                "【回到这个面板】" + NL +
                "4. 把上面那串粘进「隧道域名」，点「一键接入」" + NL +
                "     —— 程序会写配置、重签证书、自检，成功后自动填好公网地址" + NL +
                "5. 用手机流量（别连家里 WiFi）打开那个地址验证" + NL + NL +
                "⚠ 为什么不能只填地址：" + NL +
                "   本机证书只对 localhost / 局域网 IP 签发。陌生域名直接填进去，" + NL +
                "   客户端在 TLS 握手阶段就被拒，连错误页都看不到。" + NL +
                "   所以必须先用「一键接入」让本机认这个域名。" + NL + NL +
                "⚠ 自签证书：公网地址会提示不受信任，家庭自建属正常。" + NL +
                "   手机 App 第一次连接会弹「信任此服务器」，核对后点信任即可。" + NL + NL +
                "⚠ 换了节点/换了隧道 → 域名变了，要重新接入一次。",
                "怎么配置公网访问", MessageBoxButtons.OK, MessageBoxIcon.Information);
        }

        private void TestWan()
        {
            RunBusy("正在验证…", () =>
            {
                string url = ApiGet<string>("/local/public-url", d2 => Str(d2, "url"));
                if (string.IsNullOrEmpty(url)) return "还没有配置公网地址";
                if (!url.StartsWith("http")) url = "https://" + url;
                if (ProbeOk(url)) return "公网连通正常";

                // 失败要分类。用户配好地址后最常撞的是「隧道类型建错」，
                // 这时候说「检查穿透工具是否在运行」完全帮不上忙 ——
                // 工具明明在跑，只是那条隧道不接受我们的自签证书。
                Uri u;
                try { u = new Uri(url); } catch (Exception) { return "公网不通（地址解析失败）"; }
                return "公网不通：" + DiagnoseWanFailure(u.Host, u.Port);
            });
        }

        /// <summary>
        /// 明文探一下公网端口对面是谁，用来区分几种失败。
        ///
        /// 判据来自实机：隧道建成 HTTPS/HTTP 类型时，由穿透服务端接管 TLS，
        /// 它拿不到我们的自签证书 → 握手直接断；此时发明文 HTTP，回的是
        /// 「501 Not Implemented + Server: SakuraFrp」，一眼就能认出来。
        /// 而 TCP 类型隧道会把 TLS 原样透传到本机 Caddy，握手正常。
        /// </summary>
        private static string DiagnoseWanFailure(string host, int port)
        {
            try
            {
                using (var c = new System.Net.Sockets.TcpClient())
                {
                    var ar = c.BeginConnect(host, port, null, null);
                    if (!ar.AsyncWaitHandle.WaitOne(6000))
                        return "TCP 都连不上，检查隧道是否在运行";
                    c.EndConnect(ar);
                    using (var s = c.GetStream())
                    {
                        s.ReadTimeout = 6000;
                        var req = Encoding.ASCII.GetBytes(
                            "GET /healthz HTTP/1.1\r\nHost: " + host
                            + "\r\nConnection: close\r\n\r\n");
                        s.Write(req, 0, req.Length);
                        var buf = new byte[512];
                        int n = s.Read(buf, 0, buf.Length);
                        string head = Encoding.ASCII.GetString(buf, 0, n);
                        string server = "";
                        foreach (var line in head.Split('\n'))
                        {
                            if (line.ToLower().StartsWith("server:"))
                                server = line.Substring(7).Trim();
                        }
                        if (server.Length > 0)
                        {
                            string low = server.ToLower();
                            if (low.Contains("frp") || low.Contains("sakura"))
                                return "穿透服务端直接应答了（" + server + "），"
                                     + "说明这条隧道建成了 HTTPS/HTTP 类型。"
                                     + "到穿透面板改成 TCP 类型（本地地址 127.0.0.1，"
                                     + "本地端口填 Web 端口）后再验证。";
                            return "对面是 " + server + "，不是本机的 Web 服务，检查转发目标";
                        }
                        return "明文探测也没拿到响应，检查隧道本地端口是否指向 Web 端口";
                    }
                }
            }
            catch (Exception)
            {
                return "TCP 连不上，检查隧道是否在运行";
            }
        }

        private void ClearWan()
        {
            RunBusy("正在清除…", () =>
            {
                ApiPost("/local/public-url", "{\"url\": \"\"}");
                return "公网地址已清除";
            });
        }

        /// <summary>GET 一个本地 API 端点并解析 JSON。失败返回 fallback。</summary>
        private T ApiGet<T>(string path, Func<Dictionary<string, object>, T> pick,
                            T fallback = default(T))
        {
            try
            {
                string url = "http://127.0.0.1:" + _cfg.ApiPort + path;
                var req = (System.Net.HttpWebRequest)System.Net.WebRequest.Create(url);
                req.Timeout = 5000;
                req.Proxy = null;
                req.UserAgent = "LanShareTray";
                using (var resp = (System.Net.HttpWebResponse)req.GetResponse())
                using (var sr = new StreamReader(resp.GetResponseStream(), Encoding.UTF8))
                {
                    var dict = new JavaScriptSerializer()
                        .Deserialize<Dictionary<string, object>>(sr.ReadToEnd());
                    return dict == null ? fallback : pick(dict);
                }
            }
            catch (Exception) { return fallback; }
        }

        /// <summary>POST JSON 到本地 API 端点。返回是否成功。</summary>
        private bool ApiPost(string path, string json)
        {
            try
            {
                string url = "http://127.0.0.1:" + _cfg.ApiPort + path;
                var req = (System.Net.HttpWebRequest)System.Net.WebRequest.Create(url);
                req.Method = "POST";
                req.ContentType = "application/json";
                req.Timeout = 6000;
                req.Proxy = null;
                byte[] body = Encoding.UTF8.GetBytes(json);
                req.ContentLength = body.Length;
                using (var st = req.GetRequestStream()) st.Write(body, 0, body.Length);
                using (var resp = (System.Net.HttpWebResponse)req.GetResponse())
                    return (int)resp.StatusCode < 400;
            }
            catch (Exception) { return false; }
        }

        /// <summary>
        /// POST 并返回解析后的响应体。失败返回 null。
        ///
        /// 备份要用它：`/local/backup/run` 的响应里带着这一轮的真实结果，
        /// 只看 HTTP 状态码判断不了「备份到底成没成」。
        /// </summary>
        private Dictionary<string, object> ApiPostJson(string path, string json, int timeoutMs)
        {
            try
            {
                string url = "http://127.0.0.1:" + _cfg.ApiPort + path;
                var req = (System.Net.HttpWebRequest)System.Net.WebRequest.Create(url);
                req.Method = "POST";
                req.ContentType = "application/json";
                req.Timeout = timeoutMs;
                req.Proxy = null;
                byte[] body = Encoding.UTF8.GetBytes(json);
                req.ContentLength = body.Length;
                using (var st = req.GetRequestStream()) st.Write(body, 0, body.Length);
                using (var resp = (System.Net.HttpWebResponse)req.GetResponse())
                using (var sr = new StreamReader(resp.GetResponseStream(), Encoding.UTF8))
                {
                    return new JavaScriptSerializer()
                        .Deserialize<Dictionary<string, object>>(sr.ReadToEnd());
                }
            }
            catch (Exception)
            {
                return null;
            }
        }

        private static bool ProbeOk(string url)
        {
            try
            {
                var req = (System.Net.HttpWebRequest)System.Net.WebRequest.Create(url);
                // 3 秒足够：本机回环 + 服务在就该毫秒级返回。原来 8 秒是为了
                // 容忍 TLS 握手慢，但代价是服务没起来时一整轮探测要 16 秒，
                // 和 5 秒的 Timer 叠加成死循环（见 Probe 的防重叠注释）。
                req.Timeout = 3000;
                req.Proxy = null;
                req.ServerCertificateValidationCallback = (a, b, c, d) => true;
                using (var resp = (System.Net.HttpWebResponse)req.GetResponse())
                    return (int)resp.StatusCode < 500;
            }
            catch (System.Net.WebException wex)
            {
                // 4xx 也说明链路通（服务在，只是拒绝了路径）
                return wex.Response != null;
            }
            catch (Exception) { return false; }
        }

        private void OpenUrl(string url)
        {
            var u = (url ?? "").Trim();
            if (!u.StartsWith("http://", StringComparison.OrdinalIgnoreCase) &&
                !u.StartsWith("https://", StringComparison.OrdinalIgnoreCase))
            {
                MessageBox.Show(this, "地址格式不对：" + u, "LAN Share",
                    MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return;
            }
            try { System.Diagnostics.Process.Start(u); }
            catch (Exception ex) { SendToast("打不开浏览器：" + ex.Message); }
        }

        private void OpenFolder(string path)
        {
            try
            {
                var p = (path ?? "").Trim();
                if (p.Length == 0) return;
                if (!Directory.Exists(p)) Directory.CreateDirectory(p);
                System.Diagnostics.Process.Start("explorer.exe", "\"" + p.Replace("\"", "") + "\"");
            }
            catch (Exception ex) { SendToast("打不开目录：" + ex.Message); }
        }

        // ──────────────────────────────────────────────────────
        // 小工具（和旧面板同一套，保持行为一致）
        // ──────────────────────────────────────────────────────

        private static string SafeGet(Func<string> f, string fallback)
        {
            try { return f() ?? fallback; } catch (Exception) { return fallback; }
        }

        private static string Str(Dictionary<string, object> d, string k)
        {
            if (d == null || !d.ContainsKey(k) || d[k] == null) return "";
            return Convert.ToString(d[k]);
        }

        private static int Int(Dictionary<string, object> d, string k)
        {
            if (d == null || !d.ContainsKey(k) || d[k] == null) return 0;
            try { return Convert.ToInt32(d[k]); } catch (Exception) { return 0; }
        }

        private static List<object> List(Dictionary<string, object> d, string k)
        {
            if (d == null || !d.ContainsKey(k)) return new List<object>();
            var v = d[k] as System.Collections.ArrayList;
            if (v != null) return new List<object>(v.ToArray());
            var l = d[k] as List<object>;
            return l ?? new List<object>();
        }
    }
}
