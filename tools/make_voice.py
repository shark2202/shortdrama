#!/usr/bin/env python3
"""对白配音：TTS 合成 → 与镜头对轨 → 与环境音混音 → 写回成片。

为什么必须做：竖屏短剧是听觉媒介，只有环境音+字幕不算"可用"。
H3 的音轨只有环境音（雨、门铃），没有人声 —— 台词得自己配。

关键设计：
* **念什么**：`dialogue_cn`（角色台词）+ 可朗读的 `subtitle_cn`（旁白/信息）。
  跳过纯信息卡（"2025.1.15"、"凌晨 03:03"）与纯省略号 —— 念出来很怪。
* **谁念**：按该镜的 `ref` 角色分配音色（c1 女声 / c2 男声 / c3 年长女声）。
* **怎么对轨**：每句落在**那一镜的开头**（镜长 5.17s，台词 ~2-4s）。
* **怎么混**：环境音压到 0.25 做底，人声 1.0，最后统一响度 + 限幅。

用法：
  python3 tools/make_voice.py <job-dir> [--dry]
  python3 tools/make_voice.py <job-dir> --aspect portrait
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shortdrama import episode  # noqa: E402

# 角色 → 音色（Kokoro 中文）
VOICE_BY_ROLE = {"c1": "zf_xiaobei", "c2": "zm_yunjian", "c3": "zm_yunxi"}
DEFAULT_VOICE = "zf_xiaoxiao"
INFO_CARD = re.compile(r"^[\d\s:.\-年月日号第整点分秒]+$")


def ffmpeg() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def load(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def speakable(text: str) -> bool:
    """这条字幕该不该念出来。"""
    t = (text or "").strip()
    if not t or re.fullmatch(r"[\s。，、…\.\?!？！]*", t):
        return False
    if INFO_CARD.match(t):          # 纯时间戳/日期
        return False
    return True


def collect_lines(job: Path) -> list[dict]:
    story = load(job / "story.json")
    out = []
    for sc in story.get("scenes", []):
        role = sc.get("ref") or sc.get("primary")
        for field in ("dialogue_cn", "subtitle_cn"):
            t = (sc.get(field) or "").strip()
            if not speakable(t):
                continue
            if any(l["scene"] == sc["id"] and l["text"] == t for l in out):
                continue        # 同一镜同一句只念一次
            out.append({"scene": sc["id"], "idx": sc["idx"], "text": t,
                        "role": role, "field": field})
    return out


def synth(line: dict, out_wav: Path, cache: bool = True) -> Path | None:
    if cache and out_wav.exists() and out_wav.stat().st_size > 1000:
        return out_wav
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")     # CPU：GPU 留给渲染
    from kokoro import KPipeline
    global _PIPE
    try:
        _PIPE
    except NameError:
        _PIPE = KPipeline(lang_code="z", repo_id="hexgrad/Kokoro-82M", device="cpu")
    import soundfile as sf
    voice = VOICE_BY_ROLE.get(line["role"], DEFAULT_VOICE)
    for gs, ps, au in _PIPE(line["text"], voice=voice, speed=1.05):
        sf.write(out_wav, au, 24000)
        return out_wav
    return None


def build_track(job: Path, lines: list[dict], durations: list[float]) -> Path | None:
    """把每句 wav 放到它那一镜的开头，拼成一条整片人声轨。"""
    vdir = job / "voice"
    vdir.mkdir(exist_ok=True)
    starts, t = [], 0.0
    for d in durations:
        starts.append(t)
        t += d
    inputs, delays, labels = [], [], []
    for i, (line, wav) in enumerate(zip(lines, vdir_files(job, lines))):
        if wav is None:
            continue
        start = starts[line["idx"] - 1] + 0.35
        ms = int(start * 1000)
        inputs += ["-i", str(wav)]
        delays.append(f"[{i}:a]adelay={ms}|{ms}[v{i}]")
        labels.append(f"[v{i}]")
    if not inputs:
        return None
    track = vdir / "track.wav"
    cmd = [ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", *inputs]
    filt = ";".join(delays) + ";" + "".join(labels) + \
        f"amix=inputs={len(labels)}:normalize=0:duration=longest[out]"
    cmd += ["-filter_complex", filt, "-map", "[out]", "-ar", "24000", "-ac", "1", str(track)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print("[voice] 人声轨合成失败：", r.stderr[-800:])
        return None
    return track


def vdir_files(job: Path, lines: list[dict]) -> list[Path | None]:
    vdir = job / "voice"
    out = []
    for line in lines:
        p = vdir / f"{line['scene']}_{line['field']}.wav"
        out.append(p if p.exists() else None)
    return out


def mix(job: Path, track: Path) -> bool:
    """人声 + 环境音 → 写回 episode.mp4（视频流 copy，不重编码画面）。"""
    ep = job / "episode.mp4"
    if not ep.exists():
        return False
    tmp = job / "episode_vo.mp4"
    cmd = [ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
           "-i", str(ep), "-i", str(track),
           "-filter_complex",
           "[0:a]volume=0.45[bg];[1:a]volume=1.0[vo];"
           "[bg][vo]amix=inputs=2:normalize=0:duration=first,"
           "loudnorm=I=-15:TP=-2.5:LRA=11,alimiter=limit=0.72:level=disabled[a]",
           "-map", "0:v", "-map", "[a]",
           "-c:v", "copy", "-c:a", "aac", "-ar", "48000", "-b:a", "192k",
           "-movflags", "+faststart", str(tmp)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print("[voice] 混音失败：", r.stderr[-800:])
        return False
    tmp.replace(ep)
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("job")
    ap.add_argument("--dry", action="store_true", help="只列出要念的句子")
    a = ap.parse_args()
    job = Path(a.job).expanduser().resolve()
    lines = collect_lines(job)
    shots = load(job / "shots.json")["shots"]
    clips = [job / "clips" / f"{s['idx']:02d}.mp4" for s in shots]
    durations = [episode.probe(c)["duration"] for c in clips if c.exists()]
    print(f"[voice] {job.name}: 要念 {len(lines)} 句 / 共 {len(shots)} 镜")
    for l in lines:
        print(f"   {l['scene']:>4} [{l['role']}] {l['text']}")
    if a.dry:
        return 0

    vdir = job / "voice"
    vdir.mkdir(exist_ok=True)
    ok = 0
    for line in lines:
        wav = vdir / f"{line['scene']}_{line['field']}.wav"
        if synth(line, wav):
            ok += 1
        else:
            print(f"[voice] 合成失败：{line['scene']} {line['text']}")
    print(f"[voice] 合成 {ok}/{len(lines)} 句")
    track = build_track(job, lines, durations)
    if not track:
        print("[voice] 没有可混的音轨")
        return 1
    print(f"[voice] 人声轨 {track}（{episode.probe(track)['duration']:.1f}s）")
    if mix(job, track):
        print(f"[voice] 已混音写回 {job/'episode.mp4'}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
