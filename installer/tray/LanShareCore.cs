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

        public string WebUrl { get { return "http://127.0.0.1:" + WebPort + "/"; } }
        public string HealthUrl { get { return "http://127.0.0.1:" + WebPort + "/healthz"; } }
        public string LogFolder { get { return Path.Combine(InstallFolder, "logs"); } }
        public string ApiServiceName { get { return ServicePrefix + "-api"; } }
        public string CaddyServiceName { get { return ServicePrefix + "-caddy"; } }
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

        /// <summary>重启 api 和 caddy。返回 null 表示成功，否则是错误说明。</summary>
        public static string Restart(InstallConfig cfg)
        {
            var errors = new List<string>();

            // 先重启 caddy（它是入口），再确认 api 活着
            // 顺序反了会有一小段「端口在听但后端没起来」的窗口
            foreach (var name in new[] { cfg.CaddyServiceName, cfg.ApiServiceName })
            {
                string err = RestartOne(name);
                if (err != null) errors.Add(err);
            }

            if (errors.Count == 0) return null;
            return "重启失败：\n\n" + string.Join("\n", errors.ToArray());
        }

        private static string RestartOne(string name)
        {
            try
            {
                using (var sc = new ServiceController(name))
                {
                    if (sc.Status != ServiceControllerStatus.Stopped)
                    {
                        sc.Stop();
                        sc.WaitForStatus(ServiceControllerStatus.Stopped, TimeSpan.FromSeconds(20));
                    }
                    sc.Start();
                    sc.WaitForStatus(ServiceControllerStatus.Running, TimeSpan.FromSeconds(30));
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
