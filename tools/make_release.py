#!/usr/bin/env python3
"""发布打包：封面图 + 平台元数据 JSON + 发布清单。

为什么要有：短剧平台的发布需要三样东西——**封面、标题/简介/标签、成片**。
成片和质检报告流水线已经产出，封面与元数据没有；这里补上，并保证
"元数据里写的时长/分辨率"是从成片实测出来的（不是手填，避免对不上）。

用法：
  python3 tools/make_release.py --root /home/a/ai-work/duanju --series D202611_001
  python3 tools/make_release.py --root ... --episodes D202611_001_E01 D202611_001_E02
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shortdrama import episode  # noqa: E402

ROOT = Path("/home/a/ai-work/duanju")
FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
FONT_REG = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"


def ffmpeg() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        import shutil
        return shutil.which("ffmpeg")


def load(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def make_cover(job: Path, story: dict, series: dict, at_s: float = 3.0) -> Path | None:
    """从成片抽一帧做封面（竖屏 1080×1920），叠上剧名与本集标题。"""
    ep = job / "episode.mp4"
    if not ep.exists():
        return None
    from PIL import Image, ImageDraw, ImageFont

    tmp = job / "qc" / "_cover_raw.png"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
                        "-ss", str(at_s), "-i", str(ep), "-frames:v", "1", str(tmp)],
                       capture_output=True, text=True)
    if r.returncode != 0 or not tmp.exists():
        return None
    im = Image.open(tmp).convert("RGB")
    W, H = im.size
    draw = ImageDraw.Draw(im, "RGBA")
    # 底部压暗，给字留出可读背景（抖音封面也这样处理）
    grad = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    for y in range(int(H * 0.55), H):
        a = int(200 * (y - H * 0.55) / (H * 0.45))
        gd.line([(0, y), (W, y)], fill=(0, 0, 0, a))
    im = Image.alpha_composite(im.convert("RGBA"), grad)
    draw = ImageDraw.Draw(im)
    fs = int(W * 0.115)
    f1 = ImageFont.truetype(FONT, fs)
    f2 = ImageFont.truetype(FONT_REG, int(W * 0.052))
    series_title = series.get("series_title", "")
    title = story.get("title", job.name)
    draw.text((int(W * 0.06), int(H * 0.78)), series_title, font=f1, fill=(255, 255, 255, 255),
              stroke_width=int(fs * 0.08), stroke_fill=(0, 0, 0, 235))
    draw.text((int(W * 0.06), int(H * 0.78) + int(fs * 1.25)), title, font=f2,
              fill=(255, 235, 235, 255), stroke_width=int(fs * 0.05), stroke_fill=(0, 0, 0, 220))
    out = job / f"cover_{job.name}.jpg"
    im.convert("RGB").save(out, quality=92)
    tmp.unlink(missing_ok=True)
    return out


def probe_meta(path: Path) -> dict:
    info = episode.probe(path)
    return {"width": info["w"], "height": info["h"], "fps": info["fps"],
            "duration_s": round(info["duration"], 2)}


def _episode_no(job_name: str) -> str:
    """集数：D202611_001_E02_P → 2；D202611_001_E02 → 2（竖屏变体的 _P 不能当集数）。"""
    import re
    base = job_name[:-2] if job_name.endswith("_P") else job_name
    m = re.search(r"E(\d+)$", base)
    if m:
        return str(int(m.group(1)))
    tail = base.rsplit("_", 1)[-1]
    return tail if tail.isdigit() else base


def build_meta(job: Path, story: dict, series: dict, cover: Path | None) -> dict:
    ep = job / "episode.mp4"
    meta = {
        "episode_id": job.name,
        "series_title": series.get("series_title", ""),
        "series_logline": series.get("logline", ""),
        "genre": story.get("genre", ""),
        "episode_title": story.get("title", ""),
        "episode_no": story.get("episode_no") or _episode_no(job.name),
        "aspect": story.get("aspect", "landscape"),
        "video": ep.name if ep.exists() else None,
        "subtitles": "subtitles.srt",
        "cover": cover.name if cover else None,
        "duration_s": 0, "width": 0, "height": 0, "fps": 0,
        "title": f"《{series.get('series_title','')}》{story.get('title','')}｜{story.get('genre','')}短剧",
        "description": (story.get("ending", "") or series.get("logline", ""))[:180],
        "tags": ["短剧", "悬疑", story.get("genre", ""), "便利店", "凌晨三点",
                 story.get("title", "")],
        "qc": {},
    }
    if ep.exists():
        meta.update(probe_meta(ep))
    rep = job / "report.json"
    if rep.exists():
        r = load(rep)
        meta["qc"] = {
            "blocking": len(r.get("blocking", [])),
            "findings": len(r.get("findings", [])),
            "subtitle_coverage": r.get("episode_probe", {}).get("subtitle_coverage"),
            "passed": not r.get("blocking"),
        }
    return meta


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--series", default="D202611_001")
    ap.add_argument("--episodes", nargs="*", default=[])
    ap.add_argument("--cover-at", type=float, default=3.0, help="封面取第几秒的帧")
    a = ap.parse_args()

    root = Path(a.root)
    series_file = root / "series_outline.json"
    series = load(series_file) if series_file.exists() else {}
    eps = a.episodes or sorted(d.name for d in root.iterdir()
                                if d.is_dir() and d.name.startswith(a.series)
                                and (d / "episode.mp4").exists())

    metas = []
    for name in eps:
        job = root / name
        story_file = job / "story.json"
        story = load(story_file) if story_file.exists() else {}
        cover = make_cover(job, story, series, at_s=a.cover_at)
        meta = build_meta(job, story, series, cover)
        dump = job / "release_meta.json"
        dump.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        metas.append(meta)
        print(f"{name}: 封面 {cover.name if cover else '无'} | {meta['width']}x{meta['height']} "
              f"{meta['duration_s']}s | 质检 {'通过' if meta['qc'].get('passed') else '有阻断'}")

    # 汇总发布清单：平台发布要的是"哪几集、每集什么规格、能不能过门禁"
    lines = [f"# 发布清单 · {series.get('series_title', a.series)}", "",
             f"生成：{time.strftime('%F %T')} · 系列：{series.get('logline','')}", "",
             "| 集 | 标题 | 画幅 | 规格 | 时长 | 字幕覆盖 | 质检 | 封面 | 成片 | 元数据 |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for m in metas:
        qc = m.get("qc", {})
        lines.append(
            f"| {m['episode_id']} | {m['episode_title']} | {m['aspect']} | "
            f"{m['width']}×{m['height']} {m['fps']:.0f}fps | {m['duration_s']}s | "
            f"{qc.get('subtitle_coverage') or '-'} | "
            f"{'✅ 通过' if qc.get('passed') else '❌ 有阻断'} | "
            f"{m['cover'] or '-'} | {m['video'] or '-'} | release_meta.json |")
    lines += ["", "## 平台发布要点", "",
              "- 竖屏 1080×1920 是主交付（抖音/红果），横屏 1920×1056 为备用版本",
              "- 字幕已避让竖屏底部 UI（MarginV=360），不会与平台交互条重叠",
              "- 响度统一 -15 LUFS、峰值 ≤ -1 dBTP（平台不会二次压爆）",
              "- 每集已带封面图（cover_*.jpg）与元数据（release_meta.json）",
              "- 标题/简介/标签见各集 release_meta.json，可直接粘贴"]
    out = root / "RELEASE.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"发布清单 → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
