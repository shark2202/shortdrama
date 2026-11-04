#!/usr/bin/env python3
"""盯着 GPU，直到状态变化才退出（给 agent 用：状态一变就叫醒我）。

为什么不是固定轮询：轮询只会刷屏；"状态变了才通知"才是有效信号。
判定"变化"的依据 = 渲染开关 + 当前工作流类型 + 当前镜头提示词的前 60 字。

用法：
  python3 tools/gpu_watch_until_change.py            # 等到状态变化
  python3 tools/gpu_watch_until_change.py --timeout 7200 --interval 60
退出码：0 = 变了（打印前后对比）；1 = 超时没变。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gpu_watch  # noqa: E402


def signature(s: dict) -> str:
    c = s["comfy"]
    job = str(c.get("job") or "")
    return f"{'busy' if c.get('running') else 'idle'}|{job[:60]}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=int, default=7200, help="最长等待秒数（默认 2 小时）")
    ap.add_argument("--interval", type=int, default=60, help="采样间隔秒数")
    a = ap.parse_args()

    base = gpu_watch.snapshot()
    base_sig = signature(base)
    print(f"[wait] 起始状态：{base_sig}")
    print(f"[wait] {gpu_watch.human(base)}")
    t0 = time.time()
    while time.time() - t0 < a.timeout:
        time.sleep(a.interval)
        cur = gpu_watch.snapshot()
        sig = signature(cur)
        if sig != base_sig:
            print(f"\n[wait] 检测到状态变化（等待 {int(time.time()-t0)}s）")
            print(f"[wait] 之前：{base_sig}")
            print(f"[wait] 现在：{sig}")
            print(gpu_watch.human(cur))
            return 0
    print(f"[wait] {a.timeout}s 内没有变化")
    print(gpu_watch.human(gpu_watch.snapshot()))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
