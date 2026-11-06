#!/usr/bin/env python3
"""任务看板：把一集拆成 T0–T7 阶段卡片——能看状态、能一键触发。

为什么是看板而不是队列（文件即状态，D11/R19）：
* 每张卡的**状态 = 该阶段的产物在不在**（story.json / clips/*.mp4 / episode.mp4 …），
  看板自己不存任何状态，也就没有"看板和目录打架"这种 bug。
* **运行中** = 有个活着的 pid 文件（logs/stage-<key>.pid）。
* 触发 = 起一个子进程跑该阶段的命令，stdout/stderr 落到 logs/stage-<key>.log，
  网页读这个尾巴投影进度（没有心跳的阶段在页面上就等于"卡死"，R18）。

执行者两种（方案 §4.3 / D18）：
* mode=cli   → 直接跑本地命令（确定性动作：出图/出镜/拼接/质检）
* mode=agent → 起一个 pi 无头 session，把这张任务卡当提示词交给它（内容决策类阶段）
网页只做触发器，不自己实现任何 GPU/ffmpeg 逻辑。
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

PKG = Path(__file__).resolve().parent          # workspace/shortdrama/shortdrama
REPO = PKG.parent                             # workspace/shortdrama
EPISODE = PKG / "episode.py"
TOOLS = REPO / "tools"
PY = sys.executable


# ---------------------------------------------------------------- 阶段定义

def _ep(job: Path) -> str:
    """从目录名 D202611_001_E01 取集号 E01（竖屏目录 D202611_001_E01_P → E01）。"""
    name = job.name
    ep = name.split("_", 2)[-1] if "_" in name else name
    return ep[:-2] if ep.endswith("_P") else ep
    name = job.name
    return name.split("_", 2)[-1] if "_" in name else name


def _story(job: Path) -> dict:
    p = job / "story.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return {}


def _shots(job: Path) -> list[dict]:
    p = job / "shots.json"
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("shots", [])
    except ValueError:
        return []


def _subtitle_cov(job: Path) -> tuple[int, int]:
    """（有字幕的镜数, 总镜数）。"""
    sc = _story(job).get("scenes", [])
    if not sc:
        return 0, 0
    return sum(1 for s in sc if (s.get("subtitle_cn") or "").strip()), len(sc)


def _clips(job: Path) -> int:
    d = job / "clips"
    if not d.is_dir():
        return 0
    return sum(1 for p in d.glob("*.mp4") if p.stem.isdigit())


def _frames(job: Path) -> int:
    d = job / "frames"
    if not d.is_dir():
        return 0
    return sum(1 for p in d.glob("*.png"))


def _stage_cmd(stage: str, job: Path) -> list[str]:
    """阶段的本地执行命令。"""
    root = job.parent
    if stage == "dialogue":
        return [PY, str(TOOLS / "add_dialogue.py"), "--root", str(root), "--episode", _ep(job)]
    if stage == "evidence":
        return [PY, str(PKG / "evidence.py"), str(job)]
    if stage == "approval":
        return [PY, str(PKG / "approval.py"), str(job), "--show"]
    return [PY, str(EPISODE), str(job), stage]


# key, 标题, 说明, 状态判据(done, detail), 是否可直接触发
STAGES: list[dict] = [
    {"key": "lint", "title": "T1 剧本预检", "desc": "story.json 预检：毁集级错误在秒级挡下",
     "status": lambda j: (_story(j) != {}, "story.json 已就位" if _story(j) else "还没有剧本")},
    {"key": "story", "title": "T0 写剧本（LLM）", "desc": "整剧一次：题材 → 大纲 → 逐集 story.json",
     "manual": True,
     "status": lambda j: (_story(j) != {}, "已有 story.json" if _story(j) else "需要题材一句话")},
    {"key": "dialogue", "title": "T2 台词 / 字幕", "desc": "代码选镜、LLM 只写字；只改文本 → 不用重渲",
     "status": lambda j: (_subtitle_cov(j)[0] >= 0.4 * max(1, _subtitle_cov(j)[1]),
                          "字幕 %d/%d" % _subtitle_cov(j))},
    {"key": "cast", "title": "T3 定妆候选图", "desc": "Z-Image 中文出图（~10s/张），全剧共用",
     "status": lambda j: (_cands(j) > 0, "候选 %d 张" % _cands(j))},
    {"key": "pick", "title": "T3 挑图落地", "desc": "审片台挑主图/备图 → cast/<id>.png",
     "status": lambda j: (_picked(j), "已选 %d/%d" % (_picked_n(j), len(_story(j).get("characters", [])) or 1))},
    {"key": "shots", "title": "T4 分镜（提示词）", "desc": "story.json → shots.json（纯文本，秒级）",
     "status": lambda j: (bool(_shots(j)), "%d 镜" % len(_shots(j)))},
    {"key": "frames", "title": "T4 关键帧", "desc": "每镜 1 张图（10s/镜）→ 先审图再烧 GPU",
     "status": lambda j: (_frames(j) >= len(_shots(j)) and bool(_shots(j)),
                          "关键帧 %d/%d" % (_frames(j), len(_shots(j))))},
    {"key": "render", "title": "T5 分段镜头", "desc": "H3 ref2va/t2v 逐镜出片（170–210s/镜）",
     "status": lambda j: (_clips(j) >= len(_shots(j)) and bool(_shots(j)),
                          "镜头 %d/%d" % (_clips(j), len(_shots(j))))},
    {"key": "review", "title": "T5 审片", "desc": "关键帧拼图 + 成片抽帧拼图；审片台逐镜判定",
     "status": lambda j: _review_status(j)},
    {"key": "evidence", "title": "T5 证据表（无图可打印）", "desc": "每镜的亮度/静止/关键帧PSNR/与上镜相似度 → qc/evidence.md（先看哪几镜）",
     "status": lambda j: ((j / "qc" / "evidence.md").exists(),
                          "已生成" if (j / "qc" / "evidence.md").exists() else "还没跑")},
    {"key": "assemble", "title": "T6 整集", "desc": "拼接 → 放大 → 烧字幕 + BGM + 响度/亮度",
     "status": lambda j: ((j / "episode.mp4").exists(),
                          "%d MB" % ((j / "episode.mp4").stat().st_size // 1048576) if (j / "episode.mp4").exists() else "还没成片")},
    {"key": "approval", "title": "T8 交付审批", "desc": "客观门禁 + 人工签核 + 留痕（approval.json）",
     "status": lambda j: _approval_status(j)},
    {"key": "qc", "title": "T7 质检", "desc": "黑帧/静音/削波/缺镜/响度/字幕覆盖率 → report.json",
     "status": lambda j: _qc_status(j)},
]


def _cands(job: Path) -> int:
    d = job / "cast"
    if not d.is_dir():
        return 0
    return sum(1 for p in d.glob("*_cand*.png"))


def _picked_n(job: Path) -> int:
    d = job / "cast"
    if not d.is_dir():
        return 0
    return sum(1 for p in d.glob("*.png") if "_cand" not in p.name and not p.name.endswith("_alt.png"))


def _picked(job: Path) -> bool:
    chars = _story(job).get("characters", [])
    if not chars:
        return False
    d = job / "cast"
    if not d.is_dir():
        return False
    return all((d / f"{c['id']}.png").exists() for c in chars)


def _review_status(job: Path) -> tuple[bool, str]:
    p = job / "review.json"
    if not p.exists():
        return False, "还没审"
    try:
        rv = json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return False, "review.json 读不出来"
    total = len(_shots(job))
    done = sum(1 for v in rv.values() if isinstance(v, dict) and v.get("verdict"))
    redo = sum(1 for v in rv.values() if isinstance(v, dict) and v.get("verdict") == "redo")
    return (total > 0 and done >= total and redo == 0, "已审 %d/%d · 待重出 %d" % (done, total, redo))


def _approval_status(job: Path) -> tuple[bool, str]:
    """交付审批状态（approval.json）：只有 approved 才算"批过了"。"""
    p = job / "approval.json"
    if not p.exists():
        return False, "未提交"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return False, "approval.json 读不出来"
    state = d.get("state", "?")
    label = {"approved": "已批准", "submitted": "已提交待批",
             "changes_requested": "已驳回待改", "pending": "待提交"}.get(state, state)
    return state == "approved", label


def _qc_status(job: Path) -> tuple[bool, str]:
    p = job / "report.json"
    if not p.exists():
        return False, "还没质检"
    try:
        rep = json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return False, "report.json 读不出来"
    block = len(rep.get("blocking", []))
    cov = rep.get("episode_probe", {}).get("subtitle_coverage", "?")
    return block == 0, "阻断 %d · 字幕 %s" % (block, cov)


# ---------------------------------------------------------------- 运行

def _pid_file(job: Path, key: str) -> Path:
    return job / "logs" / f"stage-{key}.pid"


def _log_file(job: Path, key: str) -> Path:
    return job / "logs" / f"stage-{key}.log"


def running(job: Path, key: str) -> dict | None:
    """这个阶段现在是不是在跑（pid 活着就算在跑）。"""
    pf = _pid_file(job, key)
    if not pf.exists():
        return None
    try:
        info = json.loads(pf.read_text(encoding="utf-8"))
    except ValueError:
        return None
    pid = info.get("pid")
    if not pid:
        return None
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError):
        return None
    return info


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def agent_prompt(stage: dict, job: Path) -> str:
    """交给 pi-agent 内核的任务卡。"""
    cmd = " ".join(shlex.quote(c) for c in _stage_cmd(stage["key"], job))
    return (f"短剧阶段任务：{stage['title']}（{stage['desc']}）。\n"
            f"任务目录：{job}\n"
            f"请在 {job} 目录下执行：{cmd}\n"
            f"完成后用一两句话说明结果（产物文件、关键数字、有没有阻断）。"
            f"不要改动与该阶段无关的文件。")


def start(job: Path, key: str, mode: str = "cli") -> dict:
    """触发一个阶段。返回 {"ok":…, "pid":…, "cmd":…}。"""
    stage = next((s for s in STAGES if s["key"] == key), None)
    if stage is None:
        return {"ok": False, "error": f"没有这个阶段：{key}"}
    if stage.get("manual"):
        return {"ok": False, "error": "这个阶段需要先给参数（题材/大纲），看板不代跑"}
    job = Path(job).resolve()
    if not job.is_dir():
        return {"ok": False, "error": "任务目录不存在"}
    cur = running(job, key)
    if cur:
        return {"ok": False, "error": f"已在运行（pid {cur['pid']}）"}

    (job / "logs").mkdir(exist_ok=True)
    if mode == "agent":
        cmd = ["pi", "--print", "--mode", "json", agent_prompt(stage, job)]
        cwd = job
    else:
        cmd = _stage_cmd(key, job)
        cwd = job
    log = _log_file(job, key)
    with open(log, "ab") as fh:
        fh.write(f"\n===== {time.strftime('%H:%M:%S')} {mode}: {' '.join(cmd)}\n".encode())
        fh.flush()
        proc = subprocess.Popen(cmd, cwd=cwd, stdout=fh, stderr=subprocess.STDOUT,
                                start_new_session=True)
    info = {"pid": proc.pid, "stage": key, "mode": mode, "cmd": cmd,
            "started": time.time(), "log": str(log)}
    _pid_file(job, key).write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, **info}


def stop(job: Path, key: str) -> dict:
    """停掉在跑的阶段（只杀自己起的那个 pid）。"""
    cur = running(job, key)
    if not cur:
        return {"ok": False, "error": "没在跑"}
    pid = int(cur["pid"])
    try:
        os.killpg(os.getpgid(pid), 15)     # 整个进程组（渲染会起子进程）
    except OSError:
        try:
            os.kill(pid, 15)
        except OSError:
            return {"ok": False, "error": "杀不掉"}
    return {"ok": True, "pid": pid}


def board(job: Path) -> list[dict]:
    """一集的阶段卡片（状态从目录推断）。"""
    job = Path(job)
    out = []
    for s in STAGES:
        done, detail = s["status"](job)
        run = running(job, s["key"])
        out.append({
            "key": s["key"], "title": s["title"], "desc": s["desc"],
            "done": bool(done), "detail": detail,
            "manual": bool(s.get("manual")),
            "running": bool(run),
            "pid": run["pid"] if run else None,
            "cmd": " ".join(shlex.quote(c) for c in _stage_cmd(s["key"], job))
                      if not s.get("manual") else "",
            "log_tail": _tail(_log_file(job, s["key"])),
        })
    return out


def _tail(path: Path, n: int = 3) -> list[str]:
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").strip().splitlines()
    except OSError:
        return []
    return lines[-n:]


def render_text(job: Path) -> str:
    """终端版看板（agent 在 TUI 里也能看）。"""
    job = Path(job)
    lines = [f"[board] {job.name}  ({job})"]
    for c in board(job):
        mark = "✔" if c["done"] else ("▶" if c["running"] else "·")
        state = "运行中" if c["running"] else ("完成" if c["done"] else "待做")
        extra = f"  [pid {c['pid']}]" if c["running"] else ""
        lines.append(f"  {mark} {c['title']:<16} {state:<4} {c['detail']}{extra}")
        if c["log_tail"]:
            lines.append(f"      └ {c['log_tail'][-1][:110]}")
    return "\n".join(lines)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="任务看板：看阶段状态 / 触发阶段")
    ap.add_argument("job", help="集目录（如 /home/a/ai-work/duanju/D202611_001_E01）")
    ap.add_argument("--run", help="触发某个阶段（lint/cast/pick/shots/frames/render/assemble/qc/dialogue）")
    ap.add_argument("--mode", default="cli", choices=["cli", "agent"], help="执行者：本地命令 / pi-agent 内核")
    ap.add_argument("--stop", help="停掉在跑的阶段")
    a = ap.parse_args()
    job = Path(a.job).expanduser().resolve()
    if a.stop:
        print(stop(job, a.stop))
    elif a.run:
        print(json.dumps(start(job, a.run, a.mode), ensure_ascii=False, indent=2))
    else:
        print(render_text(job))
