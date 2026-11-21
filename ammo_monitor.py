#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Delta Force 弹药拍卖行 · 行情采集器 + 本地行情服务
------------------------------------------------
数据来源：https://deltaforcetools.gg/tw/auction-house/ammo
页面里每件道具都带 price_30d（该站记录的 30 天价格点位），采集器把这些点位
和每次轮询拿到的现价合并成价格序列，网页再用这些序列画 60 分钟 K 线。

用法：
    python ammo_monitor.py            # 采集 + 起服务 + 自动开浏览器
    python ammo_monitor.py --collect  # 只采集一次，不启动服务
    python ammo_monitor.py --no-open  # 起服务但不自动开浏览器
"""

import datetime as dt
import json
import os
import re
import socket
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")
STORE_PATH = os.path.join(DATA_DIR, "store.json")
HTML_PATH = os.path.join(HERE, "弹药K线监控.html")
URL_PATH = os.path.join(DATA_DIR, "url.txt")
LAN_URL_PATH = os.path.join(DATA_DIR, "lan_url.txt")

# 用 pythonw 无窗口启动时 stdout/stderr 是 None，print 会报错，统一转到日志文件
if sys.stdout is None or sys.stderr is None:
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        _log = open(os.path.join(DATA_DIR, "monitor.log"), "a", encoding="utf-8", buffering=1)
        sys.stdout = _log
        sys.stderr = _log
    except Exception:
        pass

BASE_URL = "https://deltaforcetools.gg/tw/auction-house/ammo"
SOURCE_LABEL = "deltaforcetools.gg · 拍卖行 / 弹药"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

POLL_SECONDS = 60
START_PORT = 8765
SITE_TZ = dt.timezone(dt.timedelta(hours=8))   # 源站时间标注为 GMT+8
KEEP_DAYS = 30
MAX_POINTS = 2000

ITEM_RE = re.compile(
    r'"item_key":"(?P<key>.*?)","item_name":"(?P<name>.*?)","image_url":"(?P<img>.*?)",'
    r'"item_level":(?P<lv>\d+|null),.*?'
    r'"current_price":"(?P<price>.*?)","change_1d_pct":"(?P<c1>.*?)",'
    r'"change_7d_pct":"(?P<c7>.*?)","change_30d_pct":"(?P<c30>.*?)",'
    r'"price_30d":(?P<series>\[[^\]]*\]|null),"lowest_price":"(?P<lo>.*?)",'
    r'"highest_price":"(?P<hi>.*?)"',
    re.S,
)
TOTAL_RE = re.compile(r'"totalPages":(\d+)')

_lock = threading.Lock()
_state = {"lastPoll": 0, "error": None, "polling": False}


# ----------------------------------------------------------------- 抓取与解析
def http_get(url, retries=2):
    last = None
    for i in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.6",
                "Cache-Control": "no-cache",
            })
            with urllib.request.urlopen(req, timeout=45) as resp:
                return resp.read().decode("utf-8", "replace")
        except Exception as exc:                      # 网络抖动重试
            last = exc
            time.sleep(1.5 + i)
    raise last


def parse_page(html):
    text = html.replace('\\"', '"')
    items = {}
    for m in ITEM_RE.finditer(text):
        raw = m.group("series")
        try:
            series = json.loads(raw) if raw and raw != "null" else []
        except Exception:
            series = []
        try:
            price = float(m.group("price"))
        except Exception:
            continue
        items[m.group("key")] = {
            "key": m.group("key"),
            "name": m.group("name").strip(),
            "level": int(m.group("lv")) if (m.group("lv") or "").isdigit() else 0,
            "price": price,
            "c1": _num(m.group("c1")),
            "c7": _num(m.group("c7")),
            "c30": _num(m.group("c30")),
            "low": _num(m.group("lo")),
            "high": _num(m.group("hi")),
            "series": series,
        }
    return items


def _num(s):
    try:
        return float(str(s).replace(",", "").strip())
    except Exception:
        return 0.0


def site_time_to_ms(s):
    """'2026-11-09 20:58:53'（GMT+8）-> epoch 毫秒"""
    try:
        return int(dt.datetime.strptime(s.strip(), "%Y-%m-%d %H:%M:%S")
                   .replace(tzinfo=SITE_TZ).timestamp() * 1000)
    except Exception:
        return 0

# ----------------------------------------------------------------- 存储
def load_store():
    if os.path.exists(STORE_PATH):
        try:
            with open(STORE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "items" in data:
                return data
        except Exception:
            pass
    return {"version": 1, "items": {}, "updateTime": 0}


def save_store(store):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = STORE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, STORE_PATH)


def merge_ticks(old, new, now):
    """按分钟去重；再压掉重复价格，只保留价格变化与 15 分钟心跳点。"""
    by_min = {}
    for t, p in old:
        by_min[int(t) // 60000] = (int(t), float(p))
    for t, p in new:
        if t:
            by_min[int(t) // 60000] = (int(t), float(p))
    ordered = sorted(by_min.items())
    cutoff = now - KEEP_DAYS * 86400 * 1000
    out = []
    last_p = None
    last_keep = 0
    for _, (t, p) in ordered:
        if t < cutoff:
            continue
        if last_p is None or p != last_p or (t - last_keep) >= 15 * 60 * 1000:
            out.append([t, p])
            last_p = p
            last_keep = t
    if len(out) > MAX_POINTS:
        out = out[-MAX_POINTS:]
    return out


def site_str_to_local(s):
    """把源站 GMT+8 墙上时间换成运行这台电脑的本地时间字符串"""
    try:
        d = dt.datetime.strptime(s.strip(), "%Y-%m-%d %H:%M:%S").replace(tzinfo=SITE_TZ)
        return d.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return s


def collect_once():
    """抓全部分页并合并进 store。返回 (items_count, source_update_text)"""
    if not _lock.acquire(blocking=False):
        return 0, ""
    _state["polling"] = True
    try:
        first = http_get(BASE_URL + "?page=1")
        m = TOTAL_RE.search(first.replace('\\"', '"'))
        total_pages = int(m.group(1)) if m else 3
        total_pages = max(1, min(total_pages, 20))

        pages = {1: first}
        if total_pages > 1:
            with ThreadPoolExecutor(max_workers=4) as pool:
                fut = {p: pool.submit(http_get, BASE_URL + "?page=%d" % p)
                       for p in range(2, total_pages + 1)}
                for p, f in fut.items():
                    try:
                        pages[p] = f.result(timeout=60)
                    except Exception:
                        pass

        fresh = {}
        for p, html in pages.items():
            fresh.update(parse_page(html))

        if not fresh:
            _state["error"] = "解析失败：页面里没有找到道具数据"
            return 0, ""

        now = int(time.time() * 1000)
        store = load_store()
        items = store.setdefault("items", {})
        latest_src = ""

        for key, it in fresh.items():
            rec = items.get(key) or {"key": key, "ticks": []}
            rec.update({
                "key": key,
                "name": it["name"],
                "level": it["level"],
                "price": it["price"],
                "c1": it["c1"],
                "c7": it["c7"],
                "c30": it["c30"],
                "low": it["low"],
                "high": it["high"],
            })
            new_points = []
            for pt in it["series"]:
                t = site_time_to_ms(pt.get("d", ""))
                if t:
                    new_points.append((t, float(pt.get("close") or 0)))
                    if pt.get("d") > latest_src:
                        latest_src = pt["d"]
            ticks = merge_ticks(rec.get("ticks") or [], new_points, now)
            last_t = ticks[-1][0] if ticks else 0
            last_p = ticks[-1][1] if ticks else None
            if last_p is None or it["price"] != last_p or (now - last_t) >= 15 * 60 * 1000:
                ticks = merge_ticks(ticks, [(now, it["price"])], now)
            rec["ticks"] = ticks
            rec["seen"] = now
            items[key] = rec

        store["updateTime"] = now
        store["sourceUpdated"] = site_str_to_local(latest_src) if latest_src else ""
        store["sourceUpdatedRaw"] = latest_src
        store["pollSeconds"] = POLL_SECONDS
        save_store(store)
        _state["lastPoll"] = now
        _state["error"] = None
        return len(fresh), latest_src
    except Exception as exc:
        _state["error"] = "%s: %s" % (type(exc).__name__, exc)
        return 0, ""
    finally:
        _state["polling"] = False
        _lock.release()


def poll_loop():
    while True:
        n, src = collect_once()
        print("[%s] 采集 %d 件 · 源站时间 %s%s" % (
            dt.datetime.now().strftime("%H:%M:%S"), n, src or "-",
            "" if not _state["error"] else " · 错误 " + str(_state["error"])))
        sys.stdout.flush()
        time.sleep(POLL_SECONDS)


# ----------------------------------------------------------------- API 组装
def build_snapshot():
    store = load_store()
    items = []
    for rec in store.get("items", {}).values():
        ticks = rec.get("ticks") or []
        spark = [round(p, 2) for _, p in ticks[-40:]]
        if not spark or spark[-1] != round(rec.get("price", 0), 2):
            spark.append(round(rec.get("price", 0), 2))
        items.append({
            "k": rec["key"],
            "n": rec.get("name", rec["key"]),
            "lv": rec.get("level", 0),
            "p": rec.get("price", 0),
            "c1": rec.get("c1", 0),
            "c7": rec.get("c7", 0),
            "c30": rec.get("c30", 0),
            "lo": rec.get("low", 0),
            "hi": rec.get("high", 0),
            "s": spark[-32:],
        })
    items.sort(key=lambda x: x["n"])
    return {
        "meta": {
            "mode": "live",
            "serverTime": int(time.time() * 1000),
            "lastPoll": store.get("updateTime", 0),
            "sourceUpdated": store.get("sourceUpdated", ""),
            "pollSeconds": store.get("pollSeconds", POLL_SECONDS),
            "count": len(items),
            "source": SOURCE_LABEL,
            "error": _state["error"],
        },
        "items": items,
    }


def build_ticks(key, hours=72):
    store = load_store()
    rec = store.get("items", {}).get(key)
    if not rec:
        return {"key": key, "ticks": []}
    now = int(time.time() * 1000)
    since = now - hours * 3600 * 1000
    ticks = [[t, p] for t, p in (rec.get("ticks") or []) if t >= since]
    if not ticks:
        ticks = (rec.get("ticks") or [])[-200:]
    return {"key": key, "price": rec.get("price", 0), "ticks": ticks}


# ----------------------------------------------------------------- HTTP 服务
class Handler(BaseHTTPRequestHandler):
    server_version = "AmmoMonitor/1.0"

    def log_message(self, fmt, *args):
        return

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urllib.parse.urlparse(self.path)
        route = path.path
        qs = urllib.parse.parse_qs(path.query)

        if route in ("/", "/index.html", "/弹药K线监控.html"):
            try:
                with open(HTML_PATH, "rb") as f:
                    body = f.read()
                self._send(200, body, "text/html; charset=utf-8")
            except FileNotFoundError:
                self._send(404, b"html not found", "text/plain; charset=utf-8")
            return

        if route == "/api/snapshot":
            snap = build_snapshot()
            self._send(200, json.dumps(snap, ensure_ascii=False,
                                       separators=(",", ":")).encode("utf-8"))
            return

        if route == "/api/ticks":
            key = (qs.get("key") or [""])[0]
            try:
                hours = float((qs.get("hours") or ["72"])[0])
            except Exception:
                hours = 72.0
            data = build_ticks(key, hours)
            self._send(200, json.dumps(data, ensure_ascii=False,
                                       separators=(",", ":")).encode("utf-8"))
            return

        if route == "/api/status":
            self._send(200, json.dumps({"ok": True, "state": _state},
                                       ensure_ascii=False).encode("utf-8"))
            return

        self._send(404, b"not found", "text/plain; charset=utf-8")


def lan_ip():
    """拿这台电脑在局域网里的 IPv4 地址（拿不到就返回空）"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.4)
        s.connect(("8.8.8.8", 53))
        ip = s.getsockname()[0]
        s.close()
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127."):
                return ip
    except Exception:
        pass
    return ""


