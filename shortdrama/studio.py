"""审片 Web 界面：TUI 看不了图/视频，所以人在浏览器里审，agent 在 TUI 里收决定。

为什么需要它：
- pi-agent 的 TUI **不能显示图片和视频** —— 定妆候选、分镜、成片都只能靠人眼在浏览器里看
- 方案 §11.2 说真正的瓶颈是"人工审片"（12 镜一集 3–5 分钟），审片没有界面就只能靠传文件

协作模型（状态全在文件里，无数据库，延续 D11）：
    agent ──写──▶  job 目录（cast/ frames/ clips/ episode.mp4 report.json run.log）
                        │
                     浏览器 ◀──读──┘
                        │
                     浏览器 ──写决定──▶ cast/selection.json   （挑图）
                                       review.json         （逐镜通过/重出）
                        │
    agent ◀──读决定──┘   （TUI 里问"人挑好了吗"：sd studio status）
    render 阶段读 review.json → 只重出被标记 redo 的镜

绑定 127.0.0.1 默认（ComfyUI 无鉴权是已知风险 R5，别再加一个）。
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
HTML = HERE / "studio.html"

from . import board                      # 任务看板（阶段卡片 + 触发，方案 §4.3/§4.4）
# 决定文件（都在 job 目录里，agent 直接读）
SELECTION = "cast/selection.json"
REVIEW = "review.json"


# ---------------------------------------------------------------- 状态

def _ffmpeg_fallback() -> str:
    """系统 python 没有 imageio_ffmpeg（本机 ffmpeg 只装在 .venv 里），回退到已知路径。"""
    import shutil
    known = ("/home/a/ai-work/.venv/lib/python3.10/site-packages/imageio_ffmpeg/"
             "binaries/ffmpeg-linux-x86_64-v7.0.2")
    if Path(known).exists():
        return known
    return shutil.which("ffmpeg") or "ffmpeg"


def _probe(path: Path) -> dict:
    """用 ffmpeg -i 解析（本机没有 ffprobe）。"""
    try:
        import imageio_ffmpeg
        ff = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        ff = _ffmpeg_fallback()
    out = subprocess.run([ff, "-hide_banner", "-i", str(path)],
                         capture_output=True, text=True).stderr
    dur = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", out)
    wh = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", out)
    return {
        "duration": (int(dur.group(1)) * 3600 + int(dur.group(2)) * 60 + float(dur.group(3)))
        if dur else 0,
        "w": int(wh.group(1)) if wh else 0,
        "h": int(wh.group(2)) if wh else 0,
        "audio": "Audio:" in out,
    }


def episode_state(job: Path) -> dict:
    """一个集的状态：进度 / 定妆候选 / 关键帧 / 镜头 / 成片 / 质检。"""
    job = Path(job)
    st: dict = {"job": job.name, "path": str(job), "exists": job.is_dir()}
    if not job.is_dir():
        return st

    # 进度：数镜头文件 vs shots.json
    shots_file = job / "shots.json"
    shots = []
    if shots_file.exists():
        try:
            shots = json.loads(shots_file.read_text(encoding="utf-8")).get("shots", [])
        except ValueError:
            shots = []
    # 只数 NN.mp4（clips/ 里还有 concat.mp4 / upscaled.mp4 两个中间产物）
    clips = sorted(p for p in (job / "clips").glob("*.mp4")
                   if p.stem.isdigit()) if (job / "clips").is_dir() else []
    st["shot_total"] = len(shots)
    st["clip_done"] = len(clips)
    st["pct"] = round(len(clips) / len(shots) * 100) if shots else 0
    st["stage"] = ("未开渲" if not shots else
                   "渲染中" if len(clips) < len(shots) else
                   "已出镜" if not (job / "episode.mp4").exists() else "已成片")
    # 画幅：审片时要能一眼分清横屏版 / 竖屏版（缩略图比例也不同）
    story_file = job / "story.json"
    st["aspect"] = "landscape"
    if story_file.exists():
        try:
            st["aspect"] = json.loads(story_file.read_text(encoding="utf-8")).get("aspect", "landscape")
        except ValueError:
            pass

    # 定妆候选（剧集级共享目录，或本集内）
    cast_dir = job / "cast"
    cands = {}
    if cast_dir.is_dir():
        for p in sorted(cast_dir.glob("*_cand*.png")):
            cid = p.name.split("_cand")[0]
            cands.setdefault(cid, []).append(p.name)
    sel = {}
    sel_file = job / SELECTION
    if sel_file.exists():
        try:
            sel = json.loads(sel_file.read_text(encoding="utf-8"))
        except ValueError:
            sel = {}
    st["cast"] = {cid: {"candidates": v, "picked": sel.get(cid, {})} for cid, v in cands.items()}
    st["cast_picked"] = {cid: (job / "cast" / f"{cid}.png").exists() for cid in cands}

    # 逐镜：关键帧 + 视频 + 决定
    review = {}
    rv_file = job / REVIEW
    if rv_file.exists():
        try:
            review = json.loads(rv_file.read_text(encoding="utf-8"))
        except ValueError:
            review = {}
    rows = []
    for s in shots:
        idx = s["idx"]
        clip = job / "clips" / f"{idx:02d}.mp4"
        kf = job / "frames" / f"{idx:02d}.png"
        rows.append({
            "idx": idx,
            "beat": s.get("beat_cn", ""),
            "prompt": s.get("prompt_en", ""),
            "subtitle": s.get("subtitle_cn", ""),
            "ref": s.get("ref"),
            "clip": f"clips/{clip.name}" if clip.exists() else None,
            "frame": f"frames/{kf.name}" if kf.exists() else None,
            "verdict": (review.get(f"{idx:02d}") or {}).get("verdict"),
            "note": (review.get(f"{idx:02d}") or {}).get("note", ""),
        })
    st["shots"] = rows
    st["review_pending"] = [r["idx"] for r in rows if r["verdict"] == "redo"]
    st["review_done"] = sum(1 for r in rows if r["verdict"])

    # 成片 + 质检
    ep = job / "episode.mp4"
    if ep.exists():
        st["episode"] = {"file": "episode.mp4", **_probe(ep),
                         "size": ep.stat().st_size}
    # 发布元数据 + 封面（放映页 / 上传包用）
    mp = job / "release_meta.json"
    if mp.exists():
        try:
            st["release"] = json.loads(mp.read_text(encoding="utf-8"))
        except ValueError:
            pass
    cover_name = (st.get("release") or {}).get("cover") or f"cover_{job.name}.jpg"
    st["cover"] = cover_name if (job / cover_name).exists() else None
    rp = job / "report.json"
    if rp.exists():
        try:
            rep = json.loads(rp.read_text(encoding="utf-8"))
            st["report"] = {"findings": rep.get("findings", []),
                            "blocking": len(rep.get("blocking", [])),
                            "audio": rep.get("episode_probe", {}).get("audio_measured"),
                            "subs": rep.get("episode_probe", {}).get("subtitle_coverage")}
        except ValueError:
            pass
    # 最后心跳（run.log 尾部）—— 让网页能显示 agent 正在干什么
    log = job / "logs" / "run.log"
    if log.exists():
        try:
            lines = log.read_text(encoding="utf-8", errors="ignore").strip().splitlines()
            st["log_tail"] = lines[-3:]
        except OSError:
            pass
    # 任务看板：阶段卡片（状态从目录推断）+ 心跳尾
    st["board"] = board.board(job)
    return st


def series_state(root: Path, series: str = "D202611_001") -> dict:
    """剧集级：把各集列出来 + 共用的定妆图。"""
    root = Path(root)
    eps = []
    for d in sorted(root.glob(f"{series}_E*")):
        if d.is_dir():
            eps.append(episode_state(d))
    shared = []
    cast_dir = root / "cast"
    if cast_dir.is_dir():
        for p in sorted(cast_dir.glob("*.png")):
            if "_cand" not in p.name:
                shared.append(p.name)
    return {"series": series, "root": str(root), "episodes": eps,
            "shared_cast": shared,
            "outline": str(root / "series_outline.json")}


# ---------------------------------------------------------------- 写决定

def write_selection(job: Path, data: dict) -> Path:
    p = Path(job) / SELECTION
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return p


def write_review(job: Path, data: dict) -> Path:
    p = Path(job) / REVIEW
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return p


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    root: Path = Path(".")
    series: str = "D202611_001"
    token: str = ""            # 写操作要带令牌（局域网开放时防误写/乱写）

    def log_message(self, fmt, *args):        # 别刷屏
        pass

    def _authorized(self) -> bool:
        """写操作必须带对令牌。读操作在局域网开放（自己的片子自己看）。

        为什么写操作要令牌：局域网里任何设备都能 POST，而「重出」会**删 clip**。
        """
        if not self.token:
            return True
        got = self.headers.get("X-Studio-Token", "")
        if not got:
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            got = (q.get("k") or [""])[0]
        return got == self.token

    def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def do_GET(self):
        p = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(p.path)
        if path in ("/", "/index.html"):
            self._send(200, HTML.read_bytes(), "text/html; charset=utf-8")
            return
        if path == "/api/state":
            self._json(series_state(self.root, self.series))
            return
        if path.startswith("/media/"):
            # /media/<job>/<相对路径>
            rest = path[len("/media/"):]
            target = (self.root / rest).resolve()
            if not str(target).startswith(str(self.root.resolve())):
                self._json({"error": "越界"}, 403)
                return
            if not target.is_file():
                self._send(404, b"not found", "text/plain")
                return
            ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
            # 支持 Range：审片台要能拖进度条 seek（成片 137MB，没有 Range 浏览器定位不了）
            size = target.stat().st_size
            rng = self.headers.get("Range")
            if rng and rng.startswith("bytes="):
                try:
                    a, _, b = rng[6:].partition("-")
                    start = int(a) if a else 0
                    end = int(b) if b else size - 1
                    start = max(0, min(start, size - 1))
                    end = max(start, min(end, size - 1))
                    with target.open("rb") as f:
                        f.seek(start)
                        data = f.read(end - start + 1)
                    self.send_response(206)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                    self.send_header("Accept-Ranges", "bytes")
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(data)
                    return
                except ValueError:
                    pass
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(size))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            with target.open("rb") as f:
                while True:
                    chunk = f.read(256 * 1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
            return
        self._send(404, b"not found", "text/plain")

    def do_POST(self):
        p = urllib.parse.urlparse(self.path)
        if not self._authorized():
            self._json({"error": "令牌不对，写操作被拒"}, 403)
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            self._json({"error": "bad json"}, 400)
            return
        if p.path == "/api/selection":
            job = self.root / data.get("job", "")
            if not job.is_dir():
                self._json({"error": "job 不存在"}, 400)
                return
            write_selection(job, data.get("selection", {}))
            self._json({"ok": True, "written": str(job / SELECTION)})
            return
        if p.path == "/api/review":
            job = self.root / data.get("job", "")
            if not job.is_dir():
                self._json({"error": "job 不存在"}, 400)
                return
            rv = data.get("review", {})
            write_review(job, rv)
            # 标记 redo 的镜：直接删掉 clip，下次 render 会重出这一镜
            for k, v in rv.items():
                if isinstance(v, dict) and v.get("verdict") == "redo":
                    clip = job / "clips" / f"{k}.mp4"
                    if clip.exists():
                        clip.unlink()
            self._json({"ok": True, "written": str(job / REVIEW)})
            return
        if p.path == "/api/run":
            # 触发一个阶段（看板按钮）。网页只做触发器，不自己跑 GPU/ffmpeg（方案 §4.3）
            job = self.root / data.get("job", "")
            if not job.is_dir():
                self._json({"error": "job 不存在"}, 400)
                return
            res = board.start(job, data.get("stage", ""), data.get("mode", "cli"))
            self._json(res, 200 if res.get("ok") else 400)
            return
        if p.path == "/api/stop":
            job = self.root / data.get("job", "")
            if not job.is_dir():
                self._json({"error": "job 不存在"}, 400)
                return
            res = board.stop(job, data.get("stage", ""))
            self._json(res, 200 if res.get("ok") else 400)
            return
        self._json({"error": "unknown endpoint"}, 404)


def serve(root: Path, series: str = "D202611_001", host: str = "127.0.0.1",
          port: int = 8095, token: str = "") -> str:
    """起审片台。局域网开放时返回带令牌的 URL（写操作需要令牌）。"""
    import secrets
    token = token or secrets.token_urlsafe(6)
    # 重定向到文件时 stdout 是块缓冲 → 令牌会卡在缓冲区里打不出来，必须 flush
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass
    Handler.root = Path(root).resolve()
    Handler.series = series
    Handler.token = token if host not in ("127.0.0.1", "localhost") else ""
    srv = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{port}/"
    if Handler.token:
        url += f"?k={Handler.token}"
    print(f"[studio] 本机   http://127.0.0.1:{port}/")
    if host not in ("127.0.0.1", "localhost"):
        for ip in _lan_ips():
            print(f"[studio] 局域网 http://{ip}:{port}/?k={Handler.token}")
    print(f"[studio] 根目录 {Handler.root}")
    print("[studio] 选图 → cast/selection.json；逐镜通过/重出 → review.json（重出会删 clip，下次 render 只重出该镜）")
    if Handler.token:
        print("[studio] 写操作需要令牌（?k=… 或 X-Studio-Token 头）")
    print("[studio] Ctrl-C 退出")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[studio] 已退出")
    return url


def _lan_ips() -> list[str]:
    out = []
    try:
        import subprocess
        r = subprocess.run(["hostname", "-I"], capture_output=True, text=True)
        out = [x for x in r.stdout.split() if not x.startswith("127.")]
    except Exception:
        pass
    return out
