// LAN Share 桌面控制面板
//
// 定位：**只做服务层的事**，业务功能全在 Web UI 里。
// 这个窗口存在的理由是那四件事在浏览器里做不了或做起来别扭：
//   1. 服务在不在跑（不用开浏览器就能看）
//   2. 局域网开没开、地址是什么、二维码在哪（手机要扫码）
//   3. 数据目录多大、在哪（要能直接打开资源管理器）
//   4. 服务挂了要能一键重启
//
// 所有状态都通过 lanshare_cli 的 --json 接口拿，不在这里重复实现判断逻辑 ——
// 逻辑只有一份，才能保证托盘、GUI、命令行看到的东西永远一致。
//
// 编译：csc /target:winexe /reference:System.Web.Extensions.dll ... （见 build-tray.ps1）

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Globalization;
using System.IO;
using System.Net;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Win32;

namespace LanShare
{
    /// <summary>一句话：调 lanshare_cli 并解析它的 JSON 输出。</summary>
    internal sealed class CliRunner
    {
        private readonly string _python;
        private readonly string _installDir;
        private readonly string _servicePrefix;

        public CliRunner(string installDir, string python, string servicePrefix)
        {
            _installDir = installDir;
            _python = python;
            _servicePrefix = servicePrefix;
        }

        /// <summary>
        /// 跑一个 lan 子命令，返回解析后的字典。失败返回 null 并给出 error。
        /// 超时设得比较长：enable 可能弹 UAC 等用户点，还要重启 Caddy。
        /// </summary>
        public Dictionary<string, object> RunLan(string action, int timeoutMs, out string error)
        {
            error = null;

            if (string.IsNullOrEmpty(_python) || !File.Exists(_python))
            {
                error = "找不到 Python 运行时：" + (_python ?? "(空)");
                return null;
            }

            var psi = new ProcessStartInfo
            {
                FileName = _python,
                Arguments = "-m lanshare_cli lan " + action + " --json --service-prefix " + _servicePrefix,
                WorkingDirectory = _installDir,
                UseShellExecute = false,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                CreateNoWindow = true,
                StandardOutputEncoding = Encoding.UTF8,
                StandardErrorEncoding = Encoding.UTF8,
            };

            try
            {
                using (var proc = Process.Start(psi))
                {
                    // 异步读，避免管道满了互相等死
                    var stdoutTask = proc.StandardOutput.ReadToEndAsync();
                    var stderrTask = proc.StandardError.ReadToEndAsync();

                    if (!proc.WaitForExit(timeoutMs))
                    {
                        try { proc.Kill(); } catch { }
                        error = "命令超时（" + (timeoutMs / 1000) + " 秒）";
                        return null;
                    }

                    string stdout = stdoutTask.Result;
                    string stderr = stderrTask.Result;

                    if (string.IsNullOrWhiteSpace(stdout))
                    {
                        error = string.IsNullOrWhiteSpace(stderr)
                            ? "命令没有输出（退出码 " + proc.ExitCode + "）"
                            : stderr.Trim();
                        return null;
                    }

                    var serializer = new JavaScriptSerializer { MaxJsonLength = int.MaxValue };
                    var dict = serializer.Deserialize<Dictionary<string, object>>(stdout);
                    if (dict == null)
                    {
                        error = "输出不是预期的 JSON";
                        return null;
                    }
                    return dict;
                }
            }
            catch (Exception ex)
            {
                error = ex.Message;
                return null;
            }
        }

        /// <summary>下载二维码 PNG（走本机免认证接口）。失败返回 null。</summary>
        public Image FetchQrPng(string url, int timeoutMs)
        {
            try
            {
                var req = (HttpWebRequest)WebRequest.Create(url);
                req.Timeout = timeoutMs;
                req.ReadWriteTimeout = timeoutMs;
                using (var resp = (HttpWebResponse)req.GetResponse())
                using (var stream = resp.GetResponseStream())
                {
                    // Image.FromStream 需要流在图片生命周期内保持可用，
                    // 所以先完整读进内存再构造
                    using (var ms = new MemoryStream())
                    {
                        var buffer = new byte[8192];
                        int read;
                        while ((read = stream.Read(buffer, 0, buffer.Length)) > 0)
                        {
                            ms.Write(buffer, 0, read);
                        }
                        ms.Position = 0;
                        return Image.FromStream(ms);
                    }
                }
            }
            catch
            {
                return null;
            }
        }
    }

