// 服务控制 + 安装信息读取
//
// 拆成独立文件的原因：托盘和主窗口都要用，放任何一边都别扭；
// 而且这两块最容易出错（服务名、注册表键名），集中一处好维护。
//
// 关于服务名的坑：NSSM 注册的服务名大小写不敏感但**不能有歧义**。
// 默认前缀是 lanshare，但已有部署用的是 lan-share，所以这里读注册表
// 的 ServicePrefix，读不到再按已知候选探测，最后才回退默认值。

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.ServiceProcess;
using System.Threading;
using Microsoft.Win32;

namespace LanShare
{
    /// <summary>
    /// 一键更新的「过场状态」文件。
    ///
    /// 为什么需要：更新时托盘要主动退出（让出文件句柄给安装器），而安装器是
    /// /VERYSILENT 静默跑的，约 40 秒。这段时间屏幕上什么都不会发生 —— 用户点完
    /// 「立即更新」只看到图标消失、过一会儿图标又回来，完全不知道中间干了什么。
    /// 2026-11-04 实机反馈就是：「点立即更新后直接就程序退出了，然后没有任何动静」。
    ///
    /// 于是在发起更新前留一个标记，托盘下次启动读到它就知道「我刚是被更新关掉的」，
    /// 从而把结果说出来：成功了报新版本并直接打开面板，失败了说失败。
    /// </summary>
    internal static class UpdateMarker
    {
        private static string FilePath
        {
            get
            {
                string dir = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                    "LANShare");
                return Path.Combine(dir, "update-in-progress.txt");
            }
        }

        /// <summary>
    /// 现在是不是还在一次更新里？5 分钟没结果就算过期。
    ///
    /// 判据是标记文件还在（发起更新时写、托盘启动复述结果时才删）。
    /// 用途：安装器跑着的时候，各路「检查更新」必须闭嘴 —— 否则它读到的
    /// 还是旧 VERSION，于是又提示有新版本、用户再点一次、装第二遍。
    /// 2026-11-04 实机就装了两遍。
    /// </summary>
    public static bool IsPending()
    {
        try
        {
            if (!File.Exists(FilePath)) return false;
            if (File.GetLastWriteTimeUtc(FilePath) < DateTime.UtcNow.AddMinutes(-5))
                return false;
            return true;
        }
        catch (Exception)
        {
            return false;
        }
    }

    /// <summary>发起更新前调用。targetVersion 是我们期望装成的版本。</summary>
        public static void Write(string targetVersion)
        {
            try
            {
                Directory.CreateDirectory(Path.GetDirectoryName(FilePath));
                File.WriteAllText(FilePath, (targetVersion ?? "") + "\n"
                                            + DateTime.Now.ToString("o"));
            }
            catch (Exception)
            {
                // 标记写不进去不能挡住更新本身
            }
        }

