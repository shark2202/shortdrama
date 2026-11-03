#!/usr/bin/env python3
"""一集短剧的驱动脚本：直接打 ComfyUI 的 HTTP API（复用 h3turing 客户端 + 现成工作流）。

不重造轮子：
* ComfyUI 客户端（上传/提交/轮询/下载/重试）→ 已装好的 `h3turing` 包
* 出图工作流 → minimax-h3-turing/workflows/api/zimage_turbo_t2i_api.json
* 出镜工作流 → minimax-h3-turing/workflows/api/h3_w4a8_ref2va_api.json
* 3x 放大     → minimax-h3-turing/scripts/upscale_video.py

只补现有脚本没覆盖的三件事：每镜换角色、烧字幕+统一响度、自动质检。

阶段（可单独重跑）：
  cast     出定妆候选图（Z-Image 中文 t2i）
  pick     按 cast/selection.json 落地主图 cast/<id>.png + 备图 cast/<id>_alt.png
  shots    story.json → shots.json（H3 提示词句式固定，不让模型自由发挥）
  render   逐镜出镜（H3 ref2va），断点续跑粒度 = 单镜
  assemble 拼接 → 3x 放大 → 烧字幕 + 统一响度 → episode.mp4
  qc       自动质检 → report.json

用法：
  /home/a/ai-work/.venv/bin/python shortdrama/episode.py <job-dir> [阶段 ...]
"""

from __future__ import annotations

import copy
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]          # workspace/
H3 = REPO / "minimax-h3-turing"
WORKFLOWS = H3 / "workflows" / "api"
COMFY = "http://127.0.0.1:8188"
SEED = 3013
CANDIDATES = 3          # 每个角色出几张定妆候选
RETRIES = 3             # 单镜失败重试上限（每次换 seed）
DELIVERY = (1920, 1056)
FPS = 24

