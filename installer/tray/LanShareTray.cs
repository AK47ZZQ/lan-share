// LAN Share 托盘程序 —— C# WinForms，.NET Framework 4.8
//
// 编译（不需要 Visual Studio，Win10 自带编译器）：
//   %WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe ^
//     /target:winexe /out:LanShareTray.exe ^
//     /reference:System.Windows.Forms.dll /reference:System.Drawing.dll ^
//     LanShareTray.cs
//
// 为什么用 .NET Framework 4.8 而不是 .NET 8 self-contained：
//   4.8 是 Win10 1809+ 的内置组件，托盘 exe 只有 ~80KB。
//   .NET 8 self-contained 要多带 60MB 运行时，为了一个托盘不值。
//
// 为什么状态用 HTTP 探针而不是查 Windows 服务状态：
//   服务状态 RUNNING 不等于应用可用。2026-10-30 实测见过服务显示 PAUSED、
//   但其实端口根本没监听的情况。只有真的请求 /healthz 才算数。
//
// 退出托盘 ≠ 停止服务。服务是独立的 Windows 服务，开机自启，不依赖托盘。

using System;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.Net;
using System.Runtime.InteropServices;
using System.Windows.Forms;

namespace LanShare
{
    /// <summary>
    /// 「验证连通性」期间临时信任的 HTTPS 主机。
    ///
    /// 为什么需要：公网入口走 FRP 隧道，证书必然是自签的（隧道结构下
    /// 没法做 Let's Encrypt 的域名验证）。严格校验会让验证按钮永远报
    /// TrustFailure，用户以为配错了。
    ///
    /// 为什么用白名单而不是全局放行：只有在用户主动点「验证」的那一刻、
    /// 对那一个主机短时间放行。其余请求（包括将来可能加的其他网络功能）
    /// 仍然走系统默认校验，不会因为装了托盘就整体变松。
    /// </summary>
    internal static class TrustedHosts
    {
        private static readonly object _gate = new object();
        private static readonly System.Collections.Generic.HashSet<string> _set =
            new System.Collections.Generic.HashSet<string>(StringComparer.OrdinalIgnoreCase);

        public static void Add(string host, int seconds)
        {
            if (string.IsNullOrEmpty(host)) return;
            lock (_gate) { _set.Add(host); }
            // 到点自动摘掉，防止忘记清理留下永久后门
            var t = new System.Threading.Timer(_ =>
            {
                lock (_gate) { _set.Remove(host); }
            }, null, seconds * 1000, System.Threading.Timeout.Infinite);
        }

        public static bool IsTrusted(string host)
        {
            lock (_gate) { return _set.Contains(host); }
        }
    }

    internal static class Program
    {
        [System.Runtime.InteropServices.DllImport("user32.dll")]
        private static extern bool SetProcessDPIAware();

        // Win10 1703+：Per-Monitor V2。比 SystemAware 强的地方是
        // 窗口跑到不同 DPI 的显示器上不会被系统按比例位图缩放。
        [System.Runtime.InteropServices.DllImport("user32.dll")]
        private static extern bool SetProcessDpiAwarenessContext(IntPtr value);

        [System.Runtime.InteropServices.DllImport("user32.dll")]
        private static extern uint GetDpiForSystem();

        // shcore 版：Win8.1+ 就有，比 user32 的 Context 版兼容面宽。
        // 2 = PROCESS_PER_MONITOR_DPI_AWARE
        [System.Runtime.InteropServices.DllImport("shcore.dll")]
        private static extern int SetProcessDpiAwareness(int value);

        // 查当前进程到底处在哪一档：0=unaware 1=system 2=permonitor
        [System.Runtime.InteropServices.DllImport("shcore.dll")]
        private static extern int GetProcessDpiAwareness(IntPtr process, out int value);