    /// <summary>控制面板主窗口。</summary>
    internal sealed class LanSharePanel : Form
    {
        private readonly InstallConfig _cfg;
        private readonly CliRunner _cli;

        // 服务状态区
        private readonly Label _serviceStatus;
        private readonly Label _apiLine;
        private readonly Label _webLine;

        // 局域网区
        private readonly Panel _lanBox;
        private readonly Label _lanState;
        private readonly Label _lanHint;
        private readonly TextBox _lanAddress;
        private readonly Button _lanCopy;
        private readonly Button _lanToggle;
        private readonly PictureBox _qrBox;
        private readonly Label _qrHint;

        // 底部
        private readonly Label _dataLabel;
        private readonly Button _btnOpenWeb;
        private readonly Button _btnRestart;
        private readonly Button _btnFolder;
        private readonly Button _btnLogs;

        private readonly System.Windows.Forms.Timer _refreshTimer;
        private readonly Label _busyLabel;

        private bool _busy;
        private string _lastLanState = "";
        private DateTime _lastRefresh = DateTime.MinValue;

        public LanSharePanel(InstallConfig cfg, CliRunner cli)
        {
            _cfg = cfg;
            _cli = cli;

            Text = "LAN Share";
            FormBorderStyle = FormBorderStyle.FixedSingle;
            MaximizeBox = false;
            StartPosition = FormStartPosition.CenterScreen;
            ClientSize = new Size(520, 650);
            Font = new Font("Microsoft YaHei UI", 9F);
            BackColor = Color.FromArgb(250, 250, 252);
            Icon = SystemIcons.Application;

            // ── 标题 ──
            var title = new Label
            {
                Text = "LAN Share",
                Font = new Font("Microsoft YaHei UI", 15F, FontStyle.Bold),
                Location = new Point(24, 18),
                AutoSize = true,
                ForeColor = Color.FromArgb(40, 44, 60),
            };
            Controls.Add(title);

            // ── 服务状态 ──
            _serviceStatus = new Label
            {
                Text = "检查中…",
                Font = new Font("Microsoft YaHei UI", 11F, FontStyle.Bold),
                Location = new Point(24, 56),
                AutoSize = true,
                ForeColor = Color.Gray,
            };
            Controls.Add(_serviceStatus);

            _apiLine = new Label
            {
                Text = "",
                Location = new Point(40, 88),
                AutoSize = true,
                ForeColor = Color.FromArgb(90, 94, 110),
            };
            Controls.Add(_apiLine);

            _webLine = new Label
            {
                Text = "",
                Location = new Point(40, 110),
                AutoSize = true,
                ForeColor = Color.FromArgb(90, 94, 110),
            };
            Controls.Add(_webLine);

            // ── 局域网卡片 ──
            _lanBox = new Panel
            {
                Location = new Point(24, 140),
                Size = new Size(472, 326),
                BackColor = Color.White,
                BorderStyle = BorderStyle.FixedSingle,
            };
            Controls.Add(_lanBox);

            var lanTitle = new Label
            {
                Text = "局域网访问",
                Font = new Font("Microsoft YaHei UI", 9F, FontStyle.Bold),
                Location = new Point(14, 12),
                AutoSize = true,
                ForeColor = Color.FromArgb(120, 124, 140),
            };
            _lanBox.Controls.Add(lanTitle);

            _lanState = new Label
            {
                Text = "读取中…",
                Font = new Font("Microsoft YaHei UI", 12F, FontStyle.Bold),
                Location = new Point(14, 36),
                Size = new Size(440, 26),
                ForeColor = Color.Gray,
            };
            _lanBox.Controls.Add(_lanState);

            _lanHint = new Label
            {
                Text = "",
                Location = new Point(14, 64),
                // 60 高是为了容纳三行中文（DPI 感知后字体按真实像素渲染，
                // 40 高只够两行，长句会被切掉半个字）
                Size = new Size(440, 60),
                ForeColor = Color.FromArgb(110, 114, 130),
            };
            _lanBox.Controls.Add(_lanHint);

            _lanAddress = new TextBox
            {
                Location = new Point(14, 132),
                Size = new Size(330, 26),
                ReadOnly = true,
                BorderStyle = BorderStyle.FixedSingle,
                Font = new Font("Consolas", 10F),
                Visible = false,
                BackColor = Color.FromArgb(248, 250, 255),
            };
            _lanBox.Controls.Add(_lanAddress);

            _lanCopy = new Button
            {
                Text = "复制",
                Location = new Point(352, 131),
                Size = new Size(100, 28),
                FlatStyle = FlatStyle.System,
                Visible = false,
            };
            _lanCopy.Click += delegate { CopyAddress(); };
            _lanBox.Controls.Add(_lanCopy);

            _qrBox = new PictureBox
            {
                Location = new Point(14, 170),
                Size = new Size(96, 96),
                SizeMode = PictureBoxSizeMode.Zoom,
                BorderStyle = BorderStyle.FixedSingle,
                Visible = false,
                BackColor = Color.White,
            };
            _lanBox.Controls.Add(_qrBox);

            _qrHint = new Label
            {
                Text = "手机扫码直接连",
                Location = new Point(124, 170),
                Size = new Size(200, 22),
                ForeColor = Color.FromArgb(110, 114, 130),
                Visible = false,
            };
            _lanBox.Controls.Add(_qrHint);

            _lanToggle = new Button
            {
                Text = "启动局域网服务",
                Location = new Point(14, 170),
                Size = new Size(180, 36),
                FlatStyle = FlatStyle.System,
            };
            _lanToggle.Click += delegate { ToggleLan(); };
            _lanBox.Controls.Add(_lanToggle);

            // ── 数据目录 ──
            _dataLabel = new Label
            {
                Text = "",
                Location = new Point(24, 482),
                Size = new Size(472, 20),
                ForeColor = Color.FromArgb(110, 114, 130),
            };
            Controls.Add(_dataLabel);

            // ── 底部按钮 ──
            _btnOpenWeb = MakeButton("打开网盘", 24, 516, 112);
            _btnOpenWeb.Click += delegate { OpenUrl(_cfg.WebUrl); };
            Controls.Add(_btnOpenWeb);

            _btnRestart = MakeButton("重启服务", 144, 516, 112);
            _btnRestart.Click += delegate { RestartServices(); };
            Controls.Add(_btnRestart);

            _btnFolder = MakeButton("数据目录", 264, 516, 112);
            _btnFolder.Click += delegate { OpenFolder(_cfg.DataFolder); };
            Controls.Add(_btnFolder);

            _btnLogs = MakeButton("看日志", 384, 516, 112);
            _btnLogs.Click += delegate { OpenFolder(_cfg.LogFolder); };
            Controls.Add(_btnLogs);

            // ── 忙碌提示 ──
            _busyLabel = new Label
            {
                Text = "",
                Location = new Point(24, 566),
                Size = new Size(472, 20),
                ForeColor = Color.FromArgb(0, 120, 200),
            };
            Controls.Add(_busyLabel);

            // ── 版本 ──
            var version = new Label
            {
                Text = "服务前缀 " + _cfg.ServicePrefix,
                Location = new Point(24, 596),
                AutoSize = true,
                ForeColor = Color.FromArgb(170, 174, 190),
                Font = new Font("Microsoft YaHei UI", 8F),
            };
            Controls.Add(version);

            // 每 5 秒刷新一次；窗口最小化时也刷新，因为托盘状态要同步
            _refreshTimer = new System.Windows.Forms.Timer { Interval = 5000 };
            _refreshTimer.Tick += delegate { RefreshStateAsync(quiet: true); };
            _refreshTimer.Start();

            Shown += delegate { RefreshStateAsync(quiet: false); };
            FormClosed += delegate { _refreshTimer.Stop(); _qrBox.Image = null; };
        }

