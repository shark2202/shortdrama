#!/usr/bin/env python3
"""上传包：把一集变成"可以直接传到平台后台"的文件夹 + 自检报告。

为什么不做自动上传（方案 §0.4「不做自动发布」）：
平台发布要账号、实名、版权声明、可能的频控与风控；把凭据放进脚本等于把账号交出去。
所以分工是：**机器把材料备齐并自检，人在平台后台粘贴上传**（一次人工动作 ≈ 1 分钟/集）。

包里有什么：
  episode.mp4            竖屏 1080×1920 主交付（硬链接，不占额外空间）
  cover_<job>.jpg        封面
  release_meta.json      标题/简介/标签/时长/分辨率（从成片实测）
  subtitles.srt          字幕（已烧进画面，这份用于平台自带字幕轨）
  文案.txt               可直接粘贴的标题 / 简介 / 话题标签
  CHECKS.md              交付前自检：阻断项、字幕覆盖、响度/峰值、文件齐不齐、sha256

用法：
  python3 tools/make_upload_pack.py --root /home/a/ai-work/duanju
  python3 tools/make_upload_pack.py --root ... --episode E03_P --zip
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shortdrama import episode  # noqa: E402

OUT_NAME = "_upload"


def sha256(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            b = fh.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def link_or_copy(src: Path, dst: Path) -> str:
    """优先硬链接（同盘不占空间、看起来仍是普通文件），失败再退回复制。"""
    if dst.exists():
        dst.unlink()
    try:
        os.link(src, dst)
        return "hardlink"
    except OSError:
        shutil.copy2(src, dst)
        return "copy"


def checks_for(job: Path) -> list[tuple[str, str, bool]]:
    """交付前自检：(项, 说明, 是否通过)"""
    out: list[tuple[str, str, bool]] = []

    rp = job / "report.json"
    rep = json.loads(rp.read_text(encoding="utf-8")) if rp.exists() else {}
    block = rep.get("blocking", [])
    probe = rep.get("episode_probe", {})
    audio = probe.get("audio_measured", {}) or {}
    out.append(("质检阻断", f"{len(block)} 项" + ("" if not block else "：" + "；".join(b["message"] for b in block[:3])),
                not block))
    cov = probe.get("subtitle_coverage", "?")
    try:
        cov_ok = int(str(cov).split("/")[0]) >= 0.4 * int(str(cov).split("/")[1])
    except Exception:
        cov_ok = False
    out.append(("字幕覆盖", f"{cov}（qc 口径，去重后）", cov_ok))
    if audio:
        i = audio.get("integrated_lufs")
        pk = audio.get("sample_peak_dbfs")
        lra = audio.get("lra_lu")
        out.append(("响度", f"I={i} LUFS（目标 -16±1）", i is not None and abs(i + 16) <= 1.5))
        out.append(("峰值", f"{pk} dBFS（≤ -1.0）", pk is not None and pk <= -1.0))
        out.append(("动态范围", f"LRA={lra} LU（≤ 14）", lra is not None and lra <= 14))

    rv = job / "qc" / "review.md"
    rv = job / "qc" / "review.md"
    if rv.exists():
        import re
        text = rv.read_text(encoding="utf-8")
        lines = text.splitlines()
        # 表头里找"结论"列的下标（表格列会随版本变，别写死）
        vidx = None
        for l in lines:
            if l.startswith("|") and "结论" in l:
                cols = [c.strip() for c in l.split("|")]
                vidx = cols.index("结论") if "结论" in cols else None
                break
        rows = [l for l in lines if re.match(r"^\| \d+ \|", l)]
        bad, unfilled = 0, 0
        for r in rows:
            cols = r.split("|")
            verdict = cols[vidx].strip() if (vidx is not None and vidx < len(cols)) else ""
            if re.search(r"重出|修词|返工", verdict) and "☐" not in verdict:
                bad += 1
            elif "☐" in verdict and not re.search(r"[✅☑√✓]", verdict):
                unfilled += 1        # 模板里的空复选框 = 没人判过
            elif not re.search(r"合格|通过|OK|[✅☑√✓]", verdict):
                unfilled += 1
                unfilled += 1
        out.append(("人工审片", f"{len(rows)} 镜，未通过 {bad} 镜，未填 {unfilled} 镜",
                    bool(rows) and bad == 0 and unfilled == 0))
    else:
        out.append(("人工审片", "缺 qc/review.md", False))

    need = ["episode.mp4", "release_meta.json", "subtitles.srt"]
    miss = [f for f in need if not (job / f).exists()]
    out.append(("交付件", f"缺 {miss}" if miss else "成片/元数据/字幕齐全", not miss))
    return out


def build_pack(job: Path, root: Path, make_zip: bool = False) -> Path:
    pack = root / OUT_NAME / job.name
    pack.mkdir(parents=True, exist_ok=True)

    meta_p = job / "release_meta.json"
    meta = json.loads(meta_p.read_text(encoding="utf-8")) if meta_p.exists() else {}
    cover = job / (meta.get("cover") or f"cover_{job.name}.jpg")

    linked = []
    for src in (job / "episode.mp4", cover, meta_p, job / "subtitles.srt"):
        if src.exists():
            linked.append((link_or_copy(src, pack / src.name), src.name))

    txt = [
        f"剧名：{meta.get('series_title', '-')}    集：第 {meta.get('episode_no', '-')} 集 "
        f"《{meta.get('episode_title', '-')}》",
        "",
        "【标题】" + str(meta.get("title", "-")),
        "",
        "【简介】" + str(meta.get("description", "-")),
        "",
        "【标签】" + " ".join("#" + t for t in meta.get("tags", [])),
        "",
        f"【规格】{meta.get('width')}×{meta.get('height')} / {meta.get('fps')}fps / "
        f"{meta.get('duration_s')}s（字幕已烧进画面；另附 subtitles.srt 供平台字幕轨）",
    ]
    (pack / "文案.txt").write_text("\n".join(txt) + "\n", encoding="utf-8")

    checks = checks_for(job)
    lines = [f"# 交付自检 · {job.name}", "",
             "| 项 | 实测 | 结论 |", "|---|---|---|"]
    for name, detail, ok in checks:
        lines.append(f"| {name} | {detail} | {'✅' if ok else '❌'} |")
    lines += ["", "## 文件指纹", ""]
    for how, name in linked:
        p = pack / name
        lines.append(f"- `{name}`（{how}） sha256 `{sha256(p)}`")
    lines += ["", "## 上传步骤（人工）",
              "",
              "1. 打开平台创作者后台 → 发布 → 选竖屏成片（`episode.mp4`）",
              "2. 封面传 `cover_*.jpg`；标题/简介/标签复制 `文案.txt`",
              "3. 标题带话题标签；简介保留钩子句（平台只显示前 ~55 字）",
              "4. 发布前确认：`CHECKS.md` 全 ✅；平台预览里字幕位置没被 UI 挡住",
              "5. 发布后把播放链接回填到本目录 `published.json`（便于复盘完播/评论）",
              ""]
    (pack / "CHECKS.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    if make_zip:
        import zipfile
        z = pack.with_suffix(".zip")
        with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in pack.iterdir():
                if p.is_file():
                    zf.write(p, p.name)
    return pack


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="生成平台上传包（材料 + 自检，不做自动上传）")
    ap.add_argument("--root", default="/home/a/ai-work/duanju")
    ap.add_argument("--episode", help="只做某集（如 E03_P）")
    ap.add_argument("--zip", action="store_true", help="同时打 zip")
    a = ap.parse_args(argv)

    root = Path(a.root).expanduser().resolve()
    eps = sorted(p for p in root.glob("D202611_001_E*") if p.is_dir() and (p / "episode.mp4").exists())
    if a.episode:
        eps = [p for p in eps if a.episode in p.name]
    if not eps:
        print(f"（{root} 下没有可交付的集）")
        return 1
    fail = 0
    for job in eps:
        pack = build_pack(job, root, make_zip=a.zip)
        checks = checks_for(job)
        bad = [n for n, _, ok in checks if not ok]
        flag = "✅" if not bad else "❌ " + "、".join(bad)
        print(f"[upload] {job.name}: {pack}{flag}")
        fail += bool(bad)
    print(f"[upload] 包目录：{root / OUT_NAME}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
