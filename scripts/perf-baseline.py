"""性能基线实测工具(2026-11-07 自主值守批):反馈列表 / 搜索 / 近期 / DAV / 照片。

方法学:顺序 20 连测取分位 —— 给路线图 P3 观察项(feedback O(n) 全读+看板
每请求重写、search/recent 全树 rglob)一个**当前规模的数字基线**,供量级
变化时对比。不是压测。

用法(对预发栈):
  python scripts/dev_stack.py start
  # 建号+种子后:
  python scripts/perf-baseline.py --base http://127.0.0.1:9766 \
      --user admin --password <pw> [--k 20]
种子:反馈经 UI/API 提(注意 20/天限速),文件走 DAV;或参照 2026-11-07
STATUS 的直写落盘方案(反馈文件直写 待处理/,文件直写 private/<user>/)。

实测(2026-11-07,80 条反馈 + 300 文件,PG 后端):
  feedback p50 78ms / p95 82ms · search p50 88ms · recent 9ms ·
  PROPFIND p50 7ms · photos p50 4.5ms

实测(2026-11-08 值守批,80 条反馈 + 9934 文件 ≈ 生产真实规模,PG 后端,
search/recent 优化后:rel_parts 预计算 + single-flight 重建):
  feedback p50 6.8ms · search p50 34.7ms · recent p50 34.7ms ·
  PROPFIND p50 5.0ms · photos p50 67.7ms(冷启首查 3.2s 系缩略图预热,
  稳态快)。并发消融:10 线程×8 请求,旧实现 p50 6406ms/41s →
  新实现 p50 362ms/3.1s(17.7x)。
"""
import argparse
import json
import os
import statistics
import time

import httpx


def pctl(xs, p):
    xs = sorted(xs)
    return xs[max(0, min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1)))))]


def bench(c, name, fn, ok=(200,), k=20):
    xs = []
    for _ in range(k):
        t0 = time.perf_counter()
        r = fn()
        xs.append((time.perf_counter() - t0) * 1000)
        # 限速桶(429)不算断言失败:连跑多轮时上一轮的配额会吃掉这一轮,
        # 直接炸掉整个基线反而拿不到数字。计入样本但打印提示,分位照出
        # —— 429 的延迟也是真实世界延迟的一部分。
        if r.status_code == 429:
            print(f"  ! {name}: 429 rate-limited (计入样本,连续多轮会这样)")
            continue
        assert r.status_code in ok, f"{name}: {r.status_code} {r.text[:80]}"
    print(f"{name:26s} n={k}  min {min(xs):7.1f}  p50 {pctl(xs, 50):7.1f}  "
          f"mean {statistics.mean(xs):7.1f}  p95 {pctl(xs, 95):7.1f}  "
          f"max {max(xs):7.1f} ms")
    return xs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:9766")
    ap.add_argument("--user", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--k", type=int, default=20)
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    c = httpx.Client(base_url=a.base, timeout=60)
    r = c.post("/api/login", json={"username": a.user, "password": a.password})
    assert r.status_code == 200, "登录失败"
    c.get("/api/feedback")
    c.get("/api/search", params={"q": "bench"})
    c.get("/api/recent")

    res = {}
    res["feedback_ms"] = {"p50": None, "p95": None}
    fb = bench(c, "GET /api/feedback", lambda: c.get("/api/feedback"), k=a.k)
    fb2 = bench(c, "GET /api/feedback(第2轮)", lambda: c.get("/api/feedback"), k=a.k)
    se = bench(c, "GET /api/search?q=bench",
               lambda: c.get("/api/search", params={"q": "bench"}), k=a.k)
    re_ = bench(c, "GET /api/recent", lambda: c.get("/api/recent"), k=a.k)
    da = bench(c, "PROPFIND /dav/private/%s/" % a.user,
               lambda: c.request("PROPFIND", "/dav/private/%s/" % a.user,
                                 headers={"Depth": "1"}), ok=(200, 207), k=a.k)
    ph = bench(c, "GET /api/photos",
               lambda: c.get("/api/photos", params={"limit": 120}), k=a.k)
    for key, xs in (("feedback", fb), ("feedback2", fb2), ("search", se),
                    ("recent", re_), ("dav_propfind", da), ("photos", ph)):
        res[key + "_ms"] = {"p50": round(pctl(xs, 50), 1), "p95": round(pctl(xs, 95), 1)}
    out = a.out or os.path.join(os.environ.get("TEMP", "."), "perf-baseline.json")
    json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
    print("saved", out)


if __name__ == "__main__":
    main()