        private Button MakeButton(string text, int x, int y, int w)
        {
            return new Button
            {
                Text = text,
                Location = new Point(x, y),
                Size = new Size(w, 32),
                FlatStyle = FlatStyle.System,
            };
        }

        // ──────────────────────────────────────────────────────
        // 状态刷新
        // ──────────────────────────────────────────────────────

        /// <summary>后台线程跑 CLI，UI 线程更新控件。</summary>
        private void RefreshStateAsync(bool quiet)
        {
            if (_busy) return;

            // 手动刷新时限制频率，防止连点
            if (quiet && (DateTime.Now - _lastRefresh).TotalSeconds < 4) return;
            _lastRefresh = DateTime.Now;

            Task.Run(() =>
            {
                string error;
                var dict = _cli.RunLan("status", 20000, out error);
                return new object[] { dict, error };
            }).ContinueWith(t =>
            {
                if (IsDisposed) return;
                var result = t.Result;
                var dict = (Dictionary<string, object>)result[0];
                var error = (string)result[1];
                ApplyStatus(dict, error, quiet);
            }, TaskScheduler.FromCurrentSynchronizationContext());
        }

        private void ApplyStatus(Dictionary<string, object> dict, string error, bool quiet)
        {
            if (dict == null)
            {
                SetServiceBadge("状态未知", Color.Gray);
                _apiLine.Text = "";
                _webLine.Text = error ?? "无法读取状态";
                _webLine.ForeColor = Color.Firebrick;
                return;
            }

            string state = Str(dict, "state");
            string stateLabel = Str(dict, "state_label");
            int apiPort = Int(dict, "api_port");
            int webPort = Int(dict, "web_port");
            bool probeOk = Bool(dict, "probe_ok");
            string probeMsg = Str(dict, "probe_msg");

            // 服务整体状态：监听地址有没有、自检通不通
            var listening = List(dict, "listening");
            bool listeningAny = listening.Count > 0;

            if (state == "on" || state == "off")
            {
                SetServiceBadge("服务正常", Color.FromArgb(40, 160, 90));
            }
            else if (state == "blocked")
            {
                SetServiceBadge("服务在跑，但局域网连不上", Color.FromArgb(220, 150, 0));
            }
            else if (state == "no_network")
            {
                SetServiceBadge("服务正常（无网络连接）", Color.FromArgb(40, 160, 90));
            }
            else
            {
                SetServiceBadge(stateLabel.Length > 0 ? stateLabel : "服务未运行", Color.Firebrick);
            }

            _apiLine.Text = "API   127.0.0.1:" + apiPort + "   " + (listeningAny ? "监听中" : "未监听");

            // Web 那行显示真实监听范围，这比「RUNNING」有信息量
            string bindText = "未监听";
            foreach (var a in listening)
            {
                string s = Convert.ToString(a);
                if (s == "0.0.0.0" || s == "::") bindText = "0.0.0.0:" + webPort + "（所有网卡）";
                else if (s == "127.0.0.1") bindText = "127.0.0.1:" + webPort + "（仅本机）";
                else if (bindText.IndexOf(':') < 0) bindText = s + ":" + webPort;
            }
            _webLine.Text = "Web   " + bindText;
            _webLine.ForeColor = Color.FromArgb(90, 94, 110);

            UpdateLanSection(dict, state, probeOk, probeMsg, quiet);
            UpdateDataLine(dict);
        }