        /// <summary>
        /// 读取并删除标记，返回 [目标版本, 时间戳]。没标记返回 null。
        /// 一次性的：读完即删，避免下次正常启动还误报「刚更新过」。
        /// </summary>
        public static string[] Take()
        {
            try
            {
                if (!File.Exists(FilePath)) return null;
                string[] lines = File.ReadAllLines(FilePath);
                File.Delete(FilePath);
                return lines;
            }
            catch (Exception)
            {
                return null;
            }
        }
    }

    /// <summary>安装信息，优先注册表，其次按已知约定探测。</summary>
    internal sealed class InstallConfig
    {
        public string InstallFolder { get; private set; }
        public string DataFolder { get; private set; }
        public int WebPort { get; private set; }
        public int ApiPort { get; private set; }
        public string ServicePrefix { get; private set; }
        public string Version { get; private set; }
        public bool Installed { get; private set; }

        public static InstallConfig Load()
        {
            var cfg = new InstallConfig
            {
                WebPort = 8765,
                ApiPort = 8766,
                InstallFolder = "",
                DataFolder = "",
                ServicePrefix = "",
                Version = "",
                Installed = false
            };

            try
            {
                using (var key = Registry.LocalMachine.OpenSubKey(@"SOFTWARE\LANShare"))
                {
                    if (key != null)
                    {
                        // InstallFolder 是安装器写的；Install 是早期名字，两个都认
                        cfg.InstallFolder = (key.GetValue("InstallFolder") as string)
                                            ?? (key.GetValue("Install") as string)
                                            ?? "";
                        cfg.DataFolder = (key.GetValue("DataFolder") as string) ?? "";
                        cfg.WebPort = ParseInt(key.GetValue("WebPort") as string, 8765);
                        cfg.ApiPort = ParseInt(key.GetValue("ApiPort") as string, 8766);
                        cfg.ServicePrefix = (key.GetValue("ServicePrefix") as string) ?? "";
                        cfg.Version = (key.GetValue("Version") as string) ?? "";
                        cfg.Installed = !string.IsNullOrEmpty(cfg.InstallFolder);
                    }
                }
            }
            catch (Exception)
            {
                // 注册表读不到不算错误，后面靠探测兜底
            }

            // 没注册表（开发态 / 绿色版）：用 exe 所在目录的上一级当安装根
            if (string.IsNullOrEmpty(cfg.InstallFolder))
            {
                try
                {
                    var exeDir = AppDomain.CurrentDomain.BaseDirectory;
                    // exe 通常在 <root>\tray\ 下，上一级就是 root
                    var parent = Directory.GetParent(exeDir.TrimEnd('\\'));
                    if (parent != null && File.Exists(Path.Combine(parent.FullName, "server.py")))
                    {
                        cfg.InstallFolder = parent.FullName;
                    }
                    else if (File.Exists(Path.Combine(exeDir, "server.py")))
                    {
                        cfg.InstallFolder = exeDir.TrimEnd('\\');
                    }
                }
                catch (Exception) { }
            }

            if (string.IsNullOrEmpty(cfg.DataFolder) && !string.IsNullOrEmpty(cfg.InstallFolder))
            {
                cfg.DataFolder = Path.Combine(cfg.InstallFolder, "data");
            }

            if (string.IsNullOrEmpty(cfg.ServicePrefix))
            {
                cfg.ServicePrefix = ServiceControl.DetectPrefix();
            }

            return cfg;
        }

        private static int ParseInt(string s, int fallback)
        {
            int v;
            return int.TryParse(s, out v) && v > 0 && v <= 65535 ? v : fallback;
        }

        /// <summary>
        /// 找 Python 运行时。
        ///
        /// 三种部署形态的路径完全不同：
        ///   安装版：{root}\runtime\python.exe       （embeddable）
        ///   开发版：{root}\.venv\Scripts\python.exe
        ///   绿色版：直接用 PATH 里的 python
        /// 只认第一种会让开发态和绿色版完全不可用。
        /// </summary>
        public string PythonPath
        {
            get
            {
                if (_pythonPath != null) return _pythonPath;

                var candidates = new List<string>();
                if (!string.IsNullOrEmpty(InstallFolder))
                {
                    candidates.Add(Path.Combine(InstallFolder, "runtime", "python.exe"));
                    candidates.Add(Path.Combine(InstallFolder, ".venv", "Scripts", "python.exe"));
                    candidates.Add(Path.Combine(InstallFolder, "venv", "Scripts", "python.exe"));
                }

                foreach (var c in candidates)
                {
                    if (File.Exists(c)) { _pythonPath = c; return _pythonPath; }
                }

                // 都没有就用 PATH 里的
                _pythonPath = "python";
                return _pythonPath;
            }
        }

        private string _pythonPath;

        // 3.72 起 Caddy 只监听 HTTPS（tls internal 自签证书）。
        // 这里拼 http 的话，「打开网盘」按钮点了没反应 —— 浏览器连
        // 8765 的明文端口，Caddy 直接不接。和 CLI 自检探针是同一个坑。
        //
        // 用 localhost 而不是 127.0.0.1：证书的站点地址来自
        // `caddyfile.py::default_hosts()`，那里以 "localhost" 打头。
        // 3.80 之前用 127.0.0.1，而它不在 SAN 里 —— 服务明明活着，
        // 托盘却一直 SecureChannelFailure，界面全红、局域网按钮点不动。
        // （3.80 起 default_hosts 也补了 127.0.0.1，两边都不指望对方。）
        public string WebUrl { get { return "https://localhost:" + WebPort + "/"; } }
        // 3.72 起 Caddy 只监听 HTTPS。拼 http 的话探针每次都连不上，
        // 托盘永远显示「服务未运行」，而且还要白等一个超时才切到 https ——
        // 这就是「点开界面时检测感觉特别慢」的真正原因。
        public string HealthUrl { get { return "https://localhost:" + WebPort + "/healthz"; } }
        public string LogFolder { get { return Path.Combine(InstallFolder, "logs"); } }
        public string ApiServiceName { get { return ServicePrefix + "-api"; } }
        public string CaddyServiceName { get { return ServicePrefix + "-caddy"; } }

        /// <summary>数据库服务名。安装版用 SQLite 时这个服务根本不存在。</summary>
        public string DbServiceName { get { return ServicePrefix + "-postgresql"; } }

        /// <summary>
        /// 数据库服务存不存在 —— 也就是「这台机器用的是 PostgreSQL 还是 SQLite」。
        ///
        /// 判断依据不是配置，是服务本身：安装包跑 SQLite，压根不装 PG 服务；
        /// 生产是手动搭的 PG 服务。查服务比读配置可靠，不会出现
        /// 「配置写着 PG 但服务没装」的鬼状态。
        /// </summary>
        public bool HasDatabaseService
        {
            get
            {
                try
                {
                    using (var sc = new ServiceController(DbServiceName))
                    {
                        var _ = sc.Status;   // 碰一下状态，不存在会抛
                        return true;
                    }
                }
                catch (Exception) { return false; }
            }
        }

        /// <summary>数据库引擎名，给界面显示用。</summary>
        public string DbEngineLabel
        {
            get { return HasDatabaseService ? "PostgreSQL" : "SQLite"; }
        }

        /// <summary>
        /// 从 postgresql.conf 读 PG 端口。读不到返回 0。
        ///
        /// 为什么去读配置文件而不是硬编码 5433：用户完全可能改过端口，
        /// 界面上显示错的端口比不显示更糟。
        /// </summary>
        public int PgPort
        {
            get
            {
                // 依次找 postgresql.conf 的几个落点。安装版和开发版布局不一样，
                // 而且 3.95 起 pgdata 从「数据目录里」搬到了「数据目录的同级」，
                // 少列一个候选就会在升级后的机器上读到 0 —— 于是「重启服务」
                // 没法等数据库真正可连，只能干等 30 秒再报超时。
                string[] candidates =
                {
                    // 3.95+：pgdata 在数据目录同级（不跟用户文件混在一起）
                    Path.Combine(
                        Path.GetDirectoryName(
                            DataFolder.TrimEnd('\\', '/')), "pgdata", "postgresql.conf"),
                    // 3.94 及以前：pgdata 就在数据目录里面
                    Path.Combine(DataFolder, "pgdata", "postgresql.conf"),
                    // 开发机布局
                    Path.Combine(InstallFolder, "env", "pginstall", "data", "postgresql.conf"),
                };

                foreach (string conf in candidates)
                {
                    if (string.IsNullOrEmpty(conf) || !File.Exists(conf)) continue;
                    try
                    {
                        foreach (string raw in File.ReadAllLines(conf))
                        {
                            string t = raw.Trim();
                            if (t.Length == 0 || t[0] == '#') continue;
                            if (!t.StartsWith("port", StringComparison.OrdinalIgnoreCase)) continue;

                            int eq = t.IndexOf('=');
                            if (eq < 0) continue;

                            string val = t.Substring(eq + 1).Trim();
                            int port;
                            if (int.TryParse(val, out port) && port > 0) return port;
                        }
                    }
                    catch (Exception) { }
                }
                return 0;
            }
        }
    }

    /// <summary>Windows 服务的启动/停止/查询。</summary>
    internal static class ServiceControl
    {
        /// <summary>探测本机实际存在的服务前缀。</summary>
        public static string DetectPrefix()
        {
            string[] candidates = { "lanshare", "lan-share", "LANShare", "LAN-Share" };

            // 先试试 OpenService —— 比枚举快，而且不受服务列表权限影响
            foreach (var p in candidates)
            {
                if (ServiceExists(p + "-caddy")) return p;
            }

            // 候选都没命中，枚举所有 -caddy 服务，取第一个
            try
            {
                foreach (var svc in ServiceController.GetServices())
                {
                    try
                    {
                        if (svc.ServiceName.EndsWith("-caddy", StringComparison.OrdinalIgnoreCase))
                        {
                            return svc.ServiceName.Substring(0, svc.ServiceName.Length - 6);
                        }
                    }
                    finally
                    {
                        svc.Dispose();
                    }
                }
            }
            catch (Exception) { }

            return "lanshare";
        }

        public static bool ServiceExists(string name)
        {
            try
            {
                using (var sc = new ServiceController(name))
                {
                    var _ = sc.Status;   // 访问 Status 才会真的去查，不存在抛异常
                    return true;
                }
            }
            catch (Exception)
            {
                return false;
            }
        }

        /// <summary>
        /// 按 数据库 → API → Web 的顺序重启，每一步都等到「端口真的在听」为止。
        ///
        /// 3.94 及以前这里只重启了 caddy 和 api，压根没碰 PG。用户点「关闭服务」
        /// 把三个服务一起停了，再点「重启」时 PG 永远躺平 —— API 一启动就连库，
        /// 等 30 秒 psycopg ConnectionTimeout 直接崩，Caddy 反代过去得到 502。
        /// 界面还显示「重启成功」，实际整个网盘是死的（2026-11-03 实机踩到）。
        ///
        /// 顺序原来是反的（先 caddy 后 api），中间那段时间端口在听、后端没起，
        /// 用户正好刷新页面就是 502。现在从底层往上起。
        /// </summary>
        public static string Restart(InstallConfig cfg)
        {
            // ── 1. 数据库 ──
            // PG 起不来后面全是白搭，直接失败返回，别让用户以为重启成功了
            if (cfg.HasDatabaseService)
            {
                string err = RestartOne(cfg.DbServiceName, 60, 60);
                if (err != null) return "重启失败：\n\n" + err;

                int pgPort = cfg.PgPort;
                if (pgPort > 0 && !WaitTcp(pgPort, 60))
                {
                    return "重启失败：\n\nPostgreSQL 服务显示在运行，但 " + pgPort
                        + " 端口 60 秒内没有开始监听。\n\n看日志："
                        + Path.Combine(cfg.LogFolder, "postgres.log");
                }
            }

            // ── 2. API ──
            string apiErr = RestartOne(cfg.ApiServiceName, 30, 40);
            if (apiErr != null) return "重启失败：\n\n" + apiErr;

            if (!WaitTcp(cfg.ApiPort, 60))
            {
                return "重启失败：\n\nAPI 服务显示在运行，但 " + cfg.ApiPort
                    + " 端口 60 秒内没有开始监听（多半是连不上数据库崩了）。\n\n看日志："
                    + Path.Combine(cfg.LogFolder, "api-stderr.log");
            }

            // ── 3. Web ──
            string caddyErr = RestartOne(cfg.CaddyServiceName, 30, 40);
            if (caddyErr != null) return "重启失败：\n\n" + caddyErr;

            if (!WaitTcp(cfg.WebPort, 60))
            {
                return "重启失败：\n\nCaddy 服务显示在运行，但 " + cfg.WebPort
                    + " 端口 60 秒内没有开始监听。\n\n看日志："
                    + Path.Combine(cfg.LogFolder, "caddy-stderr.log");
            }

            return null;
        }

        /// <summary>
        /// 轮询等某个本机端口开始监听。连上就 true。
        /// 端口传 0（读不到配置）时直接放过 —— 宁可少等，也不要因为读不到
        /// 端口号就误判重启失败。
        /// </summary>
        private static bool WaitTcp(int port, int seconds)
        {
            if (port <= 0) return true;

            DateTime deadline = DateTime.UtcNow.AddSeconds(seconds);
            while (DateTime.UtcNow < deadline)
            {
                try
                {
                    using (var tcp = new System.Net.Sockets.TcpClient())
                    {
                        IAsyncResult ar = tcp.BeginConnect("127.0.0.1", port, null, null);
                        if (ar.AsyncWaitHandle.WaitOne(800) && tcp.Connected) return true;
                    }
                }
                catch (Exception) { }
                System.Threading.Thread.Sleep(700);
            }
            return false;
        }

        public static string RestartOnePublic(string name) { return RestartOne(name); }

        /// <summary>
        /// 确保三个服务都在跑，没跑的就拉起来。返回启动了几个；失败返回 -1。
        ///
        /// 为什么需要这个：服务是独立的 Windows 服务，跟托盘进程没有父子关系 ——
        /// 托盘退出不会停服务，服务也不会因为托盘起来就自动启动。
        /// 用户点快捷方式时如果服务是停的（比如刚装完还没启、或被人手动停了），
        /// 应该直接帮他拉起来，而不是让他自己去找「服务」管理器。
        /// </summary>
        public static int EnsureRunning(InstallConfig cfg)
        {
            int started = 0;
            // PG 必须先起 —— api 启动就连库，库不在会直接崩
            string[] order = { cfg.DbServiceName, cfg.ApiServiceName, cfg.CaddyServiceName };

            // 端口一次读好。PgPort 每次 get 都要翻 postgresql.conf，别在循环里反复问。
            int pgPort = cfg.PgPort;

            foreach (var name in order)
            {
                try
                {
                    using (var sc = new ServiceController(name))
                    {
                        if (sc.Status != ServiceControllerStatus.Running
                            && sc.Status != ServiceControllerStatus.StartPending)
                        {
                            sc.Start();
                            sc.WaitForStatus(ServiceControllerStatus.Running, TimeSpan.FromSeconds(25));
                            started++;
                        }
                    }
                }
                catch (Exception)
                {
                    // 服务不存在（还没装）或没权限 —— 都不是致命问题，
                    // 后续 Probe 会把真实状态反映到界面上，不在这里打断
                    continue;
                }

                // 🔴 服务 RUNNING 不等于端口在听。
                // PG 启动要 replay WAL、API 启动要先连库再 bind，都需要几秒。
                // 不等就返回的话，界面第一次探测必然失败 —— 用户看到「服务未启动」
                // 而其实只是还没就绪（2026-11-03 实机：刚开机点托盘就是这景象）。
                int port = name == cfg.DbServiceName ? pgPort
                         : name == cfg.ApiServiceName ? cfg.ApiPort
                         : cfg.WebPort;
                WaitTcp(port, 60);
            }
            return started;
        }

        /// <summary>停止三个服务（保留注册，随时能再起）。返回 null 成功，否则错误说明。</summary>
        public static string StopAll(InstallConfig cfg)
        {
            var errors = new List<string>();
            // 逆序停：先断入口（caddy），再停 api，最后停库
            foreach (var name in new[] { cfg.CaddyServiceName, cfg.ApiServiceName, cfg.DbServiceName })
            {
                try
                {
                    using (var sc = new ServiceController(name))
                    {
                        if (sc.Status == ServiceControllerStatus.Stopped) continue;
                        sc.Stop();
                        // PG 停止时要做一次 checkpoint，还要等客户端断开连接 ——
                        // 25 秒经常不够，超时后就卡在 StopPending，下一次 Start
                        // 直接失败。给它 90 秒。
                        int wait = name == cfg.DbServiceName ? 90 : 25;
                        sc.WaitForStatus(ServiceControllerStatus.Stopped, TimeSpan.FromSeconds(wait));
                    }
                }
                catch (InvalidOperationException ex)
                {
                    errors.Add(name + "：" + ex.Message);
                }
                catch (System.ServiceProcess.TimeoutException)
                {
                    errors.Add(name + "：停止超时");
                }
                catch (Exception) { /* 服务不存在就跳过 */ }
            }
            return errors.Count == 0 ? null : string.Join(Environment.NewLine, errors.ToArray());
        }

        /// <summary>三个服务是不是全都 Running。</summary>
        public static bool AllRunning(InstallConfig cfg)
        {
            foreach (var name in new[] { cfg.DbServiceName, cfg.ApiServiceName, cfg.CaddyServiceName })
            {
                try
                {
                    using (var sc = new ServiceController(name))
                    {
                        if (sc.Status != ServiceControllerStatus.Running) return false;
                    }
                }
                catch (Exception) { return false; }
            }
            return true;
        }

        private static string RestartOne(string name)
        {
            return RestartOne(name, 20, 30);
        }

        /// <summary>
        /// 停/启一个服务，等它到目标状态。超时时间可调 —— PostgreSQL 停止时
        /// 要做一次 checkpoint + 等客户端断开，20 秒经常不够；给它更长的窗口
        /// 比用 20 秒硬等然后误报「重启失败」强。
        /// </summary>
        private static string RestartOne(string name, int stopSeconds, int startSeconds)
        {
            try
            {
                using (var sc = new ServiceController(name))
                {
                    if (sc.Status != ServiceControllerStatus.Stopped)
                    {
                        sc.Stop();
                        sc.WaitForStatus(ServiceControllerStatus.Stopped, TimeSpan.FromSeconds(stopSeconds));
                    }
                    sc.Start();
                    sc.WaitForStatus(ServiceControllerStatus.Running, TimeSpan.FromSeconds(startSeconds));
                    return null;
                }
            }
            catch (InvalidOperationException ex)
            {
                return name + "：" + ex.Message + "（可能需要管理员权限）";
            }
            catch (System.ServiceProcess.TimeoutException)
            {
                return name + "：操作超时";
            }
            catch (Exception ex)
            {
                return name + "：" + ex.Message;
            }
        }
    }
}
