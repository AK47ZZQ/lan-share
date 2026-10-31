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
using System.Runtime.InteropServices;
using System.ServiceProcess;
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
        private readonly int _apiPort;

        public CliRunner(string installDir, string python, string servicePrefix, int apiPort)
        {
            _installDir = installDir;
            _python = python;
            _servicePrefix = servicePrefix;
            _apiPort = apiPort;
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

        /// <summary>GET 本机端点，返回解析后的字典。失败返回 null 并给出 error。</summary>
        public Dictionary<string, object> GetLocal(string path, int timeoutMs, out string error)
        {
            return RequestLocal("GET", path, null, timeoutMs, out error);
        }

        /// <summary>POST 本机端点。payload 为 JSON 字符串。</summary>
        public Dictionary<string, object> PostLocal(string path, string jsonBody, int timeoutMs, out string error)
        {
            return RequestLocal("POST", path, jsonBody, timeoutMs, out error);
        }

        private Dictionary<string, object> RequestLocal(
            string method, string path, string jsonBody, int timeoutMs, out string error)
        {
            error = null;
            string url = "http://127.0.0.1:" + _apiPort + path;

            try
            {
                var req = (HttpWebRequest)WebRequest.Create(url);
                req.Method = method;
                req.Timeout = timeoutMs;
                req.ReadWriteTimeout = timeoutMs;
                req.Proxy = null;
                req.UserAgent = "LanSharePanel";

                if (jsonBody != null)
                {
                    byte[] payload = Encoding.UTF8.GetBytes(jsonBody);
                    req.ContentType = "application/json; charset=utf-8";
                    req.ContentLength = payload.Length;
                    using (var s = req.GetRequestStream())
                    {
                        s.Write(payload, 0, payload.Length);
                    }
                }

                using (var resp = (HttpWebResponse)req.GetResponse())
                using (var reader = new StreamReader(resp.GetResponseStream(), Encoding.UTF8))
                {
                    string text = reader.ReadToEnd();
                    var serializer = new JavaScriptSerializer { MaxJsonLength = int.MaxValue };
                    return serializer.Deserialize<Dictionary<string, object>>(text);
                }
            }
            catch (WebException wex)
            {
                // 403/422/500 都带 JSON 响应体，把 detail 捞出来给用户看
                var httpResp = wex.Response as HttpWebResponse;
                if (httpResp != null)
                {
                    try
                    {
                        using (var reader = new StreamReader(
                                   httpResp.GetResponseStream(), Encoding.UTF8))
                        {
                            string text = reader.ReadToEnd();
                            var serializer = new JavaScriptSerializer();
                            var dict = serializer.Deserialize<Dictionary<string, object>>(text);
                            if (dict != null && dict.ContainsKey("detail"))
                            {
                                error = Convert.ToString(dict["detail"]);
                                return null;
                            }
                            error = text.Length > 300 ? text.Substring(0, 300) : text;
                        }
                    }
                    catch (Exception)
                    {
                        error = "HTTP " + (int)httpResp.StatusCode;
                    }
                    finally
                    {
                        httpResp.Close();
                    }
                    return null;
                }
                error = wex.Status == WebExceptionStatus.Timeout
                    ? "请求超时（" + (timeoutMs / 1000) + " 秒）"
                    : "连不上本机服务：" + wex.Message;
                return null;
            }
            catch (Exception ex)
            {
                error = ex.Message;
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

        // 服务卡片：api / caddy / (postgresql)
        // 生产环境三个服务，安装版只有两个 —— 按 HasDatabaseService 动态显示
        private readonly Card _svcBox;
        private readonly Label[] _svcName = new Label[3];
        private readonly Label[] _svcInfo = new Label[3];
        private readonly Button[] _svcRestart = new Button[3];
        private readonly Label _webLine;   // 不再单独显示，API/Web 已合并进 _apiLine

        // 局域网区
        private readonly Panel _lanBox;
        private readonly Label _lanState;
        private readonly Label _lanHint;
        private readonly TextBox _lanAddress;
        private readonly Button _lanCopy;
        private readonly Button _lanToggle;
        private readonly PictureBox _qrBox;
        private readonly Label _qrHint;

        // 编号
        private readonly Label _dataLabel;
        private readonly Button _btnOpenWeb;
        private readonly Button _btnRestart;
        private readonly Button _btnLogs;

        // 公网访问区
        private readonly Panel _wanBox;
        private readonly Label _wanState;
        private readonly Label _wanHint;
        private readonly TextBox _wanInput;
        private readonly Button _wanSave;
        private readonly Button _wanTest;
        private readonly Button _wanClear;
        private readonly Button _wanHelp;
        private readonly TextBox _wanAddress;
        private readonly Button _wanCopy;
        private string _wanConfigured = "";

        private readonly System.Windows.Forms.Timer _refreshTimer;
        private readonly Label _busyLabel;

        private bool _busy;
        private string _lastLanState = "";
        private bool _dpiApplied;
        private int _wanTick;
        private DateTime _lastRefresh = DateTime.MinValue;

        public LanSharePanel(InstallConfig cfg, CliRunner cli)
        {
            _cfg = cfg;
            _cli = cli;

            Text = "LAN Share";
            FormBorderStyle = FormBorderStyle.FixedSingle;
            MaximizeBox = false;
            StartPosition = FormStartPosition.CenterScreen;
            // WinForms 标准 DPI 机制：按 96 DPI 设计，框架按实际 DPI 缩放。
            // 手工 Scale + SetWindowPos 试过几轮都对不齐 —— 那是和框架内部的
            // 缩放叠加，成了两层缩放。标准机制反而是对的。
            AutoScaleMode = AutoScaleMode.None;
            ClientSize = new Size(520, 910);
            Font = Theme.Body;
            BackColor = Theme.Bg;
            Icon = Theme.AppIcon;

            const int P = Theme.Pad;        // 窗口左右边距 24
            const int CW = 472;             // 卡片宽 = 520 - 2*24
            const int Inner = Theme.CardPad; // 卡片内边距 16

            // ───────── 顶部：标题 + 服务状态 ─────────
            var title = new Label
            {
                Text = "LAN Share",
                Font = Theme.H1,
                Location = new Point(P, 18),
                AutoSize = true,
                ForeColor = Theme.TextStrong,
            };
            Controls.Add(title);

            _serviceStatus = new Label
            {
                Text = "检查中…",
                Font = Theme.Status,
                Location = new Point(P, 54),
                AutoSize = true,
                ForeColor = Theme.Gray,
            };
            Controls.Add(_serviceStatus);

            // API/Web 合并成一行元信息 —— 原来两行各占 22px，
            // 而且字号和正文一样大，把「状态」这个主角挤下去了
            _apiLine = new Label
            {
                Text = "",
                Font = Theme.Meta,
                Location = new Point(P, 84),
                Size = new Size(CW, 18),
                ForeColor = Theme.TextMuted,
            };
            Controls.Add(_apiLine);

            // _webLine 保留字段（ApplyStatus 会写），但不单独显示：
            // 内容拼进 _apiLine，两者同步
            _webLine = new Label { Visible = false, Text = "" };
            Controls.Add(_webLine);

            // ───────── 服务卡片 ─────────
            // 生产环境跑三个服务：api / caddy / postgresql。
            // 不显示 PG 的话，数据库挂了用户完全看不出来。
            _svcBox = new Card(P, 86, CW, 150);
            Controls.Add(_svcBox);

            var svcTitle = new Label
            {
                Text = "服务",
                Font = Theme.SectionTitle,
                Location = new Point(Inner, 12),
                AutoSize = true,
                ForeColor = Theme.TextMuted,
            };
            _svcBox.Controls.Add(svcTitle);

            string[] svcKeys = { "API", "Caddy", "数据库" };
            for (int i = 0; i < 3; i++)
            {
                int rowY = 38 + i * 32;

                _svcName[i] = new Label
                {
                    Text = "●  " + svcKeys[i],
                    Font = Theme.Body,
                    Location = new Point(Inner, rowY),
                    Size = new Size(96, 22),
                    ForeColor = Theme.Gray,
                };
                _svcBox.Controls.Add(_svcName[i]);

                _svcInfo[i] = new Label
                {
                    Text = "—",
                    Font = Theme.Meta,
                    // 起点避开左侧服务名（96 宽 + 留白），宽度够放
                    // 「0.0.0.0:8765  ·  Running」这种最长组合
                    Location = new Point(Inner + 104, rowY + 3),
                    Size = new Size(250, 18),
                    ForeColor = Theme.TextMuted,
                };
                _svcBox.Controls.Add(_svcInfo[i]);

                int captured = i;
                _svcRestart[i] = Btn.Make("重启", ButtonKind.Secondary,
                    CW - Inner - 60, rowY - 2, 60, 24);
                _svcRestart[i].Font = Theme.Meta;
                _svcRestart[i].Click += delegate { RestartOneService(captured); };
                _svcBox.Controls.Add(_svcRestart[i]);
            }

            // ───────── 局域网卡片 ─────────
            _lanBox = new Card(P, 250, CW, 300);
            Controls.Add(_lanBox);

            var lanTitle = new Label
            {
                Text = "局域网访问",
                Font = Theme.SectionTitle,
                Location = new Point(Inner, 18),
                AutoSize = true,
                ForeColor = Theme.TextMuted,
            };
            _lanBox.Controls.Add(lanTitle);

            _lanState = new Label
            {
                Text = "读取中…",
                Font = Theme.Status,
                Location = new Point(Inner, 42),
                Size = new Size(CW - Inner * 2, 26),
                ForeColor = Theme.Gray,
            };
            _lanBox.Controls.Add(_lanState);

            _lanHint = new Label
            {
                Text = "",
                Font = Theme.Meta,
                Location = new Point(Inner, 72),
                Size = new Size(CW - Inner * 2, 38),
                ForeColor = Theme.TextMuted,
            };
            _lanBox.Controls.Add(_lanHint);

            // 地址 + 复制：贴在一起，形成「内容 + 操作」的组合
            _lanAddress = Btn.Address(Inner, 118, 352, 28);
            _lanAddress.Visible = false;
            _lanBox.Controls.Add(_lanAddress);

            _lanCopy = Btn.Make("复制", ButtonKind.Secondary, Inner + 352 + 8, 118, 80, 28);
            _lanCopy.Visible = false;
            _lanCopy.Click += delegate { CopyAddress(); };
            _lanBox.Controls.Add(_lanCopy);

            // 二维码和说明文字垂直居中：QR 96 高，文字单行约 18，
            // 所以文字 y = QR.y + (96-18)/2
            _qrBox = new PictureBox
            {
                Location = new Point(Inner, 158),
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
                Font = Theme.Meta,
                Location = new Point(Inner + 96 + 18, 158 + 16),
                Size = new Size(220, 18),
                ForeColor = Theme.TextMuted,
                Visible = false,
            };
            _lanBox.Controls.Add(_qrHint);

            // 关闭局域网是破坏性操作 —— 红色，且不抢主视觉
            _lanToggle = Btn.Make("启动局域网服务", ButtonKind.Primary, Inner, 158, 168, 34);
            _lanToggle.Click += delegate { ToggleLan(); };
            _lanBox.Controls.Add(_lanToggle);

            // ───────── 公网卡片 ─────────
            _wanBox = new Card(P, 564, CW, 200);
            Controls.Add(_wanBox);

            var wanTitle = new Label
            {
                Text = "公网访问",
                Font = Theme.SectionTitle,
                Location = new Point(Inner, 18),
                AutoSize = true,
                ForeColor = Theme.TextMuted,
            };
            _wanBox.Controls.Add(wanTitle);

            // 「怎么配置？」改成文字链样式，右对齐贴住卡片右边缘，
            // 视觉上是「标题行的附属链接」而不是一个独立按钮
            _wanHelp = Btn.Make("怎么配置？", ButtonKind.Link, CW - Inner - 104, 14, 104, 24);
            _wanHelp.Font = Theme.Meta;
            _wanHelp.Click += delegate { ShowWanHelp(); };
            _wanBox.Controls.Add(_wanHelp);

            _wanState = new Label
            {
                Text = "读取中…",
                Font = Theme.Status,
                Location = new Point(Inner, 42),
                Size = new Size(CW - Inner * 2, 26),
                ForeColor = Theme.Gray,
            };
            _wanBox.Controls.Add(_wanState);

            _wanHint = new Label
            {
                Text = "",
                Font = Theme.Meta,
                Location = new Point(Inner, 72),
                Size = new Size(CW - Inner * 2, 40),
                ForeColor = Theme.TextMuted,
            };
            _wanBox.Controls.Add(_wanHint);

            // 公网地址也用只读框 —— 原来它是纯文本，和上面的局域网地址长得不一样，
            // 用户会以为这个不能复制
            _wanAddress = Btn.Address(Inner, 118, 352, 28);
            _wanAddress.Visible = false;
            _wanBox.Controls.Add(_wanAddress);

            _wanCopy = Btn.Make("复制", ButtonKind.Secondary, Inner + 352 + 8, 118, 80, 28);
            _wanCopy.Visible = false;
            _wanCopy.Click += delegate { CopyWan(); };
            _wanBox.Controls.Add(_wanCopy);

            _wanInput = new TextBox
            {
                Location = new Point(Inner, 118),
                Size = new Size(352, 28),
                BorderStyle = BorderStyle.FixedSingle,
                Font = Theme.Mono9,
                BackColor = Theme.FieldBg,
            };
            _wanBox.Controls.Add(_wanInput);

            _wanSave = Btn.Make("保存", ButtonKind.Primary, Inner + 352 + 8, 118, 80, 28);
            _wanSave.Click += delegate { SaveWan(); };
            _wanBox.Controls.Add(_wanSave);

            _wanTest = Btn.Make("验证连通性", ButtonKind.Secondary, Inner, 158, 112, 28);
            _wanTest.Visible = false;
            _wanTest.Click += delegate { TestWan(); };
            _wanBox.Controls.Add(_wanTest);

            _wanClear = Btn.Make("清除", ButtonKind.Danger, Inner + 120, 158, 72, 28);
            _wanClear.Visible = false;
            _wanClear.Click += delegate { ClearWan(); };
            _wanBox.Controls.Add(_wanClear);

            // ───────── 数据目录（可点击，替代原来重复的「数据目录」按钮）─────────
            _dataLabel = new Label
            {
                Text = "",
                Font = Theme.Meta,
                Location = new Point(P, 778),
                Size = new Size(CW, 18),
                ForeColor = Theme.TextMuted,
                Cursor = Cursors.Hand,
            };
            _dataLabel.Click += delegate { OpenFolder(_cfg.DataFolder); };
            Controls.Add(_dataLabel);

            // ───────── 底部操作 ─────────
            // 去掉「数据目录」按钮：上面那行路径已经能点，两个入口做同一件事
            // 只会让用户猜「是不是不一样」
            _btnOpenWeb = Btn.Make("打开网盘", ButtonKind.Primary, P, 806, 112, 34);
            _btnOpenWeb.Click += delegate { OpenUrl(_cfg.WebUrl); };
            Controls.Add(_btnOpenWeb);

            _btnRestart = Btn.Make("重启服务", ButtonKind.Danger, P + 120, 806, 112, 34);
            _btnRestart.Click += delegate { RestartServices(); };
            Controls.Add(_btnRestart);

            // 「数据目录」按钮删了：上面那行路径已经能点，两个入口做同一件事
            // 只会让用户猜「是不是不一样」。按钮位置重排成三个。
            _btnLogs = Btn.Make("看日志", ButtonKind.Secondary, P + 240, 806, 100, 34);
            _btnLogs.Click += delegate { OpenFolder(_cfg.LogFolder); };
            Controls.Add(_btnLogs);

            // ───────── 忙碌提示 ─────────
            _busyLabel = new Label
            {
                Text = "",
                Font = Theme.Meta,
                Location = new Point(P, 854),
                Size = new Size(CW, 18),
                ForeColor = Theme.Blue,
            };
            Controls.Add(_busyLabel);

            // ───────── 页脚 ─────────
            var version = new Label
            {
                Text = "服务前缀 " + _cfg.ServicePrefix,
                Location = new Point(P, 886),
                AutoSize = true,
                ForeColor = Theme.TextFaint,
                Font = Theme.Footer,
            };
            Controls.Add(version);

            // 每 5 秒刷新一次；窗口最小化时也刷新，因为托盘状态要同步
            _refreshTimer = new System.Windows.Forms.Timer { Interval = 5000 };
            _refreshTimer.Tick += delegate
            {
                RefreshStateAsync(quiet: true);
                // 公网配置是低频的，30 秒刷一次就够（每 6 次 tick 触发一次）
                _wanTick = (_wanTick + 1) % 6;
                if (_wanTick == 0) RefreshWanAsync();
            };
            _refreshTimer.Start();

            Shown += delegate
            {
                // Shown 之后 DeviceDpi 才是真实显示器的 DPI。
                // OnLoad 时它恒为 96，在那儿缩放等于没做。
                ApplyDpiScale();
                RefreshStateAsync(quiet: false);
                RefreshWanAsync();
            };
            FormClosed += delegate { _refreshTimer.Stop(); _qrBox.Image = null; };
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
                SetServiceBadge("状态未知", Theme.Gray);
                _apiLine.Text = error ?? "无法读取状态";
                _apiLine.ForeColor = Theme.Red;
                UpdateServiceRows(null);
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

            // Web 那行显示真实监听范围，这比「RUNNING」有信息量
            string bindText = "未监听";
            foreach (var a in listening)
            {
                string s = Convert.ToString(a);
                if (s == "0.0.0.0" || s == "::") bindText = "0.0.0.0:" + webPort + "（所有网卡）";
                else if (s == "127.0.0.1") bindText = "127.0.0.1:" + webPort + "（仅本机）";
                else if (bindText.IndexOf(':') < 0) bindText = s + ":" + webPort;
            }

            // API 和 Web 合并成一行元信息：原来各占一行 22px 且字号和正文一样大，
            // 把「服务正常」这个真正的主角挤下去了
            _apiLine.Text = "API 127.0.0.1:" + apiPort + "  ·  Web " + bindText;
            _apiLine.ForeColor = Theme.TextMuted;

            UpdateLanSection(dict, state, probeOk, probeMsg, quiet);
            UpdateServiceRows(dict);
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
                _lanHint.ForeColor = Theme.TextMuted;
            }
            else
            {
                _lanHint.Text = "现在只有这台电脑能访问。开启后同一个 WiFi 下的手机也能用";
                _lanHint.ForeColor = Theme.TextMuted;
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
                // 地址框和复制按钮同高同基线，不再手动 -1 微调
                _lanCopy.Top = _lanAddress.Top;

                // 开着的时候这个按钮是「关闭」——破坏性操作，换成红色并缩小占位，
                // 别让它跟「打开网盘」那样抢主视觉
                _lanToggle.Text = "关闭局域网";
                // 右对齐到卡片内容右边缘（和上面「复制」按钮共用一条右基线 456），
                // 二维码在左、关闭在右，整行留白才算均匀
                _lanToggle.Location = new Point(472 - Theme.CardPad - 130, 200);
                _lanToggle.Size = new Size(130, 32);
                Btn.Restyle(_lanToggle, ButtonKind.Danger);

                LoadQrAsync(url, quiet && wasOn);
            }
            else
            {
                // 关着的时候这是主操作，蓝色、更大、和二维码同一个位置
                _lanToggle.Text = "启动局域网服务";
                _lanToggle.Location = new Point(Theme.CardPad, 158);
                _lanToggle.Size = new Size(180, 34);
                Btn.Restyle(_lanToggle, ButtonKind.Primary);

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

        // ──────────────────────────────────────────────────────
        // 公网访问
        // ──────────────────────────────────────────────────────

        /// <summary>读当前公网配置并更新界面。走 /local/public-url（本机免认证）。</summary>
        private void RefreshWanAsync()
        {
            Task.Run(() =>
            {
                string error;
                var dict = _cli.GetLocal("/local/public-url", 8000, out error);
                return new object[] { dict, error };
            }).ContinueWith(t =>
            {
                if (IsDisposed) return;
                var result = t.Result;
                ApplyWanState(
                    (Dictionary<string, object>)result[0],
                    (string)result[1]);
            }, TaskScheduler.FromCurrentSynchronizationContext());
        }

        private void ApplyWanState(Dictionary<string, object> dict, string error)
        {
            if (dict == null)
            {
                _wanState.Text = "○  读不到配置";
                _wanState.ForeColor = Theme.Red;
                _wanHint.Text = error ?? "服务可能没在运行";
                _wanHint.ForeColor = Theme.Red;
                _wanAddress.Visible = false;
                _wanCopy.Visible = false;
                return;
            }

            bool available = Bool(dict, "available", true);
            if (!available)
            {
                _wanState.Text = "○  读不到配置";
                _wanState.ForeColor = Theme.Red;
                _wanHint.Text = Str(dict, "error");
                _wanHint.ForeColor = Theme.Red;
                _wanAddress.Visible = false;
                _wanCopy.Visible = false;
                return;
            }

            string url = Str(dict, "url");
            _wanConfigured = url;

            if (url.Length == 0)
            {
                _wanState.Text = "○  未配置";
                _wanState.ForeColor = Color.Gray;
                _wanHint.Text = "局域网外的设备要访问，需要先用内网穿透工具"
                              + "（如 sakuraFrp）把本机端口映射出去，拿到公网地址后填在这里";
                _wanHint.ForeColor = Color.FromArgb(110, 114, 130);
                _wanHint.Height = 40;

                _wanInput.Visible = true;
                _wanSave.Visible = true;
                _wanAddress.Visible = false;
                _wanCopy.Visible = false;
                _wanTest.Visible = false;
                _wanClear.Visible = false;
                if (_wanInput.Text.Length == 0)
                {
                    _wanInput.ForeColor = Color.Gray;
                }
            }
            else
            {
                _wanState.Text = "●  已配置";
                _wanState.ForeColor = Theme.Green;
                _wanHint.Text = "局域网外可通过这个地址访问";
                _wanHint.ForeColor = Theme.TextMuted;
                _wanHint.Height = 20;

                // 地址放进只读框，和上面的局域网地址长得一样 ——
                // 纯文本会让人以为不能复制
                if (_wanAddress.Text != url) _wanAddress.Text = url;

                _wanInput.Visible = false;
                _wanSave.Visible = false;
                _wanAddress.Visible = true;
                _wanCopy.Visible = true;
                _wanTest.Visible = true;
                _wanClear.Visible = true;
            }
        }

        private void SaveWan()
        {
            string url = _wanInput.Text.Trim();
            if (url.Length == 0)
            {
                MessageBox.Show(this, "先填一个公网地址，比如 xxx.frp-any.com:35391",
                    "LAN Share", MessageBoxButtons.OK, MessageBoxIcon.Information);
                return;
            }

            _wanSave.Enabled = false;
            _wanSave.Text = "保存中";

            string body = "{\"url\":" + JsonString(url) + ",\"url_alt\":\"\"}";

            Task.Run(() =>
            {
                string error;
                var dict = _cli.PostLocal("/local/public-url", body, 15000, out error);
                return new object[] { dict, error };
            }).ContinueWith(t =>
            {
                if (IsDisposed) return;
                _wanSave.Enabled = true;
                _wanSave.Text = "保存";

                var result = t.Result;
                var dict = (Dictionary<string, object>)result[0];
                var error = (string)result[1];

                if (dict == null)
                {
                    MessageBox.Show(this,
                        "保存失败：\n\n" + (error ?? "未知错误"),
                        "LAN Share", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                    return;
                }
                RefreshWanAsync();
                _busyLabel.Text = "公网地址已保存";
            }, TaskScheduler.FromCurrentSynchronizationContext());
        }

        private void ClearWan()
        {
            var confirm = MessageBox.Show(this,
                "清除后公网访问配置就没了，局域网访问不受影响。\n\n确定清除吗？",
                "清除公网配置", MessageBoxButtons.YesNo, MessageBoxIcon.Question);
            if (confirm != DialogResult.Yes) return;

            Task.Run(() =>
            {
                string error;
                var dict = _cli.PostLocal(
                    "/local/public-url", "{\"url\":\"\",\"url_alt\":\"\"}", 15000, out error);
                return new object[] { dict, error };
            }).ContinueWith(t =>
            {
                if (IsDisposed) return;
                RefreshWanAsync();
            }, TaskScheduler.FromCurrentSynchronizationContext());
        }

        /// <summary>
        /// 用配置的公网地址请求一次，确认这条路真的通。
        ///
        /// ⚠️ 不能探 /healthz —— 服务端**故意**让它在公网入口返回 404
        /// （Caddyfile 里写着「healthz 不对公网暴露」，防止被扫描器探测）。
        /// 探 /healthz 会把「配好了」误判成「没通」。
        ///
        /// 改探根路径 /：那是登录页，公网本来就要能打开。
        /// 判定标准也放宽：拿到任何 HTTP 响应就算链路通，
        /// 具体状态码只用来区分「服务正常」和「服务在但有问题」。
        /// </summary>
        private void TestWan()
        {
            if (_wanConfigured.Length == 0) return;

            _wanTest.Enabled = false;
            _wanTest.Text = "验证中…";
            string target = _wanConfigured.TrimEnd('/') + "/";

            // 公网入口是自签证书，先把目标主机临时加入信任（60 秒后自动过期）
            try
            {
                var uri = new Uri(target);
                TrustedHosts.Add(uri.Host, 60);
            }
            catch (Exception) { }

            Task.Run(() =>
            {
                try
                {
                    var req = (HttpWebRequest)WebRequest.Create(target);
                    req.Timeout = 12000;
                    req.ReadWriteTimeout = 12000;
                    req.Proxy = null;
                    req.UserAgent = "LanSharePanel";
                    // 允许自动跟随跳转（登录页可能 302 到 /login）
                    req.AllowAutoRedirect = false;

                    using (var resp = (HttpWebResponse)req.GetResponse())
                    {
                        return "HTTP " + (int)resp.StatusCode;
                    }
                }
                catch (WebException wex)
                {
                    var httpResp = wex.Response as HttpWebResponse;
                    if (httpResp != null)
                    {
                        int code = (int)httpResp.StatusCode;
                        httpResp.Close();
                        return "HTTP " + code;
                    }
                    return "连不上：" + wex.Status;
                }
                catch (Exception ex)
                {
                    return "连不上：" + ex.Message;
                }
            }).ContinueWith(t =>
            {
                if (IsDisposed) return;
                _wanTest.Enabled = true;
                _wanTest.Text = "验证连通性";

                string outcome = t.Result;

                if (outcome.StartsWith("HTTP "))
                {
                    int code;
                    int.TryParse(outcome.Substring(5), out code);

                    if (code >= 200 && code < 400)
                    {
                        MessageBox.Show(this,
                            "通了。\n\n" + target + "\n\n"
                            + "从外部网络访问这个地址就能打开网盘登录页。\n"
                            + "注意：如果是 HTTPS 自签证书，浏览器会提示不安全，点「继续访问」即可。",
                            "验证成功", MessageBoxButtons.OK, MessageBoxIcon.Information);
                    }
                    else
                    {
                        MessageBox.Show(this,
                            "服务器有响应，但返回 " + code + "。\n\n" + target + "\n\n"
                            + "链路本身是通的（说明隧道工作正常），"
                            + "但服务端拒绝了这个请求 —— 可能是隧道配置里的"
                            + "「本地端口」没指向 " + _cfg.WebPort + "，或者反向代理规则不对。",
                            "链路通，但服务端拒绝", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                    }
                }
                else
                {
                    MessageBox.Show(this,
                        "没通。" + outcome + "\n\n" + target + "\n\n"
                        + "常见原因：\n"
                        + "  1. 内网穿透客户端没启动\n"
                        + "  2. 隧道里的本地端口填错了（应该是 " + _cfg.WebPort + "）\n"
                        + "  3. 地址里的域名或端口抄错了\n"
                        + "  4. 穿透服务商那边隧道是停用状态",
                        "验证失败", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                }
            }, TaskScheduler.FromCurrentSynchronizationContext());
        }

        private void ShowWanHelp()
        {
            MessageBox.Show(this,
                "公网访问需要先做一步「内网穿透」，这一步必须在外部工具里完成，\n"
                + "程序没法自动帮你做（穿透服务商没有开放接口）。\n\n"
                + "以 sakuraFrp 为例：\n\n"
                + "  1. 注册账号（sakurafrp.com），要手机号\n"
                + "  2. 网页后台 → 隧道 → 新建隧道\n"
                + "      · 类型选 TCP 或 HTTPS\n"
                + "      · 本地地址填 127.0.0.1\n"
                + "      · 本地端口填 " + _cfg.WebPort + "\n"
                + "  3. 下载它的客户端，登录，启动这个隧道\n"
                + "  4. 客户端会给你一个地址，形如\n"
                + "       xxx.frp-any.com:35391\n"
                + "  5. 把那个地址填回来，点保存\n\n"
                + "注意：公网地址走 HTTPS 时，浏览器和系统会提示证书不受信任 ——\n"
                + "这是自签证书的正常现象，点「继续访问」即可。",
                "怎么配置公网访问", MessageBoxButtons.OK, MessageBoxIcon.Information);
        }

        /// <summary>把字符串转成 JSON 字面量（引号、反斜杠转义）。</summary>
        private static string JsonString(string raw)
        {
            var sb = new StringBuilder("\"");
            foreach (char c in raw ?? "")
            {
                switch (c)
                {
                    case '"': sb.Append("\\\""); break;
                    case '\\': sb.Append("\\\\"); break;
                    case '\n': sb.Append("\\n"); break;
                    case '\r': sb.Append("\\r"); break;
                    case '\t': sb.Append("\\t"); break;
                    default:
                        if (c < 32) sb.Append("\\u").Append(((int)c).ToString("x4"));
                        else sb.Append(c);
                        break;
                }
            }
            sb.Append("\"");
            return sb.ToString();
        }

        /// <summary>复制公网地址，给个即时反馈（否则用户不知道点没点上）。</summary>
        private void CopyWan()
        {
            TryCopy(_wanAddress.Text);
        }

        /// <summary>
        /// 按显示器实际 DPI 缩放窗口和所有子控件。
        ///
        /// 为什么不用 AutoScaleMode：
        ///   WinForms (.NET Framework 4.8) 对 Per-Monitor V2 支持不完整。
        ///   声明 PerMonitor 后，ClientSize 仍被当作物理像素，但系统又按
        ///   (96/显示器DPI) 缩一次 —— 520 变成 351，窗口小一圈、字被位图压糊、
        ///   Label 高度不够导致绿色状态文字被裁掉半截。
        ///
        /// 手动 Scale() + 重设 ClientSize 后：
        ///   144 DPI 屏上 520 设计单位 → 780 逻辑 → 物理正好 520×910，
        ///   字体由 GDI 按真实 DPI 渲染，清晰不糊。
        /// </summary>
        [DllImport("user32.dll")]
        private static extern bool SetWindowPos(IntPtr h, IntPtr after, int x, int y, int cx, int cy, uint flags);

        [DllImport("user32.dll")]
        private static extern uint GetDpiForWindow(IntPtr h);

        protected override void OnLoad(EventArgs e)
        {
            base.OnLoad(e);

            // OnLoad 阶段 DeviceDpi 还是 96（窗口尚未关联显示器），
            // 真正的 DPI 要等 Shown。这里只留痕，不做缩放。
            ApplyDpiScale();
        }

        /// <summary>
        /// 按显示器 DPI 把窗口和控件一起放大到物理尺寸。
        ///
        /// 关键顺序：Scale() 必须放在 ResumeLayout 之后。
        /// 放在 SuspendLayout 和 ResumeLayout 中间会被布局引擎吞掉，
        /// 表现就是「窗口变大了但控件还是原尺寸、底部空一大块」。
        /// </summary>
        private void ApplyDpiScale()
        {
            if (_dpiApplied) return;

            uint apiDpi = Handle == IntPtr.Zero ? 96u : GetDpiForWindow(Handle);
            if (apiDpi == 0) apiDpi = 96;

            float k = apiDpi / 96f;
            if (k <= 1.01f)
            {
                _dpiApplied = true;
                CenterOnScreen();
                return;
            }

            _dpiApplied = true;

            SuspendLayout();
            ResumeLayout(true);

            // 布局已经结算完，这时候 Scale 才会真正作用到控件上
            Scale(new SizeF(k, k));

            int wantW = (int)(520 * k);
            int wantH = (int)(910 * k);

            int borderW = Width - ClientSize.Width;
            int borderH = Height - ClientSize.Height;
            ClientSize = new Size(wantW, wantH);
            SetWindowPos(Handle, IntPtr.Zero, 0, 0,
                wantW + borderW, wantH + borderH,
                0x0002 | 0x0004);

            CenterOnScreen();
        }

        private void CenterOnScreen()
        {
            try
            {
                // 用 Bounds 而不是 WorkingArea：窗口高度 937 已经接近整屏 960，
                // 按工作区（扣掉任务栏约 900）居中会算出负的 Top，窗口顶到屏幕外。
                var area = Screen.FromHandle(Handle).Bounds;
                int x = area.Left + (area.Width - Width) / 2;
                int y = area.Top + (area.Height - Height) / 2;
                Location = new Point(Math.Max(area.Left, x), Math.Max(area.Top, y));
            }
            catch (Exception) { }
        }


        /// <summary>
        /// 刷新服务卡片的三行（api / caddy / 数据库）。
        ///
        /// 数据来源分两种：
        ///   api、caddy  —— 服务状态用 ServiceController，端口用 lan status 的 listening
        ///   数据库       —— SQLite 时压根没有服务，显示「内置」而不是显示红叉
        /// </summary>
        private void UpdateServiceRows(Dictionary<string, object> statusDict)
        {
            string[] names = { _cfg.ApiServiceName, _cfg.CaddyServiceName, _cfg.DbServiceName };
            string[] labels = { "API", "Caddy", "数据库" };

            // caddy 的监听地址从 lan status 里拿（可能是 :: 或 127.0.0.1）
            string caddyBind = "";
            if (statusDict != null)
            {
                foreach (var a in List(statusDict, "listening"))
                {
                    string s0 = Convert.ToString(a);
                    if (s0 == "0.0.0.0" || s0 == "::") { caddyBind = "0.0.0.0"; break; }
                    if (s0 == "127.0.0.1") caddyBind = "127.0.0.1";
                }
            }

            for (int i = 0; i < 3; i++)
            {
                bool isDb = (i == 2);

                // SQLite 模式下没有数据库服务，这一行说明情况就好
                if (isDb && !_cfg.HasDatabaseService)
                {
                    _svcName[i].Text = "○  " + labels[i];
                    _svcName[i].ForeColor = Theme.TextFaint;
                    _svcInfo[i].Text = "SQLite 内置，无需服务";
                    _svcRestart[i].Visible = false;
                    continue;
                }

                _svcRestart[i].Visible = true;

                string state = "未知";
                Color color = Theme.Gray;

                try
                {
                    using (var sc = new ServiceController(names[i]))
                    {
                        switch (sc.Status)
                        {
                            case ServiceControllerStatus.Running: state = "Running"; color = Theme.Green; break;
                            case ServiceControllerStatus.Stopped: state = "Stopped"; color = Theme.Red; break;
                            case ServiceControllerStatus.StartPending: state = "Starting"; color = Theme.Amber; break;
                            case ServiceControllerStatus.StopPending: state = "Stopping"; color = Theme.Amber; break;
                            case ServiceControllerStatus.Paused: state = "Paused"; color = Theme.Amber; break;
                            default: state = sc.Status.ToString(); color = Theme.Gray; break;
                        }
                    }
                }
                catch (Exception)
                {
                    state = "未安装";
                    color = Theme.TextFaint;
                }

                _svcName[i].Text = "●  " + labels[i];
                _svcName[i].ForeColor = color;

                string addr;
                if (i == 0) addr = "127.0.0.1:" + _cfg.ApiPort;
                else if (i == 1) addr = (caddyBind.Length > 0 ? caddyBind : "—") + ":" + _cfg.WebPort;
                else
                {
                    int pg = _cfg.PgPort;
                    addr = pg > 0 ? "127.0.0.1:" + pg : "端口未知";
                }

                _svcInfo[i].Text = addr + "  ·  " + state;
                _svcInfo[i].ForeColor = color == Theme.Green ? Theme.TextMuted : color;
            }
        }

        /// <summary>单独重启某一个服务。0=api 1=caddy 2=数据库。</summary>
        private void RestartOneService(int idx)
        {
            string[] names = { _cfg.ApiServiceName, _cfg.CaddyServiceName, _cfg.DbServiceName };
            string[] labels = { "API", "Caddy", "数据库" };

            if (idx == 2 && !_cfg.HasDatabaseService) return;

            string name = names[idx];

            SetBusy(true, "正在重启 " + labels[idx] + "…");

            Task.Run(() => ServiceControl.RestartOnePublic(name))
                .ContinueWith(t =>
                {
                    if (IsDisposed) return;
                    SetBusy(false, "");

                    string err = t.Result;
                    if (err != null)
                    {
                        MessageBox.Show(this, err, "重启失败",
                            MessageBoxButtons.OK, MessageBoxIcon.Warning);
                    }
                    else
                    {
                        _busyLabel.Text = labels[idx] + " 已重启";
                        var timer = new System.Windows.Forms.Timer { Interval = 2500 };
                        timer.Tick += delegate { timer.Stop(); timer.Dispose(); _busyLabel.Text = ""; };
                        timer.Start();
                    }

                    RefreshStateAsync(quiet: false);
                }, TaskScheduler.FromCurrentSynchronizationContext());
        }

        private void CopyAddress()
        {
            TryCopy(_lanAddress.Text);
        }

        /// <summary>复制到剪贴板，并在底部给 2 秒反馈。</summary>
        private void TryCopy(string text)
        {
            if (string.IsNullOrEmpty(text)) return;
            try
            {
                Clipboard.SetText(text);
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