        private void SetServiceBadge(string text, Color color)
        {
            _serviceStatus.Text = "●  " + text;
            _serviceStatus.ForeColor = color;
        }

        private void UpdateLanSection(Dictionary<string, object> dict, string state, bool probeOk, string probeMsg, bool quiet)
        {
            bool wasOn = _lastLanState == "on" || _lastLanState == "blocked";
            bool isOn = state == "on" || state == "blocked";
            _lastLanState = state;

            // 地址（取第一个，也就是有默认网关的那个）
            string url = "";
            var addrs = List(dict, "addresses");
            if (addrs.Count > 0)
            {
                var first = addrs[0] as Dictionary<string, object>;
                if (first != null)
                {
                    url = "http://" + Str(first, "ip") + ":" + Int(dict, "web_port") + "/";
                }
            }

            bool showOn = isOn && url.Length > 0;

            _lanState.Text = showOn ? "●  局域网已开启" : (state == "no_network" ? "○  没有可用网络" : "○  局域网未开启");
            _lanState.ForeColor = showOn
                ? (state == "blocked" ? Color.FromArgb(220, 150, 0) : Color.FromArgb(40, 160, 90))
                : Color.Gray;

            if (showOn)
            {
                _lanHint.Text = state == "blocked"
                    ? "已开启但自检失败：" + probeMsg
                    : "同一个 WiFi 下的手机、平板都能访问";
                _lanHint.ForeColor = state == "blocked" ? Color.FromArgb(220, 150, 0) : Color.FromArgb(110, 114, 130);
            }
            else if (state == "no_network")
            {
                _lanHint.Text = "这台电脑没有连接任何网络，插上网线或连上 WiFi 后再试";
                _lanHint.ForeColor = Color.FromArgb(110, 114, 130);
            }
            else
            {
                _lanHint.Text = "现在只有这台电脑能访问。开启后同一个 WiFi 下的手机也能用";
                _lanHint.ForeColor = Color.FromArgb(110, 114, 130);
            }

            _lanAddress.Visible = showOn;
            _lanCopy.Visible = showOn;
            _qrBox.Visible = showOn;
            _qrHint.Visible = showOn;

            // 按钮始终可见：未开启时是「启动局域网服务」，开启后是「关闭局域网」。
            // 曾经把它设成 !showOn，结果开了之后没有关闭入口 —— 开了就关不掉。
            _lanToggle.Visible = true;

            if (showOn)
            {
                if (_lanAddress.Text != url) _lanAddress.Text = url;
                _lanCopy.Top = _lanAddress.Top - 1;
                _lanToggle.Text = "关闭局域网";
                // 开启时按钮挪到二维码下方的空白处，避免和地址栏挤在一起
                _lanToggle.Location = new Point(14, 280);
                _lanToggle.Size = new Size(140, 32);
                LoadQrAsync(url, quiet && wasOn);
            }
            else
            {
                _lanToggle.Text = "启动局域网服务";
                _lanToggle.Location = new Point(14, 170);
                _lanToggle.Size = new Size(180, 36);
                _qrBox.Image = null;
            }
        }

