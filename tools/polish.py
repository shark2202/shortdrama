#!/usr/bin/env python3
"""用本地 LLM 打磨剧本：题材 → 分集大纲 → 逐集 story.json。

    python3 tools/polish.py --brief "都市温情悬疑，深夜便利店，3集×3分钟" \
        --episodes 3 --minutes 3 --out ../duanju

流程（每一步都可单独重跑，结果落盘可人工改）：
  1. 大纲：题材 → 3 集分集大纲（每集钩子/转折/尾钩）
  2. 逐集：按大纲写 story.json（35 镜/3 分钟，中英双轨）
  3. 校验：schema + 镜数×时长 对齐 + 人物造型检查（禁"大众脸"）

只用标准库 + 本地 LLM 的 OpenAI 兼容端点（默认 http://127.0.0.1:8080/v1）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from shortdrama import facecheck, textcheck  # noqa: E402
DEFAULT_EPISODES = 3
DEFAULT_MINUTES = 3
SHOT_SECONDS = 5.17          # H3 固定 124 帧 @24fps
CANDIDATES = 3
SHOTS_PER_CHUNK = 12      # 每次写 12 镜：再大 JSON 会被 max_tokens 截断


# ---------------------------------------------------------------- LLM 调用

def chat(base: str, model: str, system: str, user: str, *, max_tokens: int = 4096,
         temperature: float = 0.8, retries: int = 2) -> str:
    """本机 llama-server 开了 reasoning：reasoning_content 与 content 共用 max_tokens，
    所以预算要给足，否则长剧本会被思考过程吃光、content 返回空字符串。"""
    payload = {"model": model, "messages": [{"role": "system", "content": system},
                                            {"role": "user", "content": user}],
               "max_tokens": max_tokens, "temperature": temperature}
    req = urllib.request.Request(base + "/chat/completions",
                                 data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=1800) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            msg = data["choices"][0]["message"]
            content = (msg.get("content") or "").strip()
            if not content:
                # reasoning 把 max_tokens 吃光了：报出来，别让人对着空字符串猜
                reason = len(msg.get("reasoning_content") or "")
                raise RuntimeError(
                    f"模型只返回了 reasoning（{reason} tokens），content 为空——"
                    f"max_tokens={max_tokens} 不够，调大后重试")
            return content
        except Exception as e:
            if attempt == retries:
                raise
            print(f"  [llm] 第 {attempt+1} 次失败（{e}），重试", file=sys.stderr)
            time.sleep(3)
    return ""


def extract_json(text: str) -> dict:
    """模型被要求只输出 JSON；容错 fenced block / 前后废话。"""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)
    try:
        return json.loads(text)
    except ValueError:
        pass
    for opener, closer in (("{", "}"), ("[", "]")):
        i = text.find(opener)
        j = text.rfind(closer)
        if i != -1 and j > i:
            try:
                return json.loads(text[i:j + 1])
            except ValueError:
                continue
    raise ValueError("模型没吐出可解析的 JSON：" + text[:300])


# ---------------------------------------------------------------- 提示词

# 爆款短剧的通行结构（行业通行做法，用于约束模型，不是凭空设定）：
#   前 3 秒钩子 → 每 15–20 秒一个信息增量 → 每集尾钩（断点）→ 每集一个小反转
SYS_OUTLINE = """你是竖屏短剧总编剧，专精都市情感/悬疑赛道。你的任务是把一个题材扩成多集短剧的分集大纲。

短剧的爆款结构（必须遵守）：
1. **前 3 秒钩子**：第一集第一个镜头就给"认知缺口"——观众脑中自动冒出"为什么？"。禁止用"某天深夜，某人走进一家店"这种平铺开场。
2. **每 15–20 秒一个信息增量**：观众必须持续拿到新信息，不能连续两个镜头都在"看风景"。
3. **每集一个反转**：每集内部要有一次预期被打破（不是大反转，是让人"哦？"的那种）。
4. **尾钩（断点）**：每集最后 5 秒必须卡在最想知道"然后呢"的地方，且不能给答案。
5. **情感落点**：悬疑要有情感底——观众要关心人，不只是关心谜题。
6. **单集独立成篇**：每集要能让没看过上一集的人看懂；但结尾要让人想看下一集。

