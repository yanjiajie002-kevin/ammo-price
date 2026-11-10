#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 data/store.json 导出成页面直接读取的 data.json（GitHub Pages 静态版）。"""

import io
import json
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))
STORE = os.path.join(HERE, "data", "store.json")
OUT = os.path.join(HERE, "data.json")

KEEP_DAYS = 30
RAW_DAYS = 3          # 最近 3 天保留全部点位，更早的按小时抽样
MAX_POINTS = 1200     # 单件点位上限


def slim(seq, now_ms):
    raw_since = now_ms - RAW_DAYS * 86400 * 1000
    out, bucket, last = [], None, None
    for t, p in seq:
        if t < now_ms - KEEP_DAYS * 86400 * 1000:
            continue
        if t >= raw_since:
            out.append([t, p])
            bucket = t // 3600000
            last = p
        else:
            b = t // 3600000
            if b != bucket:
                out.append([t, p])
                bucket, last = b, p
            else:
                out[-1] = [t, p]
                last = p
    return out[-MAX_POINTS:]


def main():
    if not os.path.exists(STORE):
        raise SystemExit("缺少 data/store.json，先跑一次采集。")
    with io.open(STORE, "r", encoding="utf-8") as f:
        store = json.load(f)

    now = int(time.time() * 1000)
    items, ticks = [], {}
    for rec in store.get("items", {}).values():
        seq = rec.get("ticks") or []
        spark = [round(p, 2) for _, p in seq[-32:]]
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
        ticks[rec["key"]] = slim(seq, now)

    items.sort(key=lambda x: x["n"])
    payload = {
        "meta": {
            "mode": "static",
            "serverTime": now,
            "lastPoll": store.get("updateTime", 0),
            "sourceUpdated": store.get("sourceUpdated", ""),
            "pollSeconds": 1200,
            "count": len(items),
            "source": "deltaforcetools.gg · 拍卖行 / 弹药",
        },
        "items": items,
        "ticks": ticks,
    }

    with io.open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))

    points = sum(len(v) for v in ticks.values())
    print("导出 %d 件 / %d 点位，data.json %.1f KB"
          % (len(items), points, os.path.getsize(OUT) / 1024.0))


if __name__ == "__main__":
    main()
