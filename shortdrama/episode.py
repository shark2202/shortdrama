#!/usr/bin/env python3
"""一集短剧的驱动脚本：直接打 ComfyUI 的 HTTP API（复用 h3turing 客户端 + 现成工作流）。

不重造轮子：
* ComfyUI 客户端（上传/提交/轮询/下载/重试）→ 已装好的 `h3turing` 包
* 出图工作流 → minimax-h3-turing/workflows/api/zimage_turbo_t2i_api.json
* 出镜工作流 → minimax-h3-turing/workflows/api/h3_w4a8_ref2va_api.json
* 3x 放大     → minimax-h3-turing/scripts/upscale_video.py

只补现有脚本没覆盖的三件事：每镜换角色、烧字幕+统一响度、自动质检。

阶段（可单独重跑）：
  lint      剧本预检：英文提示词/角色引用/时长/镜数 + 造型检查（毁集级错误挡在 10 秒处）
  cast      出定妆候选图（Z-Image 中文 t2i）
  pick      按 cast/selection.json 落地主图 cast/<id>.png + 备图 cast/<id>_alt.png
  shots     story.json → shots.json（H3 提示词句式固定，不让模型自由发挥）
  frames    每镜 1 张关键帧图（Z-Image 中文 t2i，10s/镜）→ frames/NN.png（审片用）
  render    逐镜出镜（H3 ref2va/t2v，文本驱动构图），断点续跑粒度 = 单镜
  render-i2v 同上，但用关键帧当首帧（构图 100% 可控）
  review    关键帧拼图 + 视频抽帧拼图 + clips/manifest.json（一屏审片）
  assemble  拼接 → 3x 放大 → 烧字幕 + 统一响度 → episode.mp4
  qc        自动质检 → report.json

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
DELIVERY = (1920, 1056)          # 横屏交付（640×352 的 3×）
# H3 原生支持竖屏（实测 352×640 出片正常）→ 竖屏应该**原生生成**，不是后期裁切。
# 360×640 是精确 9:16，且 3× 放大正好 = 1080×1920（抖音/红果/快手的原生竖屏规格）。
FRAME_LANDSCAPE = (640, 352)
FRAME_PORTRAIT = (352, 640)      # H3 把宽圆整到 16 的倍数（360→352）；交付时再精确缩到 1080×1920
DELIVERY_PORTRAIT = (1080, 1920)


def frame_size(aspect: str) -> tuple[int, int]:
    return FRAME_PORTRAIT if aspect == "portrait" else FRAME_LANDSCAPE


def delivery_size(aspect: str) -> tuple[int, int]:
    return DELIVERY_PORTRAIT if aspect == "portrait" else DELIVERY
FPS = 24

sys.path.insert(0, str(H3 / "python" / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shortdrama import facecheck, grade, loudness  # noqa: E402


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
    # 角色的英文外形锚点：ref2va 光靠参考图撑人脸，参考图一漂就没第二重约束
    # （实测 E02/E03 的周姐有 9 镜出成年轻男性）。H3 只吃英文，所以必须写在这里。
    traits = {c["id"]: (c.get("traits_en") or "").strip() for c in story.get("characters", [])}
    shots = []
    for sc in story["scenes"]:
        chars = sc.get("chars") or []
        # ref 显式声明（None = 镜内无人 → t2v）；旧剧本没这个字段时回退 primary
        ref = sc["ref"] if "ref" in sc else sc.get("primary")
        body = sc.get("prompt_en", "").strip().rstrip(".")
        if ref:
            # 有角色在场：ref2va 的固定句式（实测出片的句式，别让模型自由发挥）
            prompt = "<Picture 1> and <Picture 2> show the same character."
            if traits.get(ref):
                prompt += " " + traits[ref] + ","
            prompt += " " + body
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
            "dialogue_cn": sc.get("dialogue_cn", ""),
            "sfx_en": sc.get("sfx_en", ""),
            "duration_s": sc.get("duration_s"),
            "beat_cn": sc.get("beat", ""),
        })
    return shots


def stage_shots(job: Path) -> None:
    shots = build_shots(load(job / "story.json"))
    dump(job / "shots.json", {"shots": shots})
    print(f"[shots] {len(shots)} 镜 → {job/'shots.json'}")


def lint_story(story: dict) -> list[dict]:
    """剧本预检（纯函数）：返回 [{level, where, message}]，block = 会毁掉一集。"""
    problems: list[dict] = []

    def add(level, where, msg):
        problems.append({"level": level, "where": where, "message": msg})

    try:  # 并行会话的剧本/造型检查（有就用，没有不阻塞）
        from shortdrama import facecheck, textcheck
        for c in story.get("characters", []):
            for p in facecheck.check_character(c):
                add("warn", f"character {c.get('id')}", p)
        for p in textcheck.check_story(story):
            add("warn", "story", p)
    except Exception:   # 并行会话的模块还在改，坏了也不该拖垮本阶段
        pass

    scenes = story.get("scenes", [])
    ids = {c.get("id") for c in story.get("characters", [])}
    if story.get("shot_count") and story["shot_count"] != len(scenes):
        add("block", "story", f"shot_count={story['shot_count']} 与 scenes 数 {len(scenes)} 不符")
    if [s.get("idx") for s in scenes] != list(range(1, len(scenes) + 1)):
        add("block", "story", "镜头 idx 不连续（应为 1..N）")
    for s in scenes:
        where = f"scene {s.get('id') or s.get('idx')}"
        pe = s.get("prompt_en", "")
        if not pe.strip():
            add("block", where, "prompt_en 为空")
        elif any(ord(ch) > 0x2E80 for ch in pe):
            add("block", where, "prompt_en 含中文（H3 的文本编码器只吃英文）")
        if not s.get("sfx_en", "").strip():
            add("warn", where, "没有 sfx_en → 这一镜会没有声音")
        if not s.get("subtitle_cn", "").strip():
            add("warn", where, "没有 subtitle_cn → 这一镜没有字幕")
        if not s.get("prompt_cn", "").strip():
            add("warn", where, "没有 prompt_cn → 关键帧只能拿 beat 凑，构图会偏")
        ref = s.get("ref") if "ref" in s else s.get("primary")
        if ref and ref not in ids:
            add("block", where, f"ref/primary 角色 {ref} 不存在")
        if s.get("duration_s") and abs(s["duration_s"] - 5.2) > 0.05:
            add("warn", where, f"duration_s={s['duration_s']} 不是 5.2（H3 每镜固定 124 帧 @24fps）")
    total = sum(s.get("duration_s") or 0 for s in scenes)
    if story.get("duration_s") and abs(total - story["duration_s"]) > 0.5:
        add("warn", "story", f"各镜时长合计 {total:.1f}s 与 duration_s={story['duration_s']} 不符")
    return problems


def stage_lint(job: Path) -> int:
    """剧本预检（跑在出图之前）：把"注定要废一集"的错挡在 10 秒成本处。"""
    problems = lint_story(load(job / "story.json"))
    for p in problems:
        print(f"[lint] {p['level']:4s} {p['where']}: {p['message']}")
    blocking = [p for p in problems if p["level"] == "block"]
    print(f"[lint] {'阻断 ' + str(len(blocking)) + ' 项' if blocking else '通过'}"
          f"（{len(problems)} 个问题）")
    return 1 if blocking else 0


FRAME_W, FRAME_H = 1024, 576   # 16:9，与 H3 的 640×352 比例接近


def frame_prompt(story: dict, sc: dict, ch: dict | None) -> str:
    """关键帧中文提示词：画面描述 + 该镜角色的造型 + 全剧风格。"""
    parts = [sc.get("prompt_cn") or sc.get("beat", ""),
             ch.get("desc_cn", "") if ch else "",
             story.get("style_cn", "")]
    return "，".join(p.strip("，。 ") for p in parts if p.strip())


def stage_frames(job: Path) -> None:
    """每镜出 1 张关键帧图（Z-Image 中文 t2i，10s/张）。

    为什么先出图：视频一镜 210s，图一镜 10s。把"剧本/构图/人物对不对"的判断
    从 21 分钟后提前到 1 分钟后 —— 错了重出的是图（10s），不是视频（210s）。
    关键帧还能当 i2v 首帧（render-i2v），构图 100% 可控。
    """
    story = load(job / "story.json")
    shots = load(job / "shots.json")["shots"] if (job / "shots.json").exists() else build_shots(story)
    chars = {c["id"]: c for c in story.get("characters", [])}
    scenes = {s["idx"]: s for s in story.get("scenes", [])}
    (job / "frames").mkdir(parents=True, exist_ok=True)
    client = comfy()
    template = load(WORKFLOWS / "zimage_turbo_t2i_api.json")
    for shot in shots:
        dest = job / "frames" / f"{shot['idx']:02d}.png"
        if dest.exists():
            print(f"[frames] {dest.name} 已存在，跳过", flush=True)
            continue
        sc = scenes.get(shot["idx"], {})
        ch = chars.get(shot.get("ref") or shot.get("character"))
        prompt = frame_prompt(story, sc, ch)
        graph = copy.deepcopy(template)
        for node in graph.values():
            ins = node.get("inputs", {})
            if ins.get("text") == "__PROMPT__":
                ins["text"] = prompt
            if ins.get("seed") is not None:
                ins["seed"] = SEED + shot["idx"]
            if node.get("class_type") == "EmptySD3LatentImage":
                ins["width"], ins["height"] = FRAME_W, FRAME_H
        t0 = time.time()
        files = collect(client.render(graph, max_s=900))
        if not files:
            raise RuntimeError(f"第 {shot['idx']} 镜关键帧没有产物")
        client.download(files[0], dest)
        print(f"[frames] {dest.name} {time.time()-t0:.0f}s  {prompt[:50]}…", flush=True)


def stage_render(job: Path, first_frame: bool = False) -> None:
    """逐镜出镜（H3）。已存在的 clips/NN.mp4 跳过 = 单镜粒度续跑。

    两类镜走两套工作流：
    - ref2va（有角色在场）：带两张定妆图 + "<Picture 1> and <Picture 2> show the same character"
    - t2v（物镜/环境/剪影/监控回放）：不带任何参考图，也不加角色句式
    """
    story = load(job / "story.json")
    shots = load(job / "shots.json")["shots"]
    aspect = story.get("aspect", "landscape")   # landscape | portrait（竖屏原生生成）
    (job / "clips").mkdir(parents=True, exist_ok=True)
    client = comfy()
    tpl_ref2va = load(WORKFLOWS / "h3_w4a8_ref2va_api.json")
    tpl_t2v = load(WORKFLOWS / "h3_w4a8_t2v_bypass_api.json")
    tpl_i2v = load(WORKFLOWS / "h3_w4a8_i2v_bypass_api.json")

    refs = {}
    for ch in story.get("characters", []) if not first_frame else []:
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
        r1, r2 = refs.get(ref, (None, None)) if ref else (None, None)
        kf_name = None
        if first_frame:          # 关键帧当首帧：构图 100% 可控（i2v 路线）
            kf = job / "frames" / f"{idx:02d}.png"
            if not kf.exists():
                raise SystemExit(f"缺少关键帧 {kf}（先跑 frames）")
            kf_name = client.upload_image(kf)
        for attempt in range(RETRIES):
            graph = copy.deepcopy(tpl_i2v if first_frame else (tpl_ref2va if ref else tpl_t2v))
            for node in graph.values():
                ins = node.get("inputs", {})
                if ins.get("prompt") == "__H3_PROMPT__":
                    ins["prompt"] = shot["prompt_en"]
                if ins.get("image") == "__H3_REF_IMAGE_1__":
                    ins["image"] = r1
                elif ins.get("image") == "__H3_REF_IMAGE_2__":
                    ins["image"] = r2
                elif ins.get("image") == "__H3_FIRST_FRAME__":
                    ins["image"] = kf_name
                if "noise_seed" in ins:
                    ins["noise_seed"] = SEED + idx + attempt
                if "seed" in ins and "noise_seed" not in ins:
                    ins["seed"] = SEED + idx + attempt
                # 竖屏：H3 原生支持 portrait（实测 352×640 出片正常），
                # 不必后期裁切 —— 360×640 精确 9:16，3× 放大正好 1080×1920
                if ("width" in ins and "height" in ins
                        and str(node.get("class_type", "")).startswith("MiniMaxH3")):
                    ins["width"], ins["height"] = frame_size(aspect)
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


def _fresh(path: Path, deps: list[Path]) -> bool:
    """中间件是否比它依赖的产物新（复用中间件：重出 1 镜不必重拼/重放大全片）。"""
    if not path.exists():
        return False
    return all(d.exists() and d.stat().st_mtime <= path.stat().st_mtime for d in deps)


def _sheet(images: list[Path], out: Path, cols: int = 3) -> Path | None:
    """把若干图拼成一张对比图（ffmpeg tile）。图不足时用最后一张补齐。"""
    if not images:
        return None
    rows = (len(images) + cols - 1) // cols
    need = rows * cols
    tmp = out.parent / f"_sheet_{out.stem}"
    tmp.mkdir(parents=True, exist_ok=True)
    for f in tmp.glob("*.png"):
        f.unlink()
    for i in range(need):
        shutil.copyfile(images[min(i, len(images) - 1)], tmp / f"{i:02d}.png")
    run([ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-framerate", "1",
         "-pattern_type", "glob", "-i", str(tmp / "*.png"),
         "-vf", f"tile={cols}x{rows}", "-frames:v", "1", str(out)])
    return out


def stage_review(job: Path) -> None:
    """审片：关键帧行 + 视频抽帧行各拼一张图，一屏看完；并写 clips/manifest.json。

    放在 render 之后、assemble 之前 —— 废镜在这里发现，重出的是一镜（210s），
    而不是"等成片才发现第 3 镜废了"。
    """
    shots = load(job / "shots.json")["shots"]
    qc = job / "qc"
    qc.mkdir(parents=True, exist_ok=True)
    keyframes, frames, manifest = [], [], []
    for shot in shots:
        idx = shot["idx"]
        kf = job / "frames" / f"{idx:02d}.png"
        clip = job / "clips" / f"{idx:02d}.mp4"
        if kf.exists():
            keyframes.append(kf)
        if clip.exists():
            out = qc / f"v{idx:02d}.png"
            run([ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-ss", "2.5",
                 "-i", str(clip), "-frames:v", "1", str(out)])
            frames.append(out)
        info = probe(clip) if clip.exists() else {}
        manifest.append({
            "idx": idx, "id": shot.get("id"), "ref": shot.get("ref"),
            "prompt_en": shot.get("prompt_en"),
            "subtitle_cn": shot.get("subtitle_cn"),
            "seed": SEED + idx,
            "keyframe": kf.name if kf.exists() else None,
            "clip": clip.name if clip.exists() else None,
            "duration_s": round(info.get("duration", 0), 2),
            "bytes": clip.stat().st_size if clip.exists() else 0,
        })
    dump(job / "clips" / "manifest.json", manifest)
    kf_sheet = _sheet(keyframes, qc / "keyframes_sheet.png")
    vf_sheet = _sheet(frames, qc / "frames_sheet.png")
    # 文字审核单：TUI 里看不到图时，人/agent 靠这张表就能审
    story = load(job / "story.json")
    lines = [f"# {job.name} 审核单", "",
             f"生成：{time.strftime('%F %T')} · {story.get('title','')}"
             f"（{story.get('genre','')} / {len(shots)} 镜 / {story.get('duration_s','')}s）", "",
             "| # | 角色 | 字幕 | 关键帧 | 视频 | 时长 | seed | 结论 | 备注 |",
             "|---|---|---|---|---|---|---|---|---|"]
    for m in manifest:
        lines.append(f"| {m['idx']} | {m.get('ref') or '-'} | {m.get('subtitle_cn') or '-'} | "
                     f"{m.get('keyframe') or '-'} | {m.get('clip') or '缺'} | "
                     f"{m['duration_s']:.2f}s | {m['seed']} | ☐合格 ☐重出 | |")
    lines += [
        "",
        "## 怎么审（TUI 里看不到图也能审）",
        "",
        "1. **看图（终端）**：`python3 shortdrama/tview.py qc/keyframes_sheet.png`"
        "（Pillow → ANSI 彩色半块，直接在终端里显示）",
        "2. **看图（浏览器）**：`./sd assets serve <job-dir> --port 8092`"
        "（图直接渲染、视频内嵌播放；给人看用这个）",
        "3. **看视频**：视频无法 ANSI 化 → `review` 已抽帧成 qc/frames_sheet.png，同样用 1/2 看",
        "4. **只看文字**：本表的「结论」列 + `report.json`（自动质检）",
        "",
        "## 结论怎么写",
        "",
        "- **合格**：人物与定妆图一致、构图符合 beat、无穿帮畸变、字幕对得上。",
        "- **重出**：写清原因（人脸不一致 / 构图错 / 畸变 / 动作不对 / 黑帧）+ 换哪个 seed。",
        "- 重出一镜：删掉 `clips/NN.mp4` 再跑 `episode.py <job> render`（单镜粒度续跑）。",
    ]
    (qc / "review.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[review] 关键帧拼图 {kf_sheet}")
    print(f"[review] 视频抽帧拼图 {vf_sheet}")
    print(f"[review] 文字审核单 {qc/'review.md'}")
    print(f"[review] 清单 → {job/'clips'/'manifest.json'}")


def build_srt(shots: list[dict], durations: list[float]) -> str:
    """字幕时间轴按**实际**每镜时长排。

    去重规则：**只在相邻镜（≤2 镜，约 10 秒内）重复时去重**。
    - 相邻重复是噪音：观众 5 秒内看两遍同一句。
    - 隔得远的重复是**叙事回环**，必须留：E01 把"你该回来了。"放在 s11 和 s23，
      同一场戏演两遍（钩子预演 + 正片），第二次出现才有分量——全局去重会把这个
      设计抹掉。
    """
    lines, t, last = [], 0.0, {}
    for i, (shot, dur) in enumerate(zip(shots, durations)):
        text = (shot.get("subtitle_cn") or "").strip()
        if text and i - last.get(text, -99) > 2:
            last[text] = i
            lines += [f"{len(lines)//4+1}", f"{srt_time(t)} --> {srt_time(t+dur)}", text, ""]
        t += dur
    return "\n".join(lines)


def stage_assemble(job: Path) -> None:
    """拼接 → 3x 放大 → 烧字幕 + 统一响度 → episode.mp4。

    与方案 §9.5 有一处故意的差异：**先放大再烧字幕**，字幕在 1920×1056 上渲染，
    比先烧 640×352 再放大清晰（文字边缘不糊）。
    """
    shots = load(job / "shots.json")["shots"]
    story = load(job / "story.json")
    aspect = story.get("aspect", "landscape")
    clips = [job / "clips" / f"{s['idx']:02d}.mp4" for s in shots]
    missing = [c.name for c in clips if not c.exists()]
    if missing:
        raise SystemExit(f"缺镜头：{missing}")

    # 逐镜归一后再拼：H3 每镜原生音轨的响度差异很大（整片 LRA 高达 22-24 LU），
    # 逐镜对齐到 -20 LUFS 后，整片 LRA 降到 8.9 LU —— 这才是"集与集响度一致"的正解
    # （压整片只会把动态一起压扁；逐镜对齐是声音编辑的标准做法）。
    norm_dir = job / "clips" / "norm"
    norm_dir.mkdir(exist_ok=True)
    norm_clips = []
    for c in clips:
        n = norm_dir / c.name
        if not _fresh(n, [c]):
            run([ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-i", str(c),
                 "-af", "loudnorm=I=-20:TP=-3:LRA=11",
                 "-c:v", "copy", "-c:a", "aac", "-ar", "48000", "-b:a", "192k", str(n)])
        norm_clips.append(n)

    lst = job / "clips" / "concat.txt"
    lst.write_text("".join(f"file '{c.resolve()}'\n" for c in norm_clips), encoding="utf-8")
    concat = job / "clips" / "concat.mp4"
    if _fresh(concat, norm_clips):     # 中间件复用：重出 1 镜不必重拼全片
        print("[assemble] concat.mp4 已是最新，复用")
    else:
        run([ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-f", "concat",
             "-safe", "0", "-i", str(lst), "-c", "copy", str(concat)])

    durations = [probe(c)["duration"] for c in clips]
    (job / "subtitles.srt").write_text(build_srt(shots, durations), encoding="utf-8")

    up = job / "clips" / "upscaled.mp4"
    if _fresh(up, [concat]):           # 同上：放大只跑一次
        print("[assemble] upscaled.mp4 已是最新，复用")
    else:
        # 竖屏：H3 把 360 圆整成 352（16 的倍数），3× = 1056×1920，不是平台规格。
        # 直接缩到目标交付尺寸（1080×1920），2.3% 的横向拉伸看不出来。
        dw, dh = delivery_size(aspect)
        if aspect == "portrait":
            run([ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
                 "-i", str(concat), "-vf", f"scale={dw}:{dh}:flags=lanczos",
                 "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                 "-pix_fmt", "yuv420p", "-r", str(FPS), "-c:a", "aac", "-ar", "48000",
                 "-b:a", "192k", str(up)])
        else:
            run([sys.executable, str(H3 / "scripts" / "upscale_video.py"),
                 str(concat), str(up), "--scale", "3"])

    # 字幕样式：横屏贴画面底部；竖屏要避开平台 UI（底部约 250–400px 被占），
    # 字也要更大（竖屏手机上按画幅算）
    if aspect == "portrait":
        style = ("FontName=Noto Sans CJK SC,FontSize=54,PrimaryColour=&H00FFFFFF,"
                 "OutlineColour=&H00000000,BorderStyle=3,BackColour=&H90000000,"
                 "Outline=14,Shadow=0,MarginV=360,Alignment=2")
    else:
        style = ("FontName=Noto Sans CJK SC,FontSize=42,PrimaryColour=&H00FFFFFF,"
                 "OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=1,MarginV=56,Alignment=2")
    # 统一响度 + 限幅（H3 源音轨实测可能到 0 dBFS，削波必须在这里压住）
    # 统一响度：两遍 loudnorm（先测后用）+ 前后限幅。
    # 单遍 loudnorm 实测不命中目标（要 -16 得到 -18.1），且 alimiter 默认 level=true
    # 会把限掉的电平又推回去 —— 那是成片削波的直接成因（链末端 -0.5 dB → AAC 后 0.0 dBFS）。
    audio_chain = loudness.audio_chain_for(up)
    # 系列级亮度统一：3 集平均亮度差 25%（E01=100/E02=80/E03=85），连续追剧会跳。
    vf_parts = [f"subtitles=subtitles.srt:force_style='{style}'"]
    bright = grade.measure_brightness(up)
    corr = grade.brightness_filter(bright)
    if corr:
        vf_parts.append(corr)
        print(f"[assemble] 亮度校正 {bright:.1f} → {grade.TARGET_YAVG:.0f}（{corr}）")
    else:
        print(f"[assemble] 亮度 {bright:.1f} 已达标，不校正")
    run([ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-i", str(up),
         "-vf", ",".join(vf_parts),
         "-af", audio_chain,
         "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
         "-r", str(FPS), "-c:a", "aac", "-ar", "48000", "-b:a", "192k",
         "-movflags", "+faststart", str(job / "episode.mp4")], cwd=job)
    print(f"[assemble] {job/'episode.mp4'} {probe(job/'episode.mp4')}")


def stage_qc(job: Path) -> int:
    """自动质检：黑帧/静音/削波/时长/分辨率/帧率/缺镜 → report.json。"""
    shots = load(job / "shots.json")["shots"]
    aspect = load(job / "story.json").get("aspect", "landscape")
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
        if (info["w"], info["h"]) != frame_size(aspect):
            add("block", clip.name,
                f"分辨率 {info['w']}x{info['h']} != {frame_size(aspect)[0]}x{frame_size(aspect)[1]}")
        if not info["audio"]:
            add("warn", clip.name, "无音轨")
        # 一次解码同时查黑帧/静音/削波（原来跑 3 次，慢 3 倍）
        err = run([ffmpeg(), "-hide_banner", "-i", str(clip),
                   "-vf", "blackdetect=d=0.4:pix_th=0.10",
                   "-af", "silencedetect=n=-50dB:d=1.0,volumedetect", "-f", "null", "-"])
        if "black_start" in err:
            add("block", clip.name, "检出黑帧")
        if "silence_start" in err:
            add("warn", clip.name, "检出静音段")
        m = re.search(r"max_volume:\s*(-?\d+(?:\.\d+)?) dB", err)
        if m and float(m.group(1)) > -0.3:
            # 源镜头的削波由后期 limiter 兜住，所以镜头级只提示，成片级才阻断
            add("warn", clip.name, f"削波 max_volume={m.group(1)} dB（后期限幅兜底）")

    ep_path = job / "episode.mp4"
    ep = {"exists": ep_path.exists()}
    if ep_path.exists():
        info = probe(ep_path)
        ep.update(info)
        if (info["w"], info["h"]) != delivery_size(aspect):
            add("block", ep_path.name,
                f"交付分辨率 {info['w']}x{info['h']} != {delivery_size(aspect)[0]}x{delivery_size(aspect)[1]}")
        if abs(info["fps"] - FPS) > 0.5:
            add("block", ep_path.name, f"帧率 {info['fps']}")
        want = sum(s.get("duration_s") or 0 for s in shots)
        if abs(info["duration"] - want) > 0.5:
            add("warn", ep_path.name, f"成片时长 {info['duration']:.2f}s 与剧本 {want}s 偏差 > 0.5s")
        err = run([ffmpeg(), "-hide_banner", "-i", str(ep_path),
                   "-vf", "blackdetect=d=0.4:pix_th=0.10",
                   "-af", "volumedetect", "-f", "null", "-"])
        if "black_start" in err:
            add("block", ep_path.name, "成片检出黑帧")
        # 响度三项按交付标准判（比单看 max_volume 准：能看出响度偏离与动态范围过宽）
        measured = loudness.measure(ep_path)
        ep["audio_measured"] = measured
        for f in loudness.check(measured):
            add(f["level"], ep_path.name, f["message"])
    else:
        add("block", ep_path.name, "没有成片")

    # 字幕：观众唯一能读到的信息通道。分开算两件事——
    #   有效字幕 = 去重后的 subtitle_cn（上屏信息卡 + 对白字幕）
    #   台词     = dialogue_cn（口播 / 角色说的话）
    # 重复字幕分两类：**相邻重复**（≤2 镜）是噪音，成片已只烧一次；
    # **隔得远的重复**是叙事回环（E01 同一场戏演两遍），必须保留。
    subs = [(s.get("subtitle_cn") or "").strip() for s in shots]
    pos: dict[str, list[int]] = {}
    for i, t in enumerate(subs):
        if t:
            pos.setdefault(t, []).append(i)
    adjacent = {t: p for t, p in pos.items() if any(b - a <= 2 for a, b in zip(p, p[1:]))}
    echo = {t: p for t, p in pos.items() if t not in adjacent and len(p) > 1}
    eff = len(pos)
    dlg = sum(1 for s in shots if (s.get("dialogue_cn") or "").strip())
    ep["subtitle_coverage"] = f"{eff}/{len(shots)}"
    ep["subtitle_unique"] = eff
    ep["dialogue_count"] = dlg
    if echo:
        ep["subtitle_echo"] = {t: p for t, p in echo.items()}
    if adjacent:
        add("warn", "subtitles",
            f"{len(adjacent)} 条字幕在相邻镜重复（成片已只烧第一次）：{list(adjacent)[:3]}")
    if shots and eff / len(shots) < 0.15 and dlg / len(shots) < 0.15:
        add("warn", "subtitles",
            f"有效字幕 {eff}/{len(shots)}、台词 {dlg}/{len(shots)} 都偏低 —— "
            "观众几分钟里读不到足够信息，建议给无对白镜补旁白或信息卡")

    report = {"episode": job.name, "checked": time.strftime("%F %T"),
              "episode_probe": ep, "findings": findings,
              "blocking": [f for f in findings if f["level"] == "block"]}
    dump(job / "report.json", report)
    for f in findings:
        print(f"[qc] {f['level']:4s} {f['where']}: {f['message']}")
    print(f"[qc] {'阻断 ' + str(len(report['blocking'])) + ' 项' if report['blocking'] else '通过'}"
          f"（{job/'report.json'}）")
    return 1 if report["blocking"] else 0


STAGES = {
    "lint": stage_lint,          # 剧本预检（出图前，10 秒成本挡掉毁集级错误）
    "cast": stage_cast,          # 定妆候选图
    "pick": stage_pick,          # 挑图落地
    "shots": stage_shots,        # 分镜提示词
    "frames": stage_frames,      # 关键帧图（10s/镜，审片用；也能当 i2v 首帧）
    "render": stage_render,      # 出镜（ref2va/t2v，文本驱动构图）
    "render-i2v": lambda j: stage_render(j, first_frame=True),   # 出镜（关键帧首帧，构图可控）
    "review": stage_review,      # 审片拼图 + manifest
    "assemble": stage_assemble,  # 合成成片
    "qc": stage_qc,              # 质检
}


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    job = Path(argv[1]).expanduser().resolve()
    stages = argv[2:] or ["lint", "shots", "render", "review", "assemble", "qc"]
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