def already_running():
    """已经有采集器在跑就返回它的端口，避免两个进程同时写 store"""
    for port in range(START_PORT, START_PORT + 6):
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/api/status" % port, timeout=0.6) as r:
                body = r.read(300)
                if b'"ok": true' in body or b'"ok":true' in body:
                    return port
        except Exception:
            continue
    return 0


def run_server(open_browser=True, host="127.0.0.1"):
    existing = already_running()
    if existing:
        url = "http://127.0.0.1:%d/" % existing
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(URL_PATH, "w", encoding="utf-8") as f:
                f.write(url)
        except Exception:
            pass
        print("采集器已经在运行：%s" % url)
        sys.stdout.flush()
        if open_browser:
            webbrowser.open(url)
        return

    port = START_PORT
    httpd = None
    for _ in range(6):
        try:
            httpd = ThreadingHTTPServer((host, port), Handler)
            break
        except OSError:
            port += 1
    if httpd is None:
        print("端口被占用，无法启动本地服务。")
        return

    url = "http://127.0.0.1:%d/" % port
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(URL_PATH, "w", encoding="utf-8") as f:
            f.write(url)
    except Exception:
        pass

    print("本地行情服务：%s" % url)
    if host == "0.0.0.0":
        ip = lan_ip()
        lan_url = "http://%s:%d/" % (ip, port) if ip else ""
        if lan_url:
            try:
                os.makedirs(DATA_DIR, exist_ok=True)
                with open(LAN_URL_PATH, "w", encoding="utf-8") as f:
                    f.write(lan_url)
            except Exception:
                pass
        print("手机/局域网访问：%s" % (lan_url or "（没拿到局域网 IP，检查是否连了 Wi-Fi）"))
    print("关闭窗口或按 Ctrl+C 结束。")
    sys.stdout.flush()
    if open_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    # 先把 HTTP 服务让出来，采集在后台线程里跑，避免打开页面时一直转圈
    def boot_collect():
        collect_once()
        poll_loop()

    threading.Thread(target=boot_collect, daemon=True).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    args = set(sys.argv[1:])
    if "--collect" in args:
        n, src = collect_once()
        print("采集完成：%d 件，源站时间 %s%s" % (
            n, src or "-", "" if not _state["error"] else "，错误：" + str(_state["error"])))
    else:
        run_server(open_browser="--no-open" not in args,
                   host="0.0.0.0" if "--lan" in args else "127.0.0.1")