        [STAThread]
        private static void Main(string[] args)
        {
            // --panel：启动时直接打开控制面板（调试用，也方便做桌面快捷方式）
            bool openPanelOnStart = false;
            foreach (var a in args)
            {
                if (string.Equals(a, "--panel", StringComparison.OrdinalIgnoreCase))
                    openPanelOnStart = true;
            }

            // 单实例：重复双击 exe 不要开出两个托盘图标
            bool createdNew;
            using (var mutex = new System.Threading.Mutex(
                    true, @"Global\LanShareTraySingleInstance", out createdNew))
            {
                if (!createdNew)
                {
                    // 已经在跑：不弹消息框（用户只是想看窗口），
                    // 通过命名事件通知已有实例把面板显示出来
                    try
                    {
                        using (var ev = System.Threading.EventWaitHandle.OpenExisting(
                                   @"Global\LanShareShowPanel"))
                        {
                            ev.Set();
                        }
                    }
                    catch (Exception)
                    {
                        MessageBox.Show(
                            "LAN Share 托盘已经在运行了。\n\n" +
                            "看屏幕右下角的通知区域。",
                            "LAN Share",
                            MessageBoxButtons.OK,
                            MessageBoxIcon.Information);
                    }
                    return;
                }

                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);

                // DPI 感知。必须在创建任何窗口之前调用。
                //
                // 坑：原来只调 SetProcessDPIAware()（= SystemAware），
                // 在混合 DPI 环境下不够用 —— 进程按主显示器 96 DPI 渲染，
                // 窗口跑到 144 DPI 的屏幕上会被系统按 96/144 位图缩小，
                // 结果窗口物理尺寸只有设计的 67.5%，文字发虚、Label 被裁掉半行。
                //
                // Per-Monitor V2 让窗口自己按所在显示器的 DPI 渲染，不再被缩放。
                // 老系统（Win7/8）没有这个 API，回退到 SystemAware。
                // DPI 感知三层兜底，每层结果都记下来。
                //
                // 历史教训：只靠 SetProcessDpiAwarenessContext 时，同一份 exe
                // 有时按 144 DPI 渲染、有时停在 96 DPI 被系统位图拉伸 1.5 倍，
                // 字全糊 —— 这个 API 要求在本进程创建任何 UI 之前调用，时机敏感。
                int awareBefore = -1;
                try { GetProcessDpiAwareness(IntPtr.Zero, out awareBefore); } catch (Exception) { }

                bool l1 = false, l2 = false, l3 = false;
                int rc2 = -99;
                try { l1 = SetProcessDpiAwarenessContext(new IntPtr(-4)); } catch (Exception) { }
                if (!l1) { try { rc2 = SetProcessDpiAwareness(2); l2 = rc2 == 0; } catch (Exception) { } }
                if (!l1 && !l2) { try { l3 = SetProcessDPIAware(); } catch (Exception) { } }

                int awareAfter = -1;
                try { GetProcessDpiAwareness(IntPtr.Zero, out awareAfter); } catch (Exception) { }


                // ⚠️ .NET Framework 默认只启用 TLS 1.0，而现代服务器（Caddy 等）
                // 最低要求 TLS 1.2。不显式开启的话，HTTPS 探测一律报
                // SecureChannelFailure —— 看起来像"证书不对"，其实是协议版本太低。
                System.Net.ServicePointManager.SecurityProtocol =
                    System.Net.SecurityProtocolType.Tls12
                    | System.Net.SecurityProtocolType.Tls11
                    | (System.Net.SecurityProtocolType)3072;   // Tls13（枚举里没有，用数值）

                // 自签证书全局放行 —— 只对回环地址生效。
                //
                // 为什么必须用 ServicePointManager 而不是 HttpWebRequest
                // 的实例属性：后者在 .NET Framework 下对某些 TLS 路径不生效，
                // 表现成 HTTPS 探测永远报证书错误（而 curl 是通的）。
                //
                // 安全性：这里只连 127.0.0.1 / ::1，是本机自己的服务；
                // 而且回调里再判断一次目标地址，非回环一律走正常校验。
                System.Net.ServicePointManager.ServerCertificateValidationCallback =
                    delegate(object sender, System.Security.Cryptography.X509Certificates.X509Certificate cert,
                             System.Security.Cryptography.X509Certificates.X509Chain chain,
                             System.Net.Security.SslPolicyErrors errors)
                    {
                        try
                        {
                            var req = sender as System.Net.HttpWebRequest;
                            if (req != null && req.RequestUri != null)
                            {
                                string host = req.RequestUri.Host;
                                if (host == "127.0.0.1" || host == "::1" || host == "localhost")
                                    return true;   // 本机自签，放行

                                // 公网地址验证：用户配的 FRP 入口几乎必然是自签证书
                                // （隧道结构下签不出 Let's Encrypt），严格校验等于
                                // 永远验证失败。所以「验证连通性」期间临时信任目标主机。
                                // 用白名单而不是无条件放行：平时的其他 HTTPS 请求仍走严格校验。
                                if (TrustedHosts.IsTrusted(host))
                                    return true;
                            }
                        }
                        catch (Exception) { }
                        return errors == System.Net.Security.SslPolicyErrors.None;
                    };

                // ⚠️ 必须在 Application.Run 之前把 SynchronizationContext 装好。
                //
                // TrayApp 构造函数里要抓 SynchronizationContext.Current 来回调
                // ShowPanel()，但 WinForms 是在 Application.Run 内部才装上下文的 ——
                // 构造函数里抓到的永远是 null，于是「双击托盘唤出面板」这条路过不去，
                // 事件发出了、面板却纹丝不动，日志里也什么都没有（踩过）。
                System.Threading.SynchronizationContext.SetSynchronizationContext(
                    new System.Windows.Forms.WindowsFormsSynchronizationContext());

                Application.Run(new TrayApp(openPanelOnStart));
            }
        }
    }

    /// <summary>应用状态。托盘图标颜色跟着它变。</summary>
    internal enum AppState
    {
        Unknown,   // 刚启动还没探过
        Healthy,   // /healthz 返回 200
        Unhealthy, // 端口通但 healthz 不正常
        Down       // 连不上
    }

    internal sealed class TrayApp : ApplicationContext
    {
        private readonly InstallConfig _cfg;
        private readonly NotifyIcon _icon;
        private readonly Timer _timer;
        private readonly ToolStripMenuItem _statusItem;
        private AppState _state = AppState.Unknown;
        private int _failCount;
        private Icon _currentIcon;
        private bool _firstProbeDone;
        private readonly string _logPath;
        private Form _panel;                   // 控制面板窗口，懒创建（Web 版或 WinForms 版）
        private bool _forceWinFormsPanel;      // WebView2 失败过一次就别再试了
        private readonly System.Threading.EventWaitHandle _showPanelEvent;
        private readonly CliRunner _cli;

        /// <summary>写一行日志。托盘出问题时用户在「打开日志文件夹」里能看到。</summary>
        private void Log(string message)
        {
            if (string.IsNullOrEmpty(_logPath)) return;
            try
            {
                // 单文件超 1MB 就重开，避免无限增长
                var fi = new FileInfo(_logPath);
                if (fi.Exists && fi.Length > 1024 * 1024)
                {
                    var bak = _logPath + ".1";
                    if (File.Exists(bak)) File.Delete(bak);
                    File.Move(_logPath, bak);
                }
                File.AppendAllText(_logPath,
                    DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + "  " + message + "\r\n");
            }
            catch (Exception) { /* 日志写不了不能影响托盘本身 */ }
        }

        public TrayApp(bool openPanelOnStart = false)
        {
            _cfg = InstallConfig.Load();

            // 命名事件：第二个实例通过它通知已有实例弹面板。
            // 比"再开一个托盘图标"或"弹框说已经在跑"都更符合直觉 ——
            // 用户双击快捷方式就是想看窗口。
            _showPanelEvent = new System.Threading.EventWaitHandle(
                false, System.Threading.EventResetMode.AutoReset, @"Global\LanShareShowPanel");

            // 后台线程不能碰 UI。NotifyIcon 不是 Control，没有 BeginInvoke，
            // 所以先抓主线程的 SynchronizationContext，回调时 Post 回去。
            var uiContext = System.Threading.SynchronizationContext.Current;
            var listenThread = new System.Threading.Thread(() =>
            {
                while (true)
                {
                    try
                    {
                        _showPanelEvent.WaitOne();
                        if (uiContext != null)
                        {
                            uiContext.Post(delegate { ShowPanel(); }, null);
                        }
                    }
                    catch (Exception) { break; }
                }
            })
            { IsBackground = true, Name = "PanelEvent" };
            listenThread.Start();

            // 日志跟服务日志放一起，用户点「打开日志文件夹」能同时看到
            _logPath = string.IsNullOrEmpty(_cfg.LogFolder)
                ? null
                : Path.Combine(_cfg.LogFolder, "tray.log");

            _cli = new CliRunner(_cfg.InstallFolder, _cfg.PythonPath, _cfg.ServicePrefix, _cfg.ApiPort);

            Log("托盘启动 | Installed=" + _cfg.Installed
                + " Prefix=" + _cfg.ServicePrefix
                + " WebPort=" + _cfg.WebPort
                + " ApiPort=" + _cfg.ApiPort
                + " Health=" + _cfg.HealthUrl);

            // 服务是独立的 Windows 服务，不会因为托盘起来就自动启动。
            // 用户点快捷方式（或开机自启托盘）时如果服务是停的，这里直接
            // 帮他拉起来 —— 否则打开界面看到「服务未运行」，还得自己去
            // 服务管理器里点启动，太反直觉。
            //
            // 放到后台线程：启动 PG 可能要几秒，不能卡住托盘图标出现。
            if (_cfg.Installed)
            {
                var bootThread = new System.Threading.Thread(() =>
                {
                    try
                    {
                        int n = ServiceControl.EnsureRunning(_cfg);
                        if (n > 0) Log("托盘启动时自动拉起了 " + n + " 个服务");
                    }
                    catch (Exception ex) { Log("自动拉起服务失败: " + ex.Message); }
                })
                { IsBackground = true, Name = "ServiceBoot" };
                bootThread.Start();
            }

            _statusItem = new ToolStripMenuItem("正在检查状态…") { Enabled = false };

            var menu = new ContextMenuStrip();

            var panelItem = new ToolStripMenuItem("打开控制面板", null, (s, e) => ShowPanel())
            {
                Font = new Font(SystemFonts.MenuFont, FontStyle.Bold)
            };
            menu.Items.Add(panelItem);
            menu.Items.Add(new ToolStripMenuItem("打开网盘", null, (s, e) => OpenBrowser()));
            menu.Items.Add(new ToolStripSeparator());
            menu.Items.Add(_statusItem);
            menu.Items.Add(new ToolStripSeparator());
            menu.Items.Add(new ToolStripMenuItem("复制局域网地址", null, (s, e) => CopyLanAddress()));
            menu.Items.Add(new ToolStripMenuItem("打开数据文件夹", null, (s, e) => OpenFolder(_cfg.DataFolder)));
            menu.Items.Add(new ToolStripMenuItem("打开日志文件夹", null, (s, e) => OpenFolder(_cfg.LogFolder)));
            menu.Items.Add(new ToolStripSeparator());
            menu.Items.Add(new ToolStripMenuItem("重启服务", null, (s, e) => RestartServices()));
            menu.Items.Add(new ToolStripMenuItem("重新检查状态", null, (s, e) => { _failCount = 0; Probe(); }));
            menu.Items.Add(new ToolStripSeparator());
            menu.Items.Add(new ToolStripMenuItem("退出托盘（服务继续运行）", null, (s, e) => ExitTray()));

            _icon = new NotifyIcon
            {
                ContextMenuStrip = menu,
                Visible = true,
                Text = "LAN Share"
            };
            _icon.MouseClick += (s, e) =>
            {
                if (e.Button == MouseButtons.Left) ShowPanel();
            };

            SetState(AppState.Unknown);

            _timer = new Timer { Interval = 5000 };  // 基础间隔 5 秒（失败时退避，见 Probe）
            _timer.Tick += (s, e) => Probe();
            _timer.Start();

            Probe();

            // --panel 启动参数：等托盘初始化完再弹，避免窗口先于图标出现
            if (openPanelOnStart)
            {
                var t = new Timer { Interval = 400 };
                t.Tick += (s, e) => { t.Stop(); t.Dispose(); ShowPanel(); };
                t.Start();
            }

            HandlePostUpdate();
        }

        /// <summary>
        /// 如果这次启动是「一键更新」的结果，把结果说出来。
        ///
        /// 一键更新时托盘会自杀让出文件句柄，安装器静默跑约 40 秒，然后安装器
        /// 再把自己拉起来 —— 新的托盘进程对这段经历一无所知。用户看到的现象就是
        /// 图标消失、过一会儿图标回来，中间的 40 秒完全是个黑洞（2026-11-04 实机：
        /// 「直接就程序退出了，然后没有任何动静」）。
        ///
        /// 所以发起更新的那一侧会留一个标记文件，这里读它、复述结果：
        /// 版本对上了就报成功并直接把面板打开（用户立刻看到新版界面），
        /// 对不上就说清楚还在旧版本、可以再点一次或去看日志。
        /// </summary>
        private void HandlePostUpdate()
        {
            string[] mark = UpdateMarker.Take();
            if (mark == null || mark.Length == 0) return;

            string want = (mark.Length > 0 && mark[0] != null) ? mark[0].Trim() : "";
            string now = CurrentVersion();
            bool ok = want.Length > 0 && now == want;

            Log("更新后启动：期望 " + (want.Length > 0 ? want : "(未知)")
                + " 实际 " + (now.Length > 0 ? now : "(读不到)") + (ok ? " → 成功" : " → 未生效"));

            // 延到消息循环起来之后再弹：构造期间 ShowPanel 会得到一个没有
            // 消息泵的窗口，看起来就是「点了没反应」。
            var t = new Timer { Interval = 1500 };
            t.Tick += delegate
            {
                t.Stop();
                t.Dispose();
                try
                {
                    if (ok)
                    {
                        _icon.ShowBalloonTip(6000, "已更新到 v" + now,
                            "LAN Share 升级完成，正在打开控制面板。", ToolTipIcon.Info);
                        ShowPanel();
                    }
                    else
                    {
                        // 还在装（安装器比托盘慢）就别急着报失败：
                        // 把标记写回去，让这期间的「检查更新」保持沉默，
                        // 免得又提示有新版本、用户再点一次、装第二遍。
                        UpdateMarker.Write(want);
                        _icon.ShowBalloonTip(8000, "更新没有生效",
                            "目标 v" + (want.Length > 0 ? want : "?")
                            + "，当前仍是 " + (now.Length > 0 ? "v" + now : "未知")
                            + "。可以再点一次「立即更新」，或打开日志看原因。",
                            ToolTipIcon.Warning);
                    }
                }
                catch (Exception ex) { Log("更新后处理失败: " + ex.Message); }
            };
            t.Start();
        }

        /// <summary>读安装目录里的 VERSION 文件，读不到返回空串。</summary>
        private string CurrentVersion()
        {
            try
            {
                if (string.IsNullOrEmpty(_cfg.InstallFolder)) return "";
                string path = Path.Combine(_cfg.InstallFolder, "VERSION");
                if (!File.Exists(path)) return "";
                string[] lines = File.ReadAllLines(path);
                return lines.Length > 0 ? lines[0].Trim() : "";
            }
            catch (Exception)
            {
                return "";
            }
        }

        // ── 状态探测 ──────────────────────────────────────────────

        // 🔴 防重叠。
        //
        // ProbeOk 的超时是 8 秒（TLS 握手 + 服务真的没起来时要耗满），失败分支
        // 还会再探一次 HTTP，一轮最坏 16 秒。而 Timer 每 5 秒触发一次 ——
        // 服务一挂，每 5 秒就新起一轮、旧的一轮还没结束，请求无限叠加。
        // 用户看到的就是「关闭服务后整个界面卡死」。2026-11-02 实机踩到。
        //
        // 现在：上一轮没跑完就跳过本次 Tick，并且失败时把间隔拉长（退避），
        // 恢复正常再收回 5 秒。
        private volatile bool _probing;

        private void Probe()
        {
            if (_probing) return;
            _probing = true;
            try
            {
                ProbeCore();
            }
            finally
            {
                _probing = false;
                RescheduleProbe();
            }
        }

        /// <summary>失败退避：5s → 10s → 20s，上限 30s；健康时回到 5s。</summary>
        private void RescheduleProbe()
        {
            int want;
            if (_state == AppState.Healthy) want = 5000;
            else if (_failCount <= 1) want = 10000;
            else if (_failCount <= 3) want = 20000;
            else want = 30000;

            if (_timer != null && _timer.Interval != want)
                _timer.Interval = want;
        }

        private void ProbeCore()
        {
            string result = null;
            var state = AppState.Down;

            // 协议自适应，但**先 HTTPS**。
            //
            // 3.72 起新装一律是 HTTPS（Caddy tls internal），老部署可能是纯 HTTP。
            // 之前的顺序是先 HTTP 再 HTTPS：新装环境下 HTTP 那一步必然要等满
            // 4 秒超时，然后才切 HTTPS 成功 —— 每 5 秒一次的轮询白烧 4 秒，
            // 用户点开界面看到的就是「转半天才显示状态」。
            // 反过来之后，新版一次命中，老部署才多付一次超时。
            state = TryProbe(_cfg.HealthUrl, out result);
            Log("探测 HTTPS " + _cfg.HealthUrl + " → " + state + " | " + result);

            if (state != AppState.Healthy)
            {
                // 老部署可能是纯 HTTP 站点，只换协议不换主机名
                string httpUrl = "http://localhost:" + _cfg.WebPort + "/healthz";
                string httpResult;
                var httpState = TryProbe(httpUrl, out httpResult);
                Log("探测 HTTP（老部署兼容）" + httpUrl + " → " + httpState + " | " + httpResult);

                if (httpState == AppState.Healthy)
                {
                    state = httpState;
                    result = httpResult;
                }
                else if (httpState == AppState.Unhealthy && state != AppState.Unhealthy)
                {
                    state = httpState;
                    result = httpResult;
                }
            }

            _failCount = state == AppState.Healthy ? 0 : _failCount + 1;
            _firstProbeDone = true;

            // 只在状态变化或连续失败 6 次（30 秒）时记日志，避免刷屏
            if (state != _state || (state != AppState.Healthy && _failCount % 6 == 0))
            {
                Log("状态 " + _state + " → " + state
                    + (string.IsNullOrEmpty(result) ? "" : " | " + result)
                    + " | 连续失败 " + _failCount + " 次");
            }

            SetState(state, result);
        }

        /// <summary>探一次 /healthz。自签证书不校验（否则永远失败）。</summary>
        private static AppState TryProbe(string url, out string detail)
        {
            detail = null;
            try
            {
                var req = (HttpWebRequest)WebRequest.Create(url);
                req.Timeout = 2500;
                req.ReadWriteTimeout = 2500;
                req.Proxy = null;          // 本机地址不要走系统代理
                req.UserAgent = "LanShareTray";

                // 自签证书：家用 HTTPS 全是这样，不跳过校验就永远连不上
                if (url.StartsWith("https://", StringComparison.OrdinalIgnoreCase))
                {
                    req.ServerCertificateValidationCallback = (s, cert, chain, errors) => true;
                }

                using (var resp = (HttpWebResponse)req.GetResponse())
                {
                    using (var reader = new StreamReader(resp.GetResponseStream()))
                    {
                        detail = reader.ReadToEnd();
                    }
                    return (int)resp.StatusCode == 200 ? AppState.Healthy : AppState.Unhealthy;
                }
            }
            catch (WebException wex)
            {
                var httpResp = wex.Response as HttpWebResponse;
                if (httpResp != null)
                {
                    // 有响应但状态码不是 200：端口通，应用或协议有问题
                    detail = "HTTP " + (int)httpResp.StatusCode;
                    httpResp.Close();
                    return AppState.Unhealthy;
                }
                detail = wex.Status.ToString();
                return AppState.Down;
            }
            catch (Exception ex)
            {
                detail = ex.GetType().Name;
                return AppState.Down;
            }
        }

        private void SetState(AppState state, string detail = null)
        {
            _state = state;

            string label;
            switch (state)
            {
                case AppState.Healthy: label = "运行正常"; break;
                case AppState.Unhealthy: label = "响应异常"; break;
                case AppState.Down: label = "服务未运行"; break;
                default: label = "正在检查…"; break;
            }

            // 右键菜单里只给人话，技术细节塞进 tooltip。
            //
            // 之前是「状态：响应异常（HTTP 502）」「状态：服务未运行（NameResolutionFailure）」
            // —— 用户看到一堆英文异常名，既看不懂也不知道该干什么。
            // 菜单项是用来扫一眼判断好坏的，不是用来看日志的。
            _statusItem.Text = "状态：" + label;
            _statusItem.ToolTipText = string.IsNullOrEmpty(detail)
                ? label
                : ("技术细节：" + Trim(detail, 120));

            var icon = BuildIcon(state);
            _icon.Icon = icon;
            if (_currentIcon != null) _currentIcon.Dispose();
            _currentIcon = icon;

            _icon.Text = BuildTooltip(label);
        }

        private string BuildTooltip(string label)
        {
            // NotifyIcon.Text 上限 63 字符，超了会抛异常
            string t = "LAN Share · " + label + "\n" + _cfg.WebUrl;
            return t.Length > 63 ? t.Substring(0, 63) : t;
        }

        private static string Trim(string s, int max)
        {
            if (string.IsNullOrEmpty(s)) return "";
            s = s.Replace("\r", " ").Replace("\n", " ");
            return s.Length <= max ? s : s.Substring(0, max) + "…";
        }

        // ── 图标绘制 ──────────────────────────────────────────────
        //
        // 图标由两部分合成：
        //   底 = 品牌图标（logo/lanshare.ico，编译时以 /resource 嵌入程序集）
        //   角 = 右下角状态点（绿/黄/红/灰）
        //
        // 早期版本是「纯色圆 + 白字母 L」，跟网页端和 App 的品牌完全对不上，
        // 用户认不出这是同一个产品。改成品牌底图后，托盘、窗口、任务栏三处统一。
        //
        // 图标从嵌入资源读，不依赖磁盘上的 .ico —— 安装包漏拷文件也不会变成白板。

        private static Bitmap _brandCache;

        /// <summary>读嵌入的品牌图标位图（32×32）。失败返回 null，调用方回退。</summary>
        private static Bitmap LoadBrandBitmap()
        {
            if (_brandCache != null) return _brandCache;

            try
            {
                var asm = typeof(Program).Assembly;
                using (var stream = asm.GetManifestResourceStream("LanShareTray.lanshare.ico"))
                {
                    if (stream != null)
                    {
                        // .ico 里有多尺寸，取 16×16 那帧 ——
                        // 托盘实际就显示 16×16，取 32 再缩会糊成马赛克
                        using (var ico = new Icon(stream, new Size(16, 16)))
                        {
                            _brandCache = ico.ToBitmap();
                            return _brandCache;
                        }
                    }
                }
            }
            catch (Exception) { }

            // 回退：直接从 exe 自身的图标提取（/win32icon 也会带这个）
            try
            {
                using (var exeIcon = Icon.ExtractAssociatedIcon(Application.ExecutablePath))
                {
                    if (exeIcon != null)
                    {
                        _brandCache = exeIcon.ToBitmap();
                        return _brandCache;
                    }
                }
            }
            catch (Exception) { }

            return null;
        }

        private static Icon BuildIcon(AppState state)
        {
            // 四态图标由 scripts/make-desktop-icon.py 预先画好（32px PNG），
            // 这里只负责加载。绘制逻辑不在这里 —— 手机 App、桌面图标、托盘
            // 共用 scripts/brandmark.py 一份实现，避免又出现「两边长得不一样」。
            string res;
            switch (state)
            {
                case AppState.Healthy: res = "LanShareTray.tray-ok.png"; break;
                case AppState.Unhealthy: res = "LanShareTray.tray-warn.png"; break;
                case AppState.Down: res = "LanShareTray.tray-error.png"; break;
                default: res = "LanShareTray.tray-none.png"; break;
            }

            try
            {
                var asm = typeof(TrayApp).Assembly;
                using (var stream = asm.GetManifestResourceStream(res))
                {
                    if (stream != null)
                    {
                        using (var bmp = new Bitmap(stream))
                        using (var sized = new Bitmap(bmp, new Size(32, 32)))
                        {
                            // 统一到 32×32：托盘按 DPI 可能向系统要 16/20/24/32，
                            // 给 32 向小缩不会糊，给 16 放大会马赛克。
                            //
                            // ⚠️ sized 必须 using —— 状态每变一次就调一次 BuildIcon，
                            // 不释放的话每次泄漏一个 32×32 位图 + 一个 GDI 句柄。
                            var icon = IconFromBitmapWithAlpha(sized);
                            if (icon != null) return icon;
                        }
                    }
                }
            }
            catch (Exception ex)
            {
                // BuildIcon 是静态的，拿不到实例上的 Log() —— 走 Debug 输出，
                // 有调试器时可见，没有时静默回退，绝不让图标问题拖垮托盘启动
                System.Diagnostics.Debug.WriteLine("托盘图标加载失败，回退旧画法: " + ex);
            }

            return BuildIconFallback(state);
        }

        /// <summary>
        /// Bitmap → Icon，保留 alpha 通道。
        ///
        /// 为什么不能用 Bitmap.GetHicon()：它会把 alpha 抹平，圆角变方块、
        /// 半透明边缘变黑边 —— 图标看起来像贴了张黑底纸。
        /// CreateIconIndirect 走 HBITMAP（带 alpha），能保住透明度。
        /// </summary>
        private static Icon IconFromBitmapWithAlpha(Bitmap bmp)
        {
            IntPtr hIcon = IntPtr.Zero, hMask = IntPtr.Zero, hColor = IntPtr.Zero;
            try
            {
                // 用 0-alpha 做背景色传入，GetHbitmap 才会保留每像素的 alpha
                hColor = bmp.GetHbitmap(Color.FromArgb(0));
                hMask = bmp.GetHbitmap(Color.FromArgb(0));
                var ii = new ICONINFO
                {
                    fIcon = true,
                    xHotspot = 0,
                    yHotspot = 0,
                    hbmMask = hMask,
                    hbmColor = hColor
                };
                hIcon = CreateIconIndirect(ref ii);
                if (hIcon == IntPtr.Zero) return null;
                return (Icon)Icon.FromHandle(hIcon).Clone();
            }
            finally
            {
                if (hIcon != IntPtr.Zero) DestroyIcon(hIcon);
                if (hMask != IntPtr.Zero) DeleteObject(hMask);
                if (hColor != IntPtr.Zero) DeleteObject(hColor);
            }
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct ICONINFO
        {
            public bool fIcon;
            public int xHotspot;
            public int yHotspot;
            public IntPtr hbmMask;
            public IntPtr hbmColor;
        }

        [DllImport("user32.dll", SetLastError = true)]
        private static extern IntPtr CreateIconIndirect(ref ICONINFO piconinfo);

        [DllImport("gdi32.dll", SetLastError = true)]
        private static extern bool DeleteObject(IntPtr hObject);

        /// <summary>旧画法：资源缺失时兜底，保证托盘里一定有个图标。</summary>
        private static Icon BuildIconFallback(AppState state)
        {
            Color fill;
            switch (state)
            {
                case AppState.Healthy: fill = Color.FromArgb(34, 160, 90); break;
                case AppState.Unhealthy: fill = Color.FromArgb(220, 160, 30); break;
                case AppState.Down: fill = Color.FromArgb(200, 60, 60); break;
                default: fill = Color.FromArgb(140, 140, 140); break;
            }
            Color bg = state == AppState.Healthy
                ? Color.FromArgb(0x6B, 0x62, 0xFF)
                : fill;

            const int S = 32;
            float u = S / 16f;
            using (var bmp = new Bitmap(S, S))
            using (var g = Graphics.FromImage(bmp))
            {
                g.SmoothingMode = SmoothingMode.AntiAlias;
                g.PixelOffsetMode = PixelOffsetMode.HighQuality;
                g.Clear(Color.Transparent);
                using (var path = Draw.RoundedPath(new Rectangle(0, 0, S, S), (int)(4 * u)))
                using (var brush = new SolidBrush(bg))
                    g.FillPath(brush, path);
                using (var white = new SolidBrush(Color.White))
                {
                    g.FillEllipse(white, 3.5f*u, 7.1f*u, 5.0f*u, 5.0f*u);
                    g.FillEllipse(white, 5.8f*u, 4.9f*u, 6.8f*u, 6.8f*u);
                    g.FillRectangle(white, 5.8f*u, 8.6f*u, 5.2f*u, 3.5f*u);
                }
                IntPtr hIcon = bmp.GetHicon();
                try
                {
                    return (Icon)Icon.FromHandle(hIcon).Clone();
                }
                finally
                {
                    DestroyIcon(hIcon);
                }
            }
        }

        [DllImport("user32.dll", CharSet = CharSet.Auto)]
        private static extern bool DestroyIcon(IntPtr handle);

        // ── 动作 ──────────────────────────────────────────────────

        /// <summary>
        /// 显示控制面板。已开则激活到前台（不重复创建）。
        ///
        /// 窗口生命周期跟托盘一致：关掉窗口只是隐藏，不留后台进程。
        /// 这样用户点关闭的直觉（"关掉了"）和实际行为一致，
        /// 而下次点图标能秒开，不用重新初始化。
        /// </summary>
        /// <summary>
        /// 建控制面板：优先 WebView2（HTML 界面），不可用就回退 WinForms 版。
        ///
        /// 为什么要有回退：WebView2 Runtime 不是所有 Windows 都有
        /// （LTSC、精简版、老版本可能没装）。没有就白屏太糟糕，
        /// 所以宁可退回旧界面 —— 丑是丑，但功能一个不少。
        ///
        /// _forceWinFormsPanel 记住「这台机器不行」，避免每开一次窗口
        /// 都先失败一次再回退（那会闪一下）。
        /// </summary>
        private Form CreatePanel()
        {
            if (!_forceWinFormsPanel)
            {
                try
                {
                    var web = new WebPanel(_cfg, _cli);
                    Log("使用 WebView2 面板");
                    return web;
                }
                catch (WebView2Unavailable ex)
                {
                    _forceWinFormsPanel = true;
                    Log("WebView2 不可用，回退 WinForms：" + ex.Message);
                }
                catch (Exception ex)
                {
                    _forceWinFormsPanel = true;
                    Log("WebView2 面板创建失败，回退 WinForms：" + ex);
                }
            }
            return new LanSharePanel(_cfg, _cli);
        }

        private void ShowPanel()
        {
            if (!_cfg.Installed)
            {
                MessageBox.Show(
                    "LAN Share 好像还没安装好（找不到安装目录）。\n" +
                    "请重新运行安装程序。",
                    "LAN Share", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return;
            }

            if (_panel == null || _panel.IsDisposed)
            {
                try
                {
                    _panel = CreatePanel();
                    _panel.FormClosed += (s, e) => { };
                }
                catch (Exception ex)
                {
                    // 面板构造失败以前是静默的 —— 用户点了没反应，日志里也没痕迹
                    Log("面板构造失败: " + ex);
                    MessageBox.Show(
                        "打开控制面板时出错：" + Environment.NewLine + Environment.NewLine + ex.Message,
                        "LAN Share", MessageBoxButtons.OK, MessageBoxIcon.Error);
                    return;
                }
            }

            if (_panel.Visible)
            {
                if (_panel.WindowState == FormWindowState.Minimized)
                    _panel.WindowState = FormWindowState.Normal;
                _panel.Activate();
                return;
            }

            _panel.Show();
            _panel.Activate();
        }

        private void OpenBrowser()
        {
            if (!_cfg.Installed)
            {
                MessageBox.Show(
                    "LAN Share 好像还没安装好（注册表里没有安装信息）。\n" +
                    "请重新运行安装程序。",
                    "LAN Share", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return;
            }
            OpenUrl(_cfg.WebUrl);
        }

        private void CopyLanAddress()
        {
            string ip = GetLanIp();
            string addr = "http://" + ip + ":" + _cfg.WebPort;

            try
            {
                Clipboard.SetText(addr);
                _icon.ShowBalloonTip(2500, "已复制", addr, ToolTipIcon.Info);
            }
            catch (Exception)
            {
                MessageBox.Show(addr, "局域网地址（手动复制）", MessageBoxButtons.OK, MessageBoxIcon.Information);
            }
        }

        /// <summary>取一个局域网 IPv4。多网卡时挑第一个非回环、非 169.254 的。</summary>
        private static string GetLanIp()
        {
            try
            {
                foreach (var ni in System.Net.NetworkInformation.NetworkInterface.GetAllNetworkInterfaces())
                {
                    if (ni.OperationalStatus != System.Net.NetworkInformation.OperationalStatus.Up) continue;
                    if (ni.NetworkInterfaceType == System.Net.NetworkInformation.NetworkInterfaceType.Loopback) continue;

                    foreach (var ua in ni.GetIPProperties().UnicastAddresses)
                    {
                        if (ua.Address.AddressFamily != System.Net.Sockets.AddressFamily.InterNetwork) continue;
                        string s = ua.Address.ToString();
                        if (s.StartsWith("169.254.")) continue;
                        return s;
                    }
                }
            }
            catch (Exception) { }
            return "127.0.0.1";
        }

        private void OpenFolder(string path)
        {
            if (string.IsNullOrEmpty(path))
            {
                MessageBox.Show("还没有配置这个路径。", "LAN Share", MessageBoxButtons.OK, MessageBoxIcon.Information);
                return;
            }
            if (!Directory.Exists(path))
            {
                MessageBox.Show("目录不存在：\n" + path, "LAN Share", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return;
            }
            OpenUrl(path);
        }

        private static void OpenUrl(string target)
        {
            try
            {
                Process.Start(new ProcessStartInfo(target) { UseShellExecute = true });
            }
            catch (Exception ex)
            {
                MessageBox.Show("打不开：" + target + "\n\n" + ex.Message,
                    "LAN Share", MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
        }

        /// <summary>
        /// 重启两个服务。需要管理员权限，这里用 runas 提权执行 sc 命令 ——
        /// 托盘本身不要求管理员运行（那样每次开机都要过 UAC）。
        /// </summary>
        private void RestartServices()
        {
            if (MessageBox.Show(
                    "重启 LAN Share 服务？\n\n" +
                    "正在上传/下载的文件会中断，几十秒后恢复。",
                    "LAN Share", MessageBoxButtons.YesNo, MessageBoxIcon.Question) != DialogResult.Yes)
                return;

            try
            {
                var psi = new ProcessStartInfo
                {
                    FileName = "cmd.exe",
                    Arguments = "/c net stop lanshare-caddy & net stop lanshare-api & " +
                                "net start lanshare-api & net start lanshare-caddy",
                    UseShellExecute = true,
                    Verb = "runas",           // 触发 UAC
                    WindowStyle = ProcessWindowStyle.Hidden
                };
                Process.Start(psi);

                _icon.ShowBalloonTip(4000, "正在重启", "服务重启中，请稍候…", ToolTipIcon.Info);

                // 10 秒后重新探测
                var t = new Timer { Interval = 10000 };
                t.Tick += (s, e) => { t.Stop(); t.Dispose(); _failCount = 0; Probe(); };
                t.Start();
            }
            catch (System.ComponentModel.Win32Exception)
            {
                // 用户在 UAC 上点了取消
                MessageBox.Show("已取消（需要管理员权限才能重启服务）。",
                    "LAN Share", MessageBoxButtons.OK, MessageBoxIcon.Information);
            }
            catch (Exception ex)
            {
                MessageBox.Show("重启失败：" + ex.Message,
                    "LAN Share", MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
        }

        private void ExitTray()
        {
            if (_state != AppState.Healthy && _firstProbeDone && _failCount > 0)
            {
                // 状态不正常时提醒一句，避免用户以为退出托盘就好了
                MessageBox.Show(
                    "服务当前不正常。\n\n" +
                    "退出托盘不会停止服务，也不会修复问题。\n" +
                    "建议先看「打开日志文件夹」里的日志。",
                    "LAN Share", MessageBoxButtons.OK, MessageBoxIcon.Warning);
            }
            _timer.Stop();
            _icon.Visible = false;
            ExitThread();
        }

        protected override void Dispose(bool disposing)
        {
            if (disposing)
            {
                if (_timer != null) _timer.Dispose();
                if (_icon != null) { _icon.Visible = false; _icon.Dispose(); }
                if (_currentIcon != null) _currentIcon.Dispose();
            }
            base.Dispose(disposing);
        }
    }
}
