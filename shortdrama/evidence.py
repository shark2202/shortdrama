#!/usr/bin/env python3
"""证据表：在**看不到画面**的环境（SSH / TUI）里，把"这镜有什么问题"变成可打印的事实。

为什么需要它（来自实跑的两个教训）：
1. 专家不是"逐帧看"，而是**先分层下钻**：先扫全片找异常，再只看可疑镜。
   没有可打印的事实，人就只能全片看，或者干脆不看。
2. **关键帧不能代表成片**（SOP §21）：用关键帧审 105 镜误判了 29 镜。
   本工具用 **PSNR 量化"关键帧 vs 成片帧"的差距**，把"这条证据路径不可信"的镜直接标出来，
   人就知道该看哪几镜、该信哪条证据。

每镜产出 4 个可核对的事实（都来自 ffmpeg 客观测量，不是模型意见）：
- `yavg` / `sat`  亮度与饱和度（signalstats）→ 亮度离群、过暗/过曝
- `frozen_s`     画面静止秒数（freezedetect）→ 卡住/废镜
- `psnr_kf`      关键帧 vs 成片帧的 PSNR（psnr filter）→ 关键帧能不能代表成片
- `dup_prev`     与上一镜的 8×8 彩色签名距离 → 重复镜

输出：`qc/evidence.json`（机器读）+ `qc/evidence.md`（人读，含"先看哪几镜"的优先级排序）。

用法：
    python3 shortdrama/evidence.py <job-dir> [--workers 4]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

SIG_W, SIG_H = 8, 8          # 签名分辨率：够抓布局+色调，又不会被细节噪声干扰
SIG_BYTES = SIG_W * SIG_H * 3


def ffmpeg() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        import shutil
        from pathlib import Path
        exe = shutil.which("ffmpeg")
        if not exe:
            # 静态构建在 imageio-ffmpeg 的包里（独立 venv），系统 python3 里也能找到
            for root in ("/home/a/ai-work/.venv", "/home/a/ai-work/ComfyUI/.venv"):
                hits = sorted(Path(root).glob("lib/python*/site-packages/imageio_ffmpeg/binaries/ffmpeg-*"))
                if hits:
                    return str(hits[0])
            raise SystemExit("找不到 ffmpeg")
        return exe


def _run(cmd: list[str]) -> tuple[int, bytes, str]:
    p = subprocess.run(cmd, capture_output=True)
    return p.returncode, p.stdout, p.stderr.decode("utf-8", "ignore")


def brightness(clip: Path, at: float = 2.5) -> tuple[float | None, float | None]:
    """(YAVG, SATAVG)：画面亮度与饱和度。

    `metadata=print:file=-` 走 stdout → 不受 -loglevel 影响（§2.8 踩过的坑：
    metadata=print 是 info 级日志，被 -loglevel error 吞掉 → 亮度测出 0）。
    取不到帧时（clip 比取样点还短）自动往回退，别把 None 当 0。
    """
    for at_try in (at, 0.5, 0.0):
        cmd = [ffmpeg(), "-hide_banner", "-loglevel", "error", "-ss", str(at_try), "-i", str(clip),
               "-frames:v", "1", "-vf", "signalstats,metadata=print:file=-", "-f", "null", "-"]
        _, out, _ = _run(cmd)
        text = out.decode("utf-8", "ignore")
        y = _last(text, r"lavfi\.signalstats\.YAVG=([\d.]+)")
        if y is not None:
            return y, _last(text, r"lavfi\.signalstats\.SATAVG=([\d.]+)")
    return None, None


def _last(text: str, pattern: str) -> float | None:
    m = re.findall(pattern, text)
    return float(m[-1]) if m else None


def frozen_seconds(clip: Path) -> float:
    """画面静止（几乎不变）的秒数：卡住/废镜的硬信号。"""
    cmd = [ffmpeg(), "-hide_banner", "-i", str(clip),
           "-vf", "freezedetect=n=0.001:d=0.5", "-f", "null", "-"]
    _, _, err = _run(cmd)
    d = _last(err, r"frozen_duration:\s*([\d.]+)")
    return d or 0.0


def signature(clip: Path, at: float = 2.5) -> list[int] | None:
    """8×8 RGB 缩略签名：用于"这两镜是不是几乎一样"。"""
    for at_try in (at, 0.5, 0.0):
        cmd = [ffmpeg(), "-hide_banner", "-loglevel", "error", "-ss", str(at_try), "-i", str(clip),
               "-frames:v", "1", "-vf", f"scale={SIG_W}:{SIG_H}", "-pix_fmt", "rgb24",
               "-f", "rawvideo", "-"]
        rc, out, _ = _run(cmd)
        if rc == 0 and len(out) >= SIG_BYTES:
            return list(out[:SIG_BYTES])
    return None


def keyframe_psnr(kf: Path, clip: Path, at: float = 2.5) -> float | None:
    """关键帧 vs 成片同一时刻的 PSNR：<16 dB 基本是两幅不相干的画。"""
    cmd = [ffmpeg(), "-hide_banner", "-i", str(kf), "-ss", str(at), "-i", str(clip),
           "-lavfi", f"[0:v]scale=640:352[a];[1:v][a]psnr", "-f", "null", "-"]
    _, _, err = _run(cmd)
    return _last(err, r"PSNR .*?average:([\d.]+)")


def _dist(a: list[int] | None, b: list[int] | None) -> float | None:
    if not a or not b:
        return None
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


def shot_facts(job: Path, shots: list[dict]) -> list[dict]:
    """逐镜取事实（可并行）。"""
    out: list[dict] = []

    def one(shot: dict) -> dict:
        idx = shot["idx"]
        clip = job / "clips" / f"{idx:02d}.mp4"
        kf = job / "frames" / f"{idx:02d}.png"
        rec: dict[str, Any] = {"idx": idx, "ref": shot.get("ref"),
                               "subtitle": (shot.get("subtitle_cn") or "").strip()}
        if not clip.exists():
            rec["flags"] = ["缺 clip"]
            return rec
        y, s = brightness(clip)
        rec["yavg"], rec["sat"] = y, s
        rec["frozen_s"] = frozen_seconds(clip)
        rec["sig"] = signature(clip)
        if kf.exists():
            rec["psnr_kf"] = keyframe_psnr(kf, clip)
        return rec

    with ThreadPoolExecutor(max_workers=4) as ex:
        out = list(ex.map(one, shots))
    out.sort(key=lambda r: r["idx"])
    # 第二遍：需要邻居的判断（跳变/重复）+ 剧集级基准（亮度中位、关键帧 PSNR 中位）
    ys = [r["yavg"] for r in out if r.get("yavg") is not None]
    med = sorted(ys)[len(ys) // 2] if ys else 90.0
    ps = [r["psnr_kf"] for r in out if r.get("psnr_kf") is not None]
    ps_med = sorted(ps)[len(ps) // 2] if ps else None
    for i, r in enumerate(out):
        flags = r.get("flags", [])
        y = r.get("yavg")
        if y is not None:
            if y < 40:
                flags.append(f"过暗（YAVG {y:.0f}）")
            elif y > 170:
                flags.append(f"过曝（YAVG {y:.0f}）")
            if i > 0 and out[i - 1].get("yavg") is not None:
                d = abs(y - out[i - 1]["yavg"])
                if d > 10:
                    flags.append(f"亮度跳变（与上一镜差 {d:.0f}）")
        if (r.get("frozen_s") or 0) > 1.0:
            flags.append(f"画面静止 {r['frozen_s']:.1f}s（可能卡住）")
        p = r.get("psnr_kf")
        if p is not None and ps_med is not None and p < ps_med - 5:
            flags.append(f"关键帧特别不可信（PSNR {p:.0f}dB vs 本集中位 {ps_med:.0f}）")
        if i > 0:
            d = _dist(r.get("sig"), out[i - 1].get("sig"))
            if d is not None and d < 6:
                flags.append(f"与上一镜几乎相同（签名距离 {d:.1f}）")
            r["dup_prev"] = d
        r["flags"] = flags
        # 优先级：跳变/过暗过曝（观众立刻感觉到）> 重复/静止 > 关键帧可疑
        r["priority"] = (2 if any(k in f for f in flags
                                  for k in ("跳变", "过暗", "过曝")) else 0) \
            + (1 if any(k in f for f in flags
                        for k in ("相同", "静止", "关键帧")) else 0)
    meta = {"yavg_median": med, "psnr_kf_median": ps_med, "shots": len(out)}
    return out, meta


def write_evidence(job: Path, facts: list[dict], meta: dict) -> tuple[Path, Path]:
    qc = job / "qc"
    qc.mkdir(exist_ok=True)
    jp = qc / "evidence.json"
    jp.write_text(json.dumps({"meta": meta, "shots": facts},
                             ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    ps_med = meta.get("psnr_kf_median")
    lines = ["# 证据表（看不到画面时的可打印事实）", "",
             "每行一镜；`旗标` = 需要人眼确认的疑点，`优先级` 高的先看。",
             f"亮度中位 {meta.get('yavg_median', 0):.0f}；"
             + (f"关键帧 vs 成片帧 PSNR 中位 **{ps_med:.0f} dB** → 关键帧整体不能代表成片，"
                "审片只认成片抽帧（§21）。" if ps_med is not None else ""),
             "",
             "| 镜 | 亮度 | 饱和 | 静止s | 关键帧PSNR | 与上镜相似度 | 旗标 |",
             "|---|---|---|---|---|---|---|"]
    for r in facts:
        ps = f"{r['psnr_kf']:.0f}" if r.get("psnr_kf") is not None else "-"
        dp = f"{r['dup_prev']:.1f}" if r.get("dup_prev") is not None else "-"
        lines.append(f"| {r['idx']:02d} | {r.get('yavg') or 0:.0f} | {r.get('sat') or 0:.0f} | "
                     f"{r.get('frozen_s') or 0:.1f} | {ps} | {dp} | {'；'.join(r.get('flags') or []) or '-'} |")
    flagged = [r for r in facts if r.get("flags")]
    flagged.sort(key=lambda r: (-r.get("priority", 0), r["idx"]))
    lines += ["", f"## 先看这 {len(flagged)} 镜（按优先级）", ""]
    lines += [f"- **{r['idx']:02d}** {'；'.join(r['flags'])}" for r in flagged] or ["（无）"]
    mp = qc / "evidence.md"
    mp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return jp, mp


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="证据表：把画面问题变成可打印事实")
    ap.add_argument("job", help="集目录")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args(argv)
    job = Path(args.job).expanduser().resolve()
    shots = json.loads((job / "shots.json").read_text(encoding="utf-8")).get("shots", [])
    facts, meta = shot_facts(job, shots)
    jp, mp = write_evidence(job, facts, meta)
    flagged = sum(1 for r in facts if r.get("flags"))
    print(f"[evidence] {len(facts)} 镜 → {jp.name} / {mp.name}（{flagged} 镜有旗标）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