        private void UpdateDataLine(Dictionary<string, object> dict)
        {
            string dir = _cfg.DataFolder;
            _dataLabel.Text = "数据目录   " + dir;
        }

        /// <summary>二维码走 /local/qr（本机免认证）。异步加载，失败就静默降级。</summary>
        private void LoadQrAsync(string targetUrl, bool skipIfLoaded)
        {
            if (skipIfLoaded && _qrBox.Image != null) return;

            string qrUrl = "http://127.0.0.1:" + _cfg.ApiPort + "/local/qr?format=png&text="
                           + Uri.EscapeDataString(targetUrl);

            Task.Run(() => _cli.FetchQrPng(qrUrl, 5000)).ContinueWith(t =>
            {
                if (IsDisposed) return;
                var img = t.Result;
                if (img == null)
                {
                    // 拿不到二维码不影响主功能，只把提示改掉
                    _qrHint.Text = "二维码加载失败（可以直接输地址）";
                    _qrBox.Visible = false;
                    return;
                }
                var old = _qrBox.Image;
                _qrBox.Image = img;
                _qrBox.Visible = true;
                _qrHint.Text = "手机扫码直接连";
                if (old != null) old.Dispose();
            }, TaskScheduler.FromCurrentSynchronizationContext());
        }

        // ──────────────────────────────────────────────────────
        // 动作
        // ──────────────────────────────────────────────────────

        private void CopyAddress()
        {
            if (_lanAddress.Text.Length == 0) return;
            try
            {
                Clipboard.SetText(_lanAddress.Text);
                _busyLabel.Text = "地址已复制";
                var t = new System.Windows.Forms.Timer { Interval = 2000 };
                t.Tick += delegate { t.Stop(); t.Dispose(); _busyLabel.Text = ""; };
                t.Start();
            }
            catch
            {
                _busyLabel.Text = "复制失败，请手动选中复制";
            }
        }

        private void ToggleLan()
        {
            bool turningOff = _lastLanState == "on" || _lastLanState == "blocked";
            string action = turningOff ? "disable" : "enable";

            _busy = true;
            SetBusy(true, turningOff ? "正在关闭局域网访问…" : "正在开启局域网访问…");

            Task.Run(() =>
            {
                string error;
                // enable 可能要等用户点 UAC，还要重启 Caddy，给足 3 分钟
                var dict = _cli.RunLan(action, 180000, out error);
                return new object[] { dict, error };
            }).ContinueWith(t =>
            {
                if (IsDisposed) return;
                var result = t.Result;
                var dict = (Dictionary<string, object>)result[0];
                var error = (string)result[1];
                _busy = false;
                SetBusy(false, "");
                ReportActionResult(turningOff, dict, error);
                RefreshStateAsync(quiet: false);
            }, TaskScheduler.FromCurrentSynchronizationContext());
        }