sys.path.insert(0, str(H3 / "python" / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shortdrama import facecheck  # noqa: E402


def comfy():
    """懒加载 ComfyUI 客户端：纯函数（分镜/字幕）不依赖它，方便单测。"""
    try:
        from h3turing.client import ComfyUI
    except ImportError:
        raise SystemExit("缺少 h3turing 包：用 /home/a/ai-work/.venv/bin/python 运行")
    return ComfyUI(COMFY)


def collect(history: dict) -> list[dict]:
    from h3turing.client import collect_outputs
    return collect_outputs(history)


# ------------------------------------------------------------------ 工具


def ffmpeg() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        exe = shutil.which("ffmpeg")
        if exe:
            return exe
    raise SystemExit("找不到 ffmpeg：用 /home/a/ai-work/.venv/bin/python 跑")


def probe(path: Path) -> dict:
    """读视频信息。本机没有 ffprobe，用 ffmpeg -i 解析 stderr。"""
    out = subprocess.run([ffmpeg(), "-hide_banner", "-i", str(path)],
                         capture_output=True, text=True).stderr
    info = {"w": 0, "h": 0, "fps": 0.0, "duration": 0.0, "audio": False}
    m = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", out)
    if m:
        info["duration"] = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    m = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", out)
    if m:
        info["w"], info["h"] = int(m.group(1)), int(m.group(2))
    m = re.search(r"Video:.*?([\d.]+) fps", out)
    if m:
        info["fps"] = float(m.group(1))
    info["audio"] = "Audio:" in out
    return info


def srt_time(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def run(cmd: list[str], cwd: Path | None = None) -> str:
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"命令失败({r.returncode}) {' '.join(cmd[:5])}…\n{r.stderr[-1500:]}")
    return r.stderr


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def dump(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ------------------------------------------------------------------ 阶段


def stage_cast(job: Path, n: int = CANDIDATES) -> None:
    """每个角色出 n 张定妆候选（Z-Image，中文提示词）。

    先过 facecheck：造型描述不够具体就出"大众脸"，而定妆图是全剧一致性的天花板
    （方案 §9.2）—— 造型不合格的图渲 35 镜 = 35 镜都白费。
    """
    story = load(job / "story.json")
    problems = facecheck.check_characters(story.get("characters", []))
    problems += facecheck.style_pollution(story)
    if problems:
        for p in problems:
            print(f"[cast] ⚠ {p}", flush=True)
        raise SystemExit(
            "人物造型检查未通过（上面 N 条）——先改 story.json 的 desc_cn，"
            "或用 tools/fix_characters.py 让本地 LLM 重写到通过。"
            "宁可现在多花 2 分钟改文字，也不要渲 35 镜白脸。")
    (job / "cast").mkdir(parents=True, exist_ok=True)
    client = comfy()
    template = load(WORKFLOWS / "zimage_turbo_t2i_api.json")
    index = {}
    for ch in story["characters"]:
        cid = ch["id"]
        # 定妆图提示词 = 身份锚点 + 造型 + 定妆后缀。
        # **不能**混入 style_cn：风格词（霓虹洋红/低照度/胶片颗粒）会覆盖
        # cast_prompt_cn 里的"中性柔光、纯灰背景"，模型顺势出美颜网红脸，
        # 而且带色光污染 ref2va 的参考图。定妆图要的是干净中性光。
        prompt = facecheck.cast_prompt(ch, story)
        cands = []
        for k in range(1, n + 1):
            graph = copy.deepcopy(template)
            for node in graph.values():
                ins = node.get("inputs", {})
                if ins.get("text") == "__PROMPT__":
                    ins["text"] = prompt
                if "seed" in ins:
                    ins["seed"] = SEED + k
            dest = job / "cast" / f"{cid}_cand{k}.png"
            if dest.exists():
                print(f"[cast] {dest.name} 已存在，跳过（删掉它可重出）", flush=True)
                cands.append({"file": dest.name, "seed": SEED + k, "seconds": 0.0,
                              "prompt": prompt})
                continue
            t0 = time.time()
            files = collect(client.render(graph, max_s=900))
            if not files:
                raise RuntimeError(f"{cid} 第 {k} 张候选没有产物")
            client.download(files[0], dest)
            cands.append({"file": dest.name, "seed": SEED + k,
                          "seconds": round(time.time() - t0, 1), "prompt": prompt})
            print(f"[cast] {dest.name} {cands[-1]['seconds']}s", flush=True)
        index[cid] = cands
    dump(job / "cast" / "index.json", index)
    print(f"[cast] 挑图：写 {job/'cast'/'selection.json'}，形如 "
          '{"c1": {"main": "cand1", "alt": "cand2"}}')


def stage_pick(job: Path) -> None:
    """把选中的候选落地成主图/备图。

    **备图必须是主图的 i2i 派生**（同一张脸），不能直接用另一张 t2i 候选——
    不同 seed 出的是不同的人，而 ref2va 的 <Picture 2> 要求"同一个角色的另一张"，
    用错人脸 = 跨镜崩脸（方案 R3）。方案 §9.2 写的就是：主图 + i2i(denoise 0.6-0.75) 派生。
    """
    sel_file = job / "cast" / "selection.json"
    if not sel_file.exists():
        raise SystemExit(f"缺少 {sel_file}")
    client = comfy()
    i2i = load(WORKFLOWS / "zimage_turbo_i2i_api.json")
    for cid, pick in load(sel_file).items():
        main = job / "cast" / f"{cid}_{pick['main']}.png"
        if not main.exists():
            raise SystemExit(f"找不到 {main}")
        shutil.copyfile(main, job / "cast" / f"{cid}.png")
        alt_dest = job / "cast" / f"{cid}_alt.png"
        if alt_dest.exists():
            print(f"[pick] {cid}: alt 已存在，跳过", flush=True)
            continue
        # 从主图派生备图：上传主图 → i2i → 存回 alt
        ref = client.upload_image(job / "cast" / f"{cid}.png")
        graph = copy.deepcopy(i2i)
        for node in graph.values():
            ins = node.get("inputs", {})
            if ins.get("image") == "__IMAGE__":
                ins["image"] = ref
            if "seed" in ins:
                ins["seed"] = SEED + 100
            if "denoise" in ins:
                ins["denoise"] = 0.65
        files = collect(client.render(graph, max_s=900))
        if not files:
            raise SystemExit(f"{cid} 备图（i2i 派生）没有产物")
        client.download(files[0], alt_dest)
        print(f"[pick] {cid}: main={main.name} alt=i2i 派生", flush=True)

def build_shots(story: dict) -> list[dict]:
    """story.json 的场景 → H3 提示词（句式固定：实测出片的句式，别让模型自由发挥）。

    "<Picture 1> and <Picture 2> show the same character. <动作/镜头/光线>, <风格>, natural ambient audio: <环境音>"
    """
    style = story.get("style_en", "").strip(" ,")
    shots = []
    for sc in story["scenes"]:
        chars = sc.get("chars") or []
        # ref 显式声明（None = 镜内无人 → t2v）；旧剧本没这个字段时回退 primary
        ref = sc["ref"] if "ref" in sc else sc.get("primary")
        body = sc.get("prompt_en", "").strip().rstrip(".")
        if ref:
            # 有角色在场：ref2va 的固定句式（实测出片的句式，别让模型自由发挥）
            prompt = "<Picture 1> and <Picture 2> show the same character. " + body
        else:
            # 物镜/环境/剪影/监控回放：加角色句式会让 H3 去画人而不是画那个东西
            prompt = body
        if style:
            prompt += f", {style}"
        prompt += f", natural ambient audio: {sc.get('sfx_en', '')}"
        shots.append({
            "idx": sc["idx"], "id": sc["id"], "shot_type": sc.get("shot_type", ""),
            "character": ref,
            "ref": ref,
            "characters": chars,
            "prompt_en": prompt,
            "subtitle_cn": sc.get("subtitle_cn", ""),
            "sfx_en": sc.get("sfx_en", ""),
            "duration_s": sc.get("duration_s"),
            "beat_cn": sc.get("beat", ""),
        })
    return shots


def stage_shots(job: Path) -> None:
    shots = build_shots(load(job / "story.json"))
    dump(job / "shots.json", {"shots": shots})
    print(f"[shots] {len(shots)} 镜 → {job/'shots.json'}")


def stage_render(job: Path) -> None:
    """逐镜出镜（H3）。已存在的 clips/NN.mp4 跳过 = 单镜粒度续跑。

    两类镜走两套工作流：
    - ref2va（有角色在场）：带两张定妆图 + "<Picture 1> and <Picture 2> show the same character"
    - t2v（物镜/环境/剪影/监控回放）：不带任何参考图，也不加角色句式
    """
    story = load(job / "story.json")
    shots = load(job / "shots.json")["shots"]
    (job / "clips").mkdir(parents=True, exist_ok=True)
    client = comfy()
    tpl_ref2va = load(WORKFLOWS / "h3_w4a8_ref2va_api.json")
    tpl_t2v = load(WORKFLOWS / "h3_w4a8_t2v_bypass_api.json")

    refs = {}
    for ch in story["characters"]:
        cid = ch["id"]
        main = job / "cast" / f"{cid}.png"
        if not main.exists():
            raise SystemExit(f"缺少定妆图 {main}（先跑 cast + pick）")
        alt = job / "cast" / f"{cid}_alt.png"
        refs[cid] = (client.upload_image(main),
                     client.upload_image(alt if alt.exists() else main))

    for shot in shots:
        idx = shot["idx"]
        dest = job / "clips" / f"{idx:02d}.mp4"
        if dest.exists():
            print(f"[render] shot {idx:02d} 已存在，跳过", flush=True)
            continue
        ref = shot.get("ref")
        r1, r2 = refs[ref] if ref else (None, None)
        for attempt in range(RETRIES):
            graph = copy.deepcopy(tpl_ref2va if ref else tpl_t2v)
            for node in graph.values():
                ins = node.get("inputs", {})
                if ins.get("prompt") == "__H3_PROMPT__":
                    ins["prompt"] = shot["prompt_en"]
                if ins.get("image") == "__H3_REF_IMAGE_1__":
                    ins["image"] = r1
                elif ins.get("image") == "__H3_REF_IMAGE_2__":
                    ins["image"] = r2
                if "noise_seed" in ins:
                    ins["noise_seed"] = SEED + idx + attempt
                if "seed" in ins and "noise_seed" not in ins:
                    ins["seed"] = SEED + idx + attempt
            t0 = time.time()
            try:
                files = collect(client.render(graph, max_s=3600))
            except Exception as e:
                print(f"[render] shot {idx:02d} 第 {attempt+1} 次失败：{e}", flush=True)
                continue
            vids = [f for f in files if f.get("filename", "").lower().endswith((".mp4", ".webm"))]
            if not vids:
                print(f"[render] shot {idx:02d} 没有视频产物：{files}", flush=True)
                continue
            client.download(vids[0], dest)
            info = probe(dest)
            print(f"[render] shot {idx:02d} → {dest.name} {time.time()-t0:.0f}s "
                  f"{info['w']}x{info['h']} {info['duration']:.2f}s audio={info['audio']}", flush=True)
            break
        else:
            raise SystemExit(f"第 {idx} 镜渲染失败")


def build_srt(shots: list[dict], durations: list[float]) -> str:
    """字幕时间轴按**实际**每镜时长排。"""
    lines, t = [], 0.0
    for shot, dur in zip(shots, durations):
        text = (shot.get("subtitle_cn") or "").strip()
        if text:
            lines += [f"{len(lines)//4+1}", f"{srt_time(t)} --> {srt_time(t+dur)}", text, ""]
        t += dur
    return "\n".join(lines)


def stage_assemble(job: Path) -> None:
    """拼接 → 3x 放大 → 烧字幕 + 统一响度 → episode.mp4。

    与方案 §9.5 有一处故意的差异：**先放大再烧字幕**，字幕在 1920×1056 上渲染，
    比先烧 640×352 再放大清晰（文字边缘不糊）。
    """
    shots = load(job / "shots.json")["shots"]
    clips = [job / "clips" / f"{s['idx']:02d}.mp4" for s in shots]
    missing = [c.name for c in clips if not c.exists()]
    if missing:
        raise SystemExit(f"缺镜头：{missing}")

    lst = job / "clips" / "concat.txt"
    lst.write_text("".join(f"file '{c.resolve()}'\n" for c in clips), encoding="utf-8")
    concat = job / "clips" / "concat.mp4"
    run([ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-f", "concat",
         "-safe", "0", "-i", str(lst), "-c", "copy", str(concat)])

    durations = [probe(c)["duration"] for c in clips]
    (job / "subtitles.srt").write_text(build_srt(shots, durations), encoding="utf-8")

    up = job / "clips" / "upscaled.mp4"
    run([sys.executable, str(H3 / "scripts" / "upscale_video.py"),
         str(concat), str(up), "--scale", "3"])

    style = ("FontName=Noto Sans CJK SC,FontSize=42,PrimaryColour=&H00FFFFFF,"
             "OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=1,MarginV=56,Alignment=2")
    # 统一响度 + 限幅（H3 源音轨实测可能到 0 dBFS，削波必须在这里压住）
    audio_chain = "loudnorm=I=-16:TP=-1.5:LRA=11,alimiter=limit=0.891:attack=5:release=50"
    run([ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-i", str(up),
         "-vf", f"subtitles=subtitles.srt:force_style='{style}'",
         "-af", audio_chain,
         "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
         "-r", str(FPS), "-c:a", "aac", "-ar", "48000", "-b:a", "192k",
         "-movflags", "+faststart", str(job / "episode.mp4")], cwd=job)
    print(f"[assemble] {job/'episode.mp4'} {probe(job/'episode.mp4')}")


def stage_qc(job: Path) -> int:
    """自动质检：黑帧/静音/削波/时长/分辨率/帧率/缺镜 → report.json。"""
    shots = load(job / "shots.json")["shots"]
    findings: list[dict] = []

    def add(level, where, msg):
        findings.append({"level": level, "where": where, "message": msg})

    for shot in shots:
        clip = job / "clips" / f"{shot['idx']:02d}.mp4"
        if not clip.exists():
            add("block", clip.name, "缺镜头")
            continue
        info = probe(clip)
        want = shot.get("duration_s") or 0
        if abs(info["duration"] - want) > 0.5:
            add("warn", clip.name, f"时长 {info['duration']:.2f}s 与剧本 {want}s 偏差 > 0.5s")
        if (info["w"], info["h"]) != (640, 352):
            add("block", clip.name, f"分辨率 {info['w']}x{info['h']} != 640x352")
        if not info["audio"]:
            add("warn", clip.name, "无音轨")
        err = run([ffmpeg(), "-hide_banner", "-i", str(clip),
                   "-vf", "blackdetect=d=0.4:pix_th=0.10", "-f", "null", "-"])
        if "black_start" in err:
            add("block", clip.name, "检出黑帧")
        err = run([ffmpeg(), "-hide_banner", "-i", str(clip),
                   "-af", "silencedetect=n=-50dB:d=1.0", "-f", "null", "-"])
        if "silence_start" in err:
            add("warn", clip.name, "检出静音段")
        err = run([ffmpeg(), "-hide_banner", "-i", str(clip),
                   "-af", "volumedetect", "-f", "null", "-"])
        m = re.search(r"max_volume:\s*(-?\d+(?:\.\d+)?) dB", err)
        if m and float(m.group(1)) > -0.3:
            # 源镜头的削波由后期 limiter 兜住，所以镜头级只提示，成片级才阻断
            add("warn", clip.name, f"削波 max_volume={m.group(1)} dB（后期限幅兜底）")

    ep_path = job / "episode.mp4"
    ep = {"exists": ep_path.exists()}
    if ep_path.exists():
        info = probe(ep_path)
        ep.update(info)
        if (info["w"], info["h"]) != DELIVERY:
            add("block", ep_path.name, f"交付分辨率 {info['w']}x{info['h']}")
        if abs(info["fps"] - FPS) > 0.5:
            add("block", ep_path.name, f"帧率 {info['fps']}")
        want = sum(s.get("duration_s") or 0 for s in shots)
        if abs(info["duration"] - want) > 0.5:
            add("warn", ep_path.name, f"成片时长 {info['duration']:.2f}s 与剧本 {want}s 偏差 > 0.5s")
        err = run([ffmpeg(), "-hide_banner", "-i", str(ep_path),
                   "-vf", "blackdetect=d=0.4:pix_th=0.10", "-f", "null", "-"])
        if "black_start" in err:
            add("block", ep_path.name, "成片检出黑帧")
        err = run([ffmpeg(), "-hide_banner", "-i", str(ep_path),
                   "-af", "volumedetect", "-f", "null", "-"])
        m = re.search(r"max_volume:\s*(-?\d+(?:\.\d+)?) dB", err)
        if m and float(m.group(1)) > -0.3:
            add("block", ep_path.name, f"成片削波 max_volume={m.group(1)} dB")
    else:
        add("block", ep_path.name, "没有成片")

    report = {"episode": job.name, "checked": time.strftime("%F %T"),
              "episode_probe": ep, "findings": findings,
              "blocking": [f for f in findings if f["level"] == "block"]}
    dump(job / "report.json", report)
    for f in findings:
        print(f"[qc] {f['level']:4s} {f['where']}: {f['message']}")
    print(f"[qc] {'阻断 ' + str(len(report['blocking'])) + ' 项' if report['blocking'] else '通过'}"
          f"（{job/'report.json'}）")
    return 1 if report["blocking"] else 0


STAGES = {"cast": stage_cast, "pick": stage_pick, "shots": stage_shots,
          "render": stage_render, "assemble": stage_assemble, "qc": stage_qc}


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    job = Path(argv[1]).expanduser().resolve()
    stages = argv[2:] or ["shots", "render", "assemble", "qc"]
    for name in stages:
        if name not in STAGES:
            print(f"未知阶段 {name}；可选：{' '.join(STAGES)}", file=sys.stderr)
            return 2
        print(f"===== stage: {name} =====", flush=True)
        rc = STAGES[name](job)
        if rc:
            return rc
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
