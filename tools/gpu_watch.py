#!/usr/bin/env python3
"""GPU 值班员：采样一次"显卡在干什么"，写进日志（给 cron 定期跑）。

只用标准库：cron 的环境变量很干净，不依赖 venv。

产出：
  logs/gpu_watch.jsonl     每次一行 JSON（可 grep / 可 jq）
  logs/gpu_watch.log       每次一行人话
  logs/gpu_watch/latest.json  最新一次快照（快速看当前状态）

用法：python3 tools/gpu_watch.py [--quiet]
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.request
from pathlib import Path

ROOT = Path("/home/a/ai-work")
LOG_DIR = ROOT / "logs"
COMFY = "http://127.0.0.1:8188"
COMFY_OUT = ROOT / "workspace" / "ComfyUI" / "output"
JOBS = ROOT / "duanju"


def sh(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=20).stdout.strip()
    except Exception:
        return ""


def gpu() -> dict:
    q = sh(["nvidia-smi",
            "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw",
            "--format=csv,noheader,nounits"]).splitlines()
    out = {"util": 0, "mem_used": 0, "mem_total": 0, "temp": 0, "power": 0}
    if q:
        try:
            u, mu, mt, tp, pw = [float(x.strip()) for x in q[0].split(",")]
            out.update(util=int(u), mem_used=int(mu), mem_total=int(mt),
                       temp=int(tp), power=round(pw, 1))
        except ValueError:
            pass
    procs = sh(["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory",
                "--format=csv,noheader,nounits"]).splitlines()
    out["procs"] = [p for p in procs if p.strip()]
    return out


def comfy_queue() -> dict:
    out = {"reachable": False, "running": 0, "pending": 0, "job": None}
    try:
        with urllib.request.urlopen(COMFY + "/queue", timeout=10) as r:
            q = json.loads(r.read())
        out["reachable"] = True
        run = q.get("queue_running", [])
        out["running"], out["pending"] = len(run), len(q.get("queue_pending", []))
        if run:
            g = run[0][2] if len(run[0]) > 2 else {}
            bits = []
            for nid, n in (g.items() if isinstance(g, dict) else []):
                i = n.get("inputs", {})
                ct = n.get("class_type", "")
                if ct in ("MiniMaxH3ReferenceToVideo", "MiniMaxH3ImageToVideo"):
                    bits.append(f"{ct}: {str(i.get('prompt',''))[:110]}")
                elif ct == "SaveVideo":
                    bits.append(f"输出前缀 {i.get('filename_prefix','')}")
                elif ct == "KSampler":
                    bits.append("Z-Image 出图")
            out["job"] = " ｜ ".join(bits) or "（图结构已识别，节点类型未知）"
    except Exception as e:
        out["error"] = str(e)[:80]
    return out


def newest_output() -> dict:
    best = None
    for sub in ("video", "image"):
        d = COMFY_OUT / sub
        if not d.is_dir():
            continue
        files = [p for p in d.iterdir() if p.is_file()]
        if not files:
            continue
        f = max(files, key=lambda p: p.stat().st_mtime)
        age = time.time() - f.stat().st_mtime
        if best is None or age < best["age_s"]:
            best = {"file": f"{sub}/{f.name}", "age_s": int(age), "size_kb": f.stat().st_size // 1024}
    return best or {}


def job_activity() -> dict:
    """哪个集最近在出片（clips 最新 mtime）。"""
    best = None
    if JOBS.is_dir():
        for d in JOBS.iterdir():
            clips = d / "clips"
            if not clips.is_dir():
                continue
            fs = [p for p in clips.glob("*.mp4")]
            if not fs:
                continue
            newest = max(fs, key=lambda p: p.stat().st_mtime)
            age = time.time() - newest.stat().st_mtime
            if best is None or age < best["age_s"]:
                best = {"episode": d.name, "clips": len(fs),
                        "latest": newest.name, "age_s": int(age)}
    return best or {}


def snapshot() -> dict:
    return {
        "ts": time.strftime("%F %T"),
        "gpu": gpu(),
        "comfy": comfy_queue(),
        "output": newest_output(),
        "job": job_activity(),
    }


def human(s: dict) -> str:
    g, c, o, j = s["gpu"], s["comfy"], s["output"], s["job"]
    if not c["reachable"]:
        return f"{s['ts']} | ComfyUI 不在线 | GPU {g['util']}% {g['mem_used']}/{g['mem_total']}MB"
    if c["running"]:
        head = f"{s['ts']} | GPU {g['util']}% {g['mem_used']}MB {g['temp']}℃ {g['power']}W | 渲染中"
        tail = f" | 队列 {c['running']}+{c['pending']}"
        if o:
            tail += f" | 最新产物 {o['file']}（{o['age_s']}s 前）"
        if j:
            tail += f" | {j['episode']} {j['clips']} 镜，最近 {j['latest']}"
        return head + tail + "\n    └ " + str(c["job"])
    return (f"{s['ts']} | GPU {g['util']}% {g['mem_used']}MB {g['temp']}℃ | **空闲**"
            f"（队列 {c['running']}+{c['pending']}）"
            + (f" | 最近产物 {o['file']}（{o['age_s']}s 前）" if o else "")
            + (f" | {j['episode']} {j['clips']} 镜" if j else ""))


def main() -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    s = snapshot()
    with (LOG_DIR / "gpu_watch.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(s, ensure_ascii=False) + "\n")
    with (LOG_DIR / "gpu_watch.log").open("a", encoding="utf-8") as f:
        f.write(human(s) + "\n")
    (LOG_DIR / "gpu_watch").mkdir(exist_ok=True)
    (LOG_DIR / "gpu_watch" / "latest.json").write_text(
        json.dumps(s, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if "--quiet" not in os.sys.argv:
        print(human(s))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
