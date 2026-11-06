#!/usr/bin/env python3
"""本地审批层：把"这一集批了没有"变成有状态、有依据、不可抵赖的一份记录。

为什么需要（现存的三个洞）：
1. `qc` 只回答"技术上过没过"（report.json），**没人签核**；
2. `qc/review.md` 是逐镜判定（通过/重出），但没有**整体交付的决议**——哪一集可以发，没人负责；
3. 交付件"文件齐全"≠"可发布"（审片单可能是空模板、质检报告可能是中途态），
   而 `gate.sh` 只查文件在不在。

所以审批 = **客观门禁 + 人工签核 + 留痕**：
* 客观门禁在**批准那一刻重跑**（不信任提交时的快照）——批准即代表"我确认它现在是好的"；
* 人工签核带 `by / at / note / items`（驳回必须写明改哪几镜、为什么）；
* 每次决议 append 进 `logs/approval.log`（只增不改的流水）+ `approval.json`（当前状态）。

状态机（文件即状态，与看板/审片台同一套约定）：
    pending ──submit──▶ submitted ──approve──▶ approved
                          │  ▲
                    reject│  │submit（改完再交）
                          ▼  │
                  changes_requested
    approved ──任何改动（新 clip / 新成片）──▶ 自动回落到 pending（见 `stale()`）

用法：
    python3 shortdrama/approval.py <job> --show
    python3 shortdrama/approval.py <job> --submit --by agent
    python3 shortdrama/approval.py <job> --approve --by director --note "过"
    python3 shortdrama/approval.py <job> --reject  --by director --note "s23 咬钥匙" --items 23
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

FILE = "approval.json"
LOG = "logs/approval.log"

STATES = ("pending", "submitted", "changes_requested", "approved")


# ---------------------------------------------------------------- 状态读写

def load(job: Path) -> dict:
    p = Path(job) / FILE
    if not p.exists():
        return {"job": Path(job).name, "state": "pending", "rounds": [], "updated_at": None}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return {"job": Path(job).name, "state": "pending", "rounds": [], "updated_at": None}


def save(job: Path, data: dict) -> None:
    job = Path(job)
    (job / "logs").mkdir(exist_ok=True)
    (job / FILE).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _log(job: Path, line: str) -> None:
    job = Path(job)
    (job / "logs").mkdir(exist_ok=True)
    with (job / "logs" / "approval.log").open("a", encoding="utf-8") as fh:
        fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {line}\n")


# ---------------------------------------------------------------- 客观门禁

def objective_checks(job: Path) -> list[dict]:
    """客观检查（批准时重跑，不信快照）。每一项都能自己复算。"""
    job = Path(job)
    out: list[dict] = []

    rp = job / "report.json"
    rep = json.loads(rp.read_text(encoding="utf-8")) if rp.exists() else {}
    block = rep.get("blocking", [])
    out.append({"item": "质检阻断", "ok": not block and rp.exists(),
                "detail": f"{len(block)} 项" + ("（缺 report.json）" if not rp.exists() else "")})

    rv = job / "qc" / "review.md"
    if rv.exists():
        import re
        rows = [l for l in rv.read_text(encoding="utf-8").splitlines() if re.match(r"^\| \d+ \|", l)]
        vidx = None
        for l in rv.read_text(encoding="utf-8").splitlines():
            if l.startswith("|") and "结论" in l:
                cols = [c.strip() for c in l.split("|")]
                vidx = cols.index("结论") if "结论" in cols else None
                break
        bad = unfilled = 0
        for r in rows:
            cols = r.split("|")
            v = cols[vidx].strip() if (vidx is not None and vidx < len(cols)) else ""
            if re.search(r"重出|修词|返工", v) and "☐" not in v:
                bad += 1
            elif "☐" in v and not re.search(r"[✅☑√✓]", v):
                unfilled += 1
            elif not re.search(r"合格|通过|OK|[✅☑√✓]", v):
                unfilled += 1
        out.append({"item": "人工逐镜审片",
                    "ok": bool(rows) and bad == 0 and unfilled == 0,
                    "detail": f"{len(rows)} 镜：未通过 {bad}，未填 {unfilled}"})
    else:
        out.append({"item": "人工逐镜审片", "ok": False, "detail": "缺 qc/review.md"})

    ev = job / "qc" / "evidence.md"
    out.append({"item": "证据表", "ok": ev.exists(), "detail": "已生成" if ev.exists() else "缺 qc/evidence.md"})

    need = ["episode.mp4", "release_meta.json", "subtitles.srt", "subtitles.ass", "clips/manifest.json"]
    miss = [f for f in need if not (job / f).exists()]
    out.append({"item": "交付件", "ok": not miss, "detail": "缺 " + ", ".join(miss) if miss else "齐全"})

    cov = rep.get("episode_probe", {}).get("subtitle_coverage", "?")
    try:
        a, b = (int(x) for x in str(cov).split("/"))
        cov_ok = b > 0 and a / b >= 0.4
    except Exception:
        cov_ok = False
    out.append({"item": "字幕覆盖", "ok": cov_ok, "detail": f"{cov}（qc 口径）"})
    return out


# ---------------------------------------------------------------- 状态流转

def stale(job: Path, data: dict) -> dict:
    """成片/clip 在批准之后被改过 → 批准自动失效（退回 pending）。

    没有这一步，"批准"就会变成一句空话：批完之后重压成片，仍然显示"已批准"。
    """
    job = Path(job)
    if data.get("state") != "approved":
        return data
    ep = job / "episode.mp4"
    approved_at = data.get("updated_at")
    if not ep.exists() or not approved_at:
        return data
    ts = time.mktime(time.strptime(approved_at, "%Y-%m-%d %H:%M:%S"))
    ep_new = ep.stat().st_mtime > ts + 1
    newest = max((p.stat().st_mtime for p in (job / "clips").glob("*.mp4")), default=0)
    clip_new = newest > ts + 1
    if ep_new or clip_new:
        data["state"] = "pending"
        data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        data["rounds"].append({"state": "pending", "by": "system", "at": data["updated_at"],
                                "note": "成片/clip 在批准后被改动 → 批准自动失效"})
        _log(job, "auto-reset approved → pending（成片或 clip 在批准后被改动）")
        save(job, data)
    return data


def act(job: Path, action: str, by: str, note: str = "", items: list[str] | None = None) -> dict:
    job = Path(job)
    data = stale(job, load(job))
    checks = objective_checks(job)
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    ok_all = all(c["ok"] for c in checks)

    if action == "submit":
        if data["state"] == "approved":
            return {"ok": False, "error": "已批准；要重批请先 --reject 或等成片改动自动回落"}
        data["state"] = "submitted"
    elif action == "approve":
        if data["state"] != "submitted":
            return {"ok": False, "error": f"当前状态 {data['state']}，不能直接批准（先 --submit）"}
        if not ok_all:
            bad = [f"{c['item']}（{c['detail']}）" for c in checks if not c["ok"]]
            return {"ok": False, "error": "客观门禁未过，不能批准：" + "；".join(bad)}
        data["state"] = "approved"
    elif action == "reject":
        if data["state"] not in ("submitted", "approved"):
            return {"ok": False, "error": f"当前状态 {data['state']}，无可驳回的决议"}
        if not note and not items:
            return {"ok": False, "error": "驳回必须写理由（--note）或指明镜号（--items）"}
        data["state"] = "changes_requested"
    else:
        return {"ok": False, "error": f"未知动作 {action}"}

    data.setdefault("rounds", []).append({
        "state": data["state"], "by": by, "at": now, "note": note,
        "items": items or [], "checks": {c["item"]: c["detail"] for c in checks},
    })
    data["updated_at"] = now
    save(job, data)
    _log(job, f"{action} by={by} state={data['state']} note={note!r} items={items or []}")
    return {"ok": True, "state": data["state"], "checks": checks, "rounds": len(data["rounds"])}


def render_md(job: Path, data: dict | None = None) -> str:
    """人读的审批单（跟 upload 包里的 CHECKS.md 配套）。"""
    job = Path(job)
    data = data or stale(job, load(job))
    checks = objective_checks(job)
    lines = [f"# 交付审批单 · {job.name}", "",
             f"**当前状态：{data['state']}**"
             + (f"（最后决议 {data.get('updated_at')}）" if data.get("updated_at") else ""), "",
             "## 客观门禁（批准时重跑，不信快照）", "",
             "| 项 | 实测 | 结论 |", "|---|---|---|"]
    for c in checks:
        lines.append(f"| {c['item']} | {c['detail']} | {'✅' if c['ok'] else '❌'} |")
    lines += ["", "## 决议历史", ""]
    if not data.get("rounds"):
        lines.append("（还没有任何决议）")
    for r in data.get("rounds", []):
        items = ("；涉及镜：" + ",".join(r.get("items", []))) if r.get("items") else ""
        lines.append(f"- **{r['state']}** · {r.get('at')} · {r.get('by')} "
                     f"{('· ' + r['note']) if r.get('note') else ''}{items}")
    lines += ["", "## 怎么改状态", "",
              "```bash",
              f"python3 shortdrama/approval.py {job} --submit --by agent",
              f"python3 shortdrama/approval.py {job} --approve --by director --note '过'",
              f"python3 shortdrama/approval.py {job} --reject --by director --note 's23 咬钥匙' --items 23",
              "```"]
    return "\n".join(lines) + "\n"


def write_md(job: Path) -> Path:
    p = Path(job) / "qc" / "approval.md"
    p.parent.mkdir(exist_ok=True)
    p.write_text(render_md(Path(job)), encoding="utf-8")
    return p


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="本地审批层：提交 / 批准 / 驳回 / 查看")
    ap.add_argument("job", help="集目录")
    ap.add_argument("--show", action="store_true", help="打印审批单（默认）")
    ap.add_argument("--submit", action="store_true")
    ap.add_argument("--approve", action="store_true")
    ap.add_argument("--reject", action="store_true")
    ap.add_argument("--by", default="", help="决议人（agent / director / …）")
    ap.add_argument("--note", default="", help="理由")
    ap.add_argument("--items", nargs="*", help="涉及的镜号")
    a = ap.parse_args(argv)

    job = Path(a.job).expanduser().resolve()
    if not job.is_dir():
        print(f"目录不存在：{job}")
        return 1

    action = "submit" if a.submit else "approve" if a.approve else "reject" if a.reject else None
    if action:
        res = act(job, action, a.by or "unknown", a.note, a.items)
        if not res.get("ok"):
            print(f"❌ {res.get('error')}")
            write_md(job)
            return 1
        print(f"✅ {action} → 状态 {res['state']}")
    write_md(job)
    print(render_md(job))
    return 0


if __name__ == "__main__":
    sys.exit(main())