输出要求：**只输出一个 JSON 对象**，不要 Markdown 代码块，不要任何解释文字。格式：
{
  "series_title": "剧名",
  "logline": "一句话故事线",
  "genre": "题材",
  "style_cn": "中文风格描述（画面质感，10-20字）",
  "style_en": "English style for video model, no quotes, no commas",
  "characters": [
    {"id": "c1", "name": "姓名", "desc_cn": "外貌+造型+气质的具体描述，40-80字",
     "voice": "声音特征"}
  ],
  "episodes": [
    {"index": 1, "title": "本集标题", "hook": "前3秒钩子具体是什么",
     "beats": ["beat1", "beat2", ...], "twist": "本集反转是什么",
     "cliffhanger": "尾钩：卡在什么画面/信息上"}
  ]
}

要求：beats 写成能拍的动作或画面（不是抽象剧情描述），每集 6-10 个 beat。"""

SYS_EPISODE = """你是竖屏短剧编剧，同时是 MiniMax H3 视频模型的提示词工程师。你写一集的逐镜剧本。

硬约束：
1. **中英双轨**：`beat`/`subtitle_cn`/`sfx`/`desc_cn` 用中文；`prompt_en`/`sfx_en`/`style_en` 用英文。
   **中文绝不能进 prompt_en**——H3 的文本编码器只认英文，中文进去效果极差。
2. **镜数与时长对齐**：H3 每镜固定 124 帧 @24fps = 5.17 秒。镜数 × 5.17 ≈ 目标时长。
3. **prompt_en 三件套**：主体 → 动作/运镜 → 环境与光线。一行英文，不加引号。
   动作要"能被看见"：伸手、转身、抬头、推开、放下。不要写情绪形容词（模型看不见"悲伤"）。
4. **环境音进 sfx_en**：H3 靠 `natural ambient audio:` 那一段出声。
5. **画面里不能有需要观众读懂的文字**：H3 不认字形，画出来的字一定是乱码。
   照片上的日期、监控时间戳、手写字、招牌、海报——凡是"要读"的都会废。
   正确写法：保留"这里有行字"的质感，但明确不可辨认
   （`a faded date stamp, too small and blurred to read`、`blurred ink handwriting, illegible`）；
   观众要读到的内容写进 `subtitle_cn`（后期烧字幕，一定正确）。
6. **每镜一个主角**：`primary` 指这一镜用谁的定妆图当参考。双人同框时主角给参考图，另一人只写文字。
7. **人物造型必须有辨识度**：不要"漂亮/网红/大众脸"。要具体到发型细节、有无痣/疤/眼镜/胡茬、
   脸型、年龄感、穿衣的具体款式和颜色、随身物件。见下方"人物造型规则"。

**人物造型规则（避免大众脸，重要）**：
- 不要对称的、标准化的"AI 美女脸"。要指定：脸型（方/长/圆/心形/菱形）、不对称特征
  （单侧眉 higher、痣的位置、疤、龅牙、下颌角明显、颧骨突出、发际线）。
- 年龄要写进细节（法令纹、鱼尾纹、手部粗糙、颈纹），不要只写数字。
- 穿着写具体款式和颜色，不要"穿着朴素"。
- 职业痕迹要写进去（便利店店员 → 便利店工牌/荧光工服/记账本/挂绳）。

