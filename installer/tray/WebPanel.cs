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
            // 正在跑后台动作（开局域网/检查更新）时不要再叠一个 CLI 进程。
            // 老代码 `_busy && !force` 在 force=true 时会放行 —— 而开局域网
            // 结束那一秒正好会 force 刷一次，于是两个 Python 同时跑，
            // 用户感觉界面像冻住了。
            if (_busy) return;
            if (_stateInFlight) return;     // 防重入：上一次还没回来就别再发
            if (!force && (DateTime.Now - _lastPush).TotalSeconds < 10) return;
            _lastPush = DateTime.Now;
            _stateInFlight = true;

            // 先把「本地就能算」的部分推出去。
            // 服务状态查 SCM 是毫秒级，没理由陪 CLI 冷启动一起等好几秒 ——
            // 老代码一进来就 RunLan，界面在结果回来前全是「正在检测中」，
            // 这就是「每次打开 GUI 都卡、服务都显示正在检测」的根因。
            Send("state", BuildLocalPayload());

            Task.Run(() =>
            {
                string error;
                // 60 秒：lan status 内部要做防火墙检查 + HTTPS 自检，慢机器上十几秒
                // 很正常。反正它在后台跑，不挡首帧显示。
                var dict = _cli.RunLan("status", 60000, out error);
                return BuildPayload(dict, error);
            }).ContinueWith(t =>
            {
                // try/finally 保命：RunLan 抛异常时 t.Result 会重新抛出，
                // 如果不用 finally 兜住，_stateInFlight 永远是 true，
                // 界面从此再也不刷新 —— 表现为「点了按钮没反应」。
                try
                {
                    if (t.IsFaulted || t.IsCanceled)
                    {
                        AppendLog("状态刷新失败：" + (t.Exception == null
                            ? "已取消" : t.Exception.GetBaseException().Message));
                        return;
                    }
                    if (IsDisposed || _web == null) return;
                    Send("state", t.Result);
                }
                finally
                {
                    _stateInFlight = false;
                }
            }, TaskScheduler.FromCurrentSynchronizationContext());
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
            bool? listeningAll = LanListeningOnAll();
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
                    if (string.IsNullOrEmpty(_lastQrSrc))
                        _lastQrSrc = FetchQrFor(url);
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
                p["lanHint"] = "服务好像没在运行，稍后重试或点「重启服务」";
            }

            // 有上一次的完整结果就沿用它，避免第一帧把地址/二维码闪没
            if (_lastPayload != null)
            {
                foreach (var key in new[]
                {
                    "healthy", "lanOn", "lanUrl", "lanHint", "wanUrl", "qr",
                    "certTrusted", "lanBlocked", "noNetwork",
                })
                {
                    if (_lastPayload.ContainsKey(key)) p[key] = _lastPayload[key];
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
        /// 局域网开关状态 —— 直接看 8765 端口监听在哪。
        ///
        /// 这是 CLI `lan status` 里唯一的权威判据（它自己也这么判断：
        /// listens_on_loopback_only → 关，listens_on_all_interfaces → 开），
        /// 只是它顺带要做防火墙检查、HTTPS 自检，慢的时候要十几秒。
        /// GUI 每次刷新都跑一遍那个，必然超时。
        ///
        /// 返回值：true = 所有网卡都在听（局域网开着）
        ///         false = 只有环回在听（只本机）
        ///         null = 端口根本没监听（服务没起来）
        /// </summary>
        private bool? LanListeningOnAll()
        {
            try
            {
                var listeners = System.Net.NetworkInformation.IPGlobalProperties
                    .GetIPGlobalProperties()
                    .GetActiveTcpListeners();
                bool any = false;
                bool all = false;
                foreach (var ep in listeners)
                {
                    if (ep.Port != _cfg.WebPort) continue;
                    any = true;
                    if (ep.Address.Equals(System.Net.IPAddress.Any) ||
                        ep.Address.Equals(System.Net.IPAddress.IPv6Any) ||
                        ep.Address.Equals(System.Net.IPAddress.IPv6Loopback) ||
                        ep.Address.ToString() == "::")
                    {
                        all = true;
                    }
                }
                if (!any) return null;
                // Caddy 在 Windows 上通常监听 ::（IPv6 通吃），这等价于所有网卡
                return all;
            }
            catch (Exception)
            {
                return null;
            }
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
                if (_lastQrSrc != null)
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
                    break;

                case "copy":
                    CopyAddress(Convert.ToString(msg.ContainsKey("which") ? msg["which"] : "lan"));
                    break;

                case "restartService":
                    RestartOne(Convert.ToInt32(msg.ContainsKey("index") ? msg["index"] : 0));
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
            }
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

        private void CheckUpdate()
        {
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
                    _updateUrl = Str(d, "url");
                }
                catch (Exception ex)
                {
                    p["error"] = "检查失败：" + ex.Message;
                }
                Send("update", p);
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
                if (which == "lan")
                {
                    string err;
                    var d = _cli.RunLan("status", 8000, out err);
                    var addrs = List(d, "addresses");
                    if (addrs.Count > 0)
                    {
                        var first = addrs[0] as Dictionary<string, object>;
                        if (first != null)
                            text = "https://" + Str(first, "ip") + ":" + Int(d, "web_port") + "/";
                    }
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
                _cli.RunLan(turnOn ? "enable" : "disable", 30000, out err);
                return err == null
                    ? (turnOn ? "局域网已开启" : "局域网已关闭")
                    : ("操作失败：" + err);
            });
        }

        /// <summary>跑一个后台动作，期间遮罩，结束后 toast + 刷新。</summary>
        private void RunBusy(string hint, Func<string> work)
        {
            if (_busy) return;
            _busy = true;
            SendBusy(true, hint);

            Task.Run(() =>
            {
                try { return work(); }
                catch (Exception ex) { return "出错：" + ex.Message; }
            }).ContinueWith(t =>
            {
                _busy = false;
                SendBusy(false, null);
                SendToast(t.Result);
                PushStateAsync(true);
            }, TaskScheduler.FromCurrentSynchronizationContext());
        }

        private void ShowWanHelp()
        {
            var NL = Environment.NewLine;
            MessageBox.Show(
                "公网访问需要一个内网穿透工具（比如 SakuraFrp、frp、花生壳）。" + NL + NL +
                "步骤：" + NL +
                "1. 在穿透工具里建一条隧道，指向本机 8765 端口" + NL +
                "2. 工具会给你一个公网地址，形如 xxx.frp-any.com:35391" + NL +
                "3. 把那个地址填进来，点「验证连通性」" + NL + NL +
                "注意：程序没法替你建隧道 —— 那需要登录你自己的穿透服务账号。",
                "怎么配置公网访问", MessageBoxButtons.OK, MessageBoxIcon.Information);
        }

        private void TestWan()
        {
            RunBusy("正在验证…", () =>
            {
                string url = ApiGet<string>("/local/public-url", d2 => Str(d2, "url"));
                if (string.IsNullOrEmpty(url)) return "还没有配置公网地址";
                if (!url.StartsWith("http")) url = "https://" + url;
                bool ok = ProbeOk(url);
                return ok ? "连通正常" : "连不上，检查穿透工具是否在运行";
            });
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

        private static bool ProbeOk(string url)
        {
            try
            {
                var req = (System.Net.HttpWebRequest)System.Net.WebRequest.Create(url);
                req.Timeout = 8000;
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
