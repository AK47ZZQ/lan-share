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
    internal static class Program
    {
        [System.Runtime.InteropServices.DllImport("user32.dll")]
        private static extern bool SetProcessDPIAware();

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

                // DPI 感知：不加的话在 125%/150% 缩放的屏幕上，
                // 系统会把整个窗口位图拉伸，文字全部发虚。
                // 必须在创建任何窗口之前调用。
                try { SetProcessDPIAware(); } catch (Exception) { }

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
                            }
                        }
                        catch (Exception) { }
                        // 非本机：走系统默认校验，不能因为我们是托盘就放松
                        return errors == System.Net.Security.SslPolicyErrors.None;
                    };

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
        private LanSharePanel _panel;          // 控制面板窗口，懒创建
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

            _cli = new CliRunner(_cfg.InstallFolder, _cfg.PythonPath, _cfg.ServicePrefix);

            Log("托盘启动 | Installed=" + _cfg.Installed
                + " Prefix=" + _cfg.ServicePrefix
                + " WebPort=" + _cfg.WebPort
                + " ApiPort=" + _cfg.ApiPort
                + " Health=" + _cfg.HealthUrl);

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

            _timer = new Timer { Interval = 5000 };  // 5 秒一轮
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
        }

        // ── 状态探测 ──────────────────────────────────────────────

        private void Probe()
        {
            string result = null;
            var state = AppState.Down;

            // 协议自适应：老部署用自签 HTTPS，新安装包是纯 HTTP。
            // 硬编码任一个都会在另一半环境里误报「服务挂了」。
            // 先试 HTTP（新装默认），400/失败再试 HTTPS。
            state = TryProbe(_cfg.HealthUrl, out result);
            Log("探测 HTTP " + _cfg.HealthUrl + " → " + state + " | " + result);

            if (state != AppState.Healthy)
            {
                string httpsUrl = "https://127.0.0.1:" + _cfg.WebPort + "/healthz";
                string httpsResult;
                var httpsState = TryProbe(httpsUrl, out httpsResult);
                Log("探测 HTTPS " + httpsUrl + " → " + httpsState + " | " + httpsResult);

                if (httpsState == AppState.Healthy)
                {
                    state = httpsState;
                    result = httpsResult;
                }
                else if (httpsState == AppState.Unhealthy)
                {
                    // 两者都不健康时，HTTPS 的那个更有意义（说明端口确实在听）
                    state = httpsState;
                    result = httpsResult;
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
                req.Timeout = 4000;
                req.ReadWriteTimeout = 4000;
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

            _statusItem.Text = "状态：" + label + (string.IsNullOrEmpty(detail) ? "" : "（" + Trim(detail, 40) + "）");

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
        // 不依赖外部 .ico 文件：代码画一个，省得打包时漏文件。

        private static Icon BuildIcon(AppState state)
        {
            Color fill;
            switch (state)
            {
                case AppState.Healthy: fill = Color.FromArgb(34, 160, 90); break;    // 绿
                case AppState.Unhealthy: fill = Color.FromArgb(220, 160, 30); break; // 黄
                case AppState.Down: fill = Color.FromArgb(200, 60, 60); break;       // 红
                default: fill = Color.FromArgb(140, 140, 140); break;                // 灰
            }

            using (var bmp = new Bitmap(32, 32))
            using (var g = Graphics.FromImage(bmp))
            {
                g.SmoothingMode = SmoothingMode.AntiAlias;
                g.Clear(Color.Transparent);

                using (var brush = new SolidBrush(fill))
                    g.FillEllipse(brush, 1, 1, 30, 30);

                // 白色 L
                using (var pen = new Pen(Color.White, 3.5f))
                {
                    pen.StartCap = LineCap.Round;
                    pen.EndCap = LineCap.Round;
                    g.DrawLine(pen, 11, 9, 11, 21);
                    g.DrawLine(pen, 11, 21, 21, 21);
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
                _panel = new LanSharePanel(_cfg, _cli);
                _panel.FormClosed += (s, e) => { };
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