输出要求：**只输出一个 JSON 对象**，不要 Markdown 代码块，不要解释。格式：
{
  "title": "本集标题",
  "episode": "剧集编号",
  "genre": "题材",
  "style_cn": "中文风格",
  "style_en": "English style, no quotes",
  "cast_prompt_cn": "定妆照的固定后缀（中性光、纯灰背景、半身正面，不要混入风格词）",
  "characters": [{"id":"c1","name":"","desc_cn":"","ref_image":"cast/c1.png","voice":""}],
  "scenes": [
    {"id":"s1","idx":1,"chars":["c1"],"primary":"c1",
     "beat":"中文：这个镜头里发生什么（动作+运镜+光线，1-2句）",
     "duration_s":5.17,"shot_type":"wide",
     "prompt_en":"English prompt for H3, one line",
     "sfx":"中文环境音","sfx_en":"English ambient audio",
     "dialogue_cn":"这一镜的台词或画外音（可空）","subtitle_cn":"上屏字幕"}
  ],
  "ending": "本集收束"
}"""


# ---------------------------------------------------------------- 校验

# 人物造型检查统一在 shortdrama/facecheck.py（与 cast 阶段用的是同一套规则）


def check_character(c: dict) -> list[str]:
    return [f"{c.get('id')}：{p}" for p in facecheck.check(c.get("desc_cn", ""))]


def validate_story(story: dict, target_seconds: float) -> list[str]:
    problems = []
    scenes = story.get("scenes") or []
    if not scenes:
        return ["scenes 为空"]
    total = sum(s.get("duration_s", 0) for s in scenes)
    if abs(total - target_seconds) > target_seconds * 0.1:
        problems.append(f"总时长 {total:.1f}s 与目标 {target_seconds:.0f}s 偏差 >10%")
    for s in scenes:
        if not s.get("prompt_en"):
            problems.append(f"{s.get('idx')} 镜缺 prompt_en")
        elif re.search(r"[\u4e00-\u9fff]", s["prompt_en"]):
            problems.append(f"{s.get('idx')} 镜 prompt_en 里混了中文")
        if not s.get("primary"):
            problems.append(f"{s.get('idx')} 镜缺 primary（拿谁的定妆图当参考）")
    ids = {c["id"] for c in story.get("characters", [])}
    for s in scenes:
        if s.get("primary") and s["primary"] not in ids:
            problems.append(f"{s.get('idx')} 镜 primary={s['primary']} 不在 characters 里")
    for c in story.get("characters", []):
        problems += check_character(c)
    problems += textcheck.check_story(story)
    return problems


# ---------------------------------------------------------------- 主流程

def build_outline(base: str, model: str, brief: str, episodes: int, minutes: int) -> dict:
    print(f"[1/3] 生成 {episodes} 集分集大纲 …", flush=True)
    user = (f"题材：{brief}\n"
            f"要求：{episodes} 集，每集约 {minutes} 分钟（{int(minutes*60)} 秒）。\n"
            f"人物：2-3 个，每个都要有高度可辨识的外形设定（不要大众脸）。\n"
            f"注意：这是\"都市温情悬疑\"——温情是底色（人物之间的微光），悬疑是钩子。\n"
            f"请只输出 JSON。")
    t0 = time.time()
    raw = chat(base, model, SYS_OUTLINE, user, max_tokens=6000, temperature=0.85)
    data = extract_json(raw)
    print(f"      完成 {time.time()-t0:.0f}s：{data.get('series_title')} / "
          f"{len(data.get('episodes', []))} 集", flush=True)
    return data


def build_episode(base: str, model: str, outline: dict, ep: dict, minutes: int) -> dict:
    """写一集。分块生成：一次写 SHOTS_PER_CHUNK 镜，避免长 JSON 被 max_tokens 截断
    （reasoning 还要吃掉一截预算，35 镜一次写完会截断）。"""
    n_shots = max(1, round(minutes * 60 / SHOT_SECONDS))
    print(f"[2/3] 写第 {ep['index']} 集（{n_shots} 镜 / {minutes} 分钟）…", flush=True)

    head = (f"系列：{outline.get('series_title')}\n"
            f"故事线：{outline.get('logline')}\n"
            f"风格：{outline.get('style_cn')} / {outline.get('style_en')}\n\n"
            f"人物（造型必须照抄，保持全剧一致）：\n"
            + "\n".join(f"  {c['id']} {c['name']}：{c['desc_cn']}"
                        for c in outline.get("characters", []))
            + f"\n\n本集（{ep['index']}/{len(outline.get('episodes', []))}）：{ep.get('title')}\n"
            f"钩子（前3秒）：{ep.get('hook')}\n"
            f"本集反转：{ep.get('twist')}\n"
            f"尾钩（最后5秒卡住，不给答案）：{ep.get('cliffhanger')}\n")

    scenes: list[dict] = []
    chunk_idx = 0
    for start in range(0, n_shots, SHOTS_PER_CHUNK):
        chunk_idx += 1
        count = min(SHOTS_PER_CHUNK, n_shots - start)
        beats = ep.get("beats", [])
        # 把 beat 按比例切给每一块，保持叙事顺序
        lo = round(start / n_shots * len(beats))
        hi = round((start + count) / n_shots * len(beats))
        part = beats[lo:hi] or beats[-1:]
        plan = "\n".join(f"  {i+1}. {b}" for i, b in enumerate(part))
        first = start == 0
        last = start + count >= n_shots
        user = (head
                + f"beat 列表（本块要覆盖的内容，按顺序）：\n{plan}\n\n"
                + f"请写 **{count} 镜**（idx 从 {start+1} 到 {start+count}）。"
                + ("这是第一块，第 1 镜必须就是钩子（前3秒给认知缺口）。" if first else
                   "这是中段，承接上一块的结尾，镜头要连贯。")
                + ("这是最后一块，最后 1 镜必须是尾钩（卡住，不给答案）。" if last else "")
                + "\n每镜字段：id/idx/chars/primary/beat/duration_s(5.17)/shot_type/"
                  "prompt_en/sfx/sfx_en/dialogue_cn/subtitle_cn。"
                  "\nprompt_en 严格三件套：主体 → 动作与运镜 → 环境与光线，一行英文。"
                  "\ncast_prompt_cn 固定写：人物定妆照，胸部以上，正面，中性柔光，纯灰背景，高细节"
                  "（**不要**加风格词，风格词会污染定妆图的光线）"
                  "\n只输出 JSON，不要 Markdown 代码块。")
        t0 = time.time()
        raw = chat(base, model, SYS_EPISODE, user, max_tokens=9000, temperature=0.8)
        data = extract_json(raw)
        got = data.get("scenes") or []
        if not got:
            raise RuntimeError(f"第 {ep['index']} 集第 {chunk_idx} 块没吐出 scenes")
        for s in got:
            s["idx"] = len(scenes) + 1
            s["id"] = f"s{s['idx']}"
            s["duration_s"] = SHOT_SECONDS
            scenes.append(s)
        print(f"      块 {chunk_idx}：{len(got)} 镜（{time.time()-t0:.0f}s），"
              f"累计 {len(scenes)}/{n_shots}", flush=True)

    story = {
        "title": ep.get("title") or outline.get("series_title", ""),
        "episode": f"E{ep['index']:02d}",
        "series": outline.get("series_title", ""),
        "genre": outline.get("genre", ""),
        "style_cn": outline.get("style_cn", ""),
        "style_en": outline.get("style_en", ""),
        "cast_prompt_cn": "人物定妆照，胸部以上，正面，中性柔光，纯灰背景，高细节",
        "characters": [dict(c, ref_image=f"cast/{c['id']}.png")
                       for c in outline.get("characters", [])],
        "scenes": scenes,
        "ending": ep.get("cliffhanger", ""),
    }
    return story


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="本地 LLM 打磨短剧剧本")
    ap.add_argument("--brief", required=True, help="题材一句话")
    ap.add_argument("--episodes", type=int, default=DEFAULT_EPISODES)
    ap.add_argument("--minutes", type=int, default=DEFAULT_MINUTES)
    ap.add_argument("--base", default="http://127.0.0.1:8080/v1")
    ap.add_argument("--model", default="qwen3.8-local")
    ap.add_argument("--out", default=str(HERE.parent.parent / "duanju"))
    ap.add_argument("--series-id", default="D202611_001")
    ap.add_argument("--only-outline", action="store_true", help="只出大纲，不写分集")
    ap.add_argument("--outline", help="复用已有大纲 JSON，不再问 LLM")
    args = ap.parse_args(argv)

    out_root = Path(args.out).expanduser().resolve()
    out_root.mkdir(parents=True, exist_ok=True)

    if args.outline:
        outline = json.loads(Path(args.outline).read_text(encoding="utf-8"))
    else:
        outline = build_outline(args.base, args.model, args.brief, args.episodes, args.minutes)
    (out_root / "series_outline.json").write_text(
        json.dumps(outline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"      大纲 → {out_root/'series_outline.json'}")
    if args.only_outline:
        return 0

    target = args.minutes * 60
    all_problems = []
    for ep in outline.get("episodes", []):
        ep_dir = out_root / f"{args.series_id}_E{ep['index']:02d}"
        ep_dir.mkdir(parents=True, exist_ok=True)
        story = build_episode(args.base, args.model, outline, ep, args.minutes)
        problems = validate_story(story, target)
        (ep_dir / "story.json").write_text(
            json.dumps(story, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"      剧本 → {ep_dir/'story.json'}")
        for p in problems:
            print(f"      ⚠ {ep['index']} 集：{p}")
        all_problems += [f"E{ep['index']}: {p}" for p in problems]

    print(f"[3/3] 校验完成：{len(all_problems)} 个问题")
    return 1 if all_problems else 0


if __name__ == "__main__":
    sys.exit(main())