        private void ReportActionResult(bool wasTurningOff, Dictionary<string, object> dict, string error)
        {
            string verb = wasTurningOff ? "关闭" : "开启";

            if (dict == null)
            {
                MessageBox.Show(this,
                    "执行" + verb + "时出错：\n\n" + (error ?? "未知错误"),
                    "LAN Share", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return;
            }

            bool success = Bool(dict, "success");
            bool cancelled = Bool(dict, "cancelled");

            if (success) return;   // 状态刷新会把界面更新好，不用再打扰

            if (cancelled)
            {
                MessageBox.Show(this,
                    "已取消。\n\n配置防火墙需要管理员权限，你在授权窗口点了「否」。",
                    "LAN Share", MessageBoxButtons.OK, MessageBoxIcon.Information);
                return;
            }

            // 失败：把每一步摊开给用户看，比一句「失败了」有用得多
            var sb = new StringBuilder();
            sb.AppendLine("执行" + verb + "没有成功。下面每一步的结果：");
            sb.AppendLine();

            var steps = List(dict, "steps");
            foreach (var s in steps)
            {
                var step = s as Dictionary<string, object>;
                if (step == null) continue;
                bool stepOk = Bool(step, "ok");
                sb.AppendLine((stepOk ? "  ✓ " : "  ✗ ") + Str(step, "name"));
                string detail = Str(step, "detail");
                if (detail.Length > 0) sb.AppendLine("      " + detail);
            }

            MessageBox.Show(this, sb.ToString(), "LAN Share", MessageBoxButtons.OK, MessageBoxIcon.Warning);
        }

        private void RestartServices()
        {
            var confirm = MessageBox.Show(this,
                "重启后正在进行的上传/下载会中断，网页需要刷新。\n\n确定重启服务吗？",
                "重启服务", MessageBoxButtons.YesNo, MessageBoxIcon.Question);
            if (confirm != DialogResult.Yes) return;

            SetBusy(true, "正在重启服务…");

            Task.Run(() =>
            {
                string msg = ServiceControl.Restart(_cfg);
                return msg;
            }).ContinueWith(t =>
            {
                if (IsDisposed) return;
                SetBusy(false, "");
                string msg = t.Result;
                if (msg != null)
                {
                    MessageBox.Show(this, msg, "重启服务", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                }
                _lastRefresh = DateTime.MinValue;
                RefreshStateAsync(quiet: false);
            }, TaskScheduler.FromCurrentSynchronizationContext());
        }

        private void SetBusy(bool busy, string text)
        {
            _busyLabel.Text = text;
            _lanToggle.Enabled = !busy;
            _btnRestart.Enabled = !busy;
            UseWaitCursor = busy;
        }

        private void OpenUrl(string url)
        {
            try { Process.Start(url); }
            catch (Exception ex)
            {
                MessageBox.Show(this, "打不开浏览器：" + ex.Message, "LAN Share",
                    MessageBoxButtons.OK, MessageBoxIcon.Warning);
            }
        }

        private void OpenFolder(string path)
        {
            try
            {
                if (!Directory.Exists(path)) Directory.CreateDirectory(path);
                Process.Start("explorer.exe", "\"" + path + "\"");
            }
            catch (Exception ex)
            {
                MessageBox.Show(this, "打不开目录：" + ex.Message, "LAN Share",
                    MessageBoxButtons.OK, MessageBoxIcon.Warning);
            }
        }

        // ──────────────────────────────────────────────────────
        // JSON 取值辅助（JavaScriptSerializer 返回的是 object，要逐层转）
        // ──────────────────────────────────────────────────────

        private static string Str(Dictionary<string, object> d, string key, string fallback = "")
        {
            if (d == null || !d.ContainsKey(key) || d[key] == null) return fallback;
            return Convert.ToString(d[key], CultureInfo.InvariantCulture);
        }

        private static int Int(Dictionary<string, object> d, string key, int fallback = 0)
        {
            if (d == null || !d.ContainsKey(key) || d[key] == null) return fallback;
            try { return Convert.ToInt32(d[key], CultureInfo.InvariantCulture); }
            catch { return fallback; }
        }

        private static bool Bool(Dictionary<string, object> d, string key, bool fallback = false)
        {
            if (d == null || !d.ContainsKey(key) || d[key] == null) return fallback;
            try { return Convert.ToBoolean(d[key], CultureInfo.InvariantCulture); }
            catch { return fallback; }
        }

        private static List<object> List(Dictionary<string, object> d, string key)
        {
            var result = new List<object>();
            if (d == null || !d.ContainsKey(key) || d[key] == null) return result;

            // ⚠️ JavaScriptSerializer 解析 JSON 数组返回的是 ArrayList，
            // 不是 List<object>。直接 as List<object> 会得到 null，
            // 表现成「数组永远为空」—— 状态区显示「未监听」但服务明明在跑。
            var list = d[key] as System.Collections.IEnumerable;
            if (list == null || d[key] is string) return result;

            foreach (var item in list) result.Add(item);
            return result;
        }
    }
}
