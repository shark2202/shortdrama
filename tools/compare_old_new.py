#!/usr/bin/env python3
"""生成"旧片 vs 新片"并排对比图（逐镜复审用）。

场景：按规格重出了一批镜（旧片在 clips/bak/），需要逐镜看新旧差别，
     变差的镜回滚旧片（cp clips/bak/NN.mp4 clips/NN.mp4）。

用法：
  python3 tools/compare_old_new.py <job-dir> [--per-sheet 12] [--out qc]
  → 每集每画幅若干张 PNG：左列=旧（bak），右列=新
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shortdrama import episode  # noqa: E402


def grab(clip: Path, out: Path, at: float = 2.5) -> bool:
    r = subprocess.run([episode.ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
                        "-ss", f"{at:.2f}", "-i", str(clip), "-frames:v", "1", str(out)],
                       capture_output=True, text=True)
    return r.returncode == 0 and out.exists()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("job")
    ap.add_argument("--per-sheet", type=int, default=12)
    ap.add_argument("--out", default="qc")
    a = ap.parse_args()
    job = Path(a.job).expanduser().resolve()
    bak = job / "clips" / "bak"
    if not bak.is_dir():
        print("没有 clips/bak（不是重出批次）")
        return 0
    outdir = job / a.out
    outdir.mkdir(parents=True, exist_ok=True)
    tmp = outdir / "_cmp"
    tmp.mkdir(exist_ok=True)
    for f in tmp.glob("*.png"):
        f.unlink()

    olds = sorted(bak.glob("[0-9][0-9].mp4"))
    pairs = []
    for b in olds:
        new = job / "clips" / b.name
        if new.exists():
            pairs.append((b, new))
    print(f"{job.name}: 可对比 {len(pairs)} 镜")

    from PIL import Image
    per = a.per_sheet
    for sheet_i in range(0, len(pairs), per):
        chunk = pairs[sheet_i:sheet_i + per]
        cells = []
        for b, new in chunk:
            ob, on = tmp / f"old_{b.stem}.png", tmp / f"new_{b.stem}.png"
            if grab(b, ob) and grab(new, on):
                cells.append((b.stem, Image.open(ob).convert("RGB"), Image.open(on).convert("RGB")))
        if not cells:
            continue
        tw = 420
        thumbs = []
        for _, o, n in cells:
            for im in (o, n):
                thumbs.append(im.resize((tw, int(im.height * tw / im.width)), Image.LANCZOS))
        th = thumbs
        rh = max(t.height for t in th)
        sheet = Image.new("RGB", (tw * 2, rh * len(cells)), (12, 12, 12))
        for r, (_, _, _) in enumerate(cells):
            for c, t in enumerate(th[r * 2:r * 2 + 2]):
                sheet.paste(t, (c * tw, r * rh))
        out = outdir / f"cmp_{sheet_i // per + 1}.png"
        sheet.save(out)
        print(f"  {out}  镜号 {[c[0] for c in cells]}  (左=旧 右=新)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
