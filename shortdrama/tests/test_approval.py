"""本地审批层的状态机回归测试（纯文件，不碰 GPU / ComfyUI）。"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from shortdrama import approval  # noqa: E402


def _job(tmp: str) -> Path:
    """造一个"全绿"的 job 目录：质检 0 阻断 + 审片单填满 + 证据表 + 交付件。"""
    job = Path(tmp) / "D202611_001_E01"
    (job / "qc").mkdir(parents=True)
    (job / "clips").mkdir(parents=True)
    (job / "qc" / "review.md").write_text(
        "| # | 角色 | 字幕 | 关键帧 | 视频 | 时长 | seed | 结论 | 备注 |\n"
        "| 1 | c1 | 一句 | 01.png | 01.mp4 | 5.17s | 3014 | **合格** |  |\n",
        encoding="utf-8")
    (job / "qc" / "evidence.md").write_text("# 证据表\n", encoding="utf-8")
    (job / "report.json").write_text(json.dumps({
        "blocking": [], "episode_probe": {"subtitle_coverage": "32/35"}}), encoding="utf-8")
    for f in ("episode.mp4", "release_meta.json", "subtitles.srt", "subtitles.ass"):
        (job / f).write_text("x", encoding="utf-8")
    (job / "clips" / "manifest.json").write_text("[]", encoding="utf-8")
    return job


class TestApproval(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.job = _job(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_initial_state_is_pending(self):
        self.assertEqual(approval.load(self.job)["state"], "pending")

    def test_cannot_approve_without_submit(self):
        res = approval.act(self.job, "approve", "director")
        self.assertFalse(res["ok"])

    def test_submit_then_approve(self):
        self.assertTrue(approval.act(self.job, "submit", "agent")["ok"])
        res = approval.act(self.job, "approve", "director", "过")
        self.assertTrue(res["ok"])
        self.assertEqual(res["state"], "approved")
        data = approval.load(self.job)
        self.assertEqual(len(data["rounds"]), 2)
        self.assertTrue((self.job / "logs" / "approval.log").exists())

    def test_reject_requires_reason(self):
        approval.act(self.job, "submit", "agent")
        res = approval.act(self.job, "reject", "director")
        self.assertFalse(res["ok"])           # 没理由不许驳回
        res = approval.act(self.job, "reject", "director", "s23 咬钥匙", ["23"])
        self.assertTrue(res["ok"])
        self.assertEqual(res["state"], "changes_requested")

    def test_objective_gate_blocks_approval(self):
        # 审片单变成空模板 → 批准必须被拒
        (self.job / "qc" / "review.md").write_text(
            "| # | 角色 | 字幕 | 关键帧 | 视频 | 时长 | seed | 结论 | 备注 |\n"
            "| 1 | c1 | 一句 | 01.png | 01.mp4 | 5.17s | 3014 | ☐合格 ☐重出 |  |\n",
            encoding="utf-8")
        approval.act(self.job, "submit", "agent")
        res = approval.act(self.job, "approve", "director")
        self.assertFalse(res["ok"])
        self.assertIn("人工逐镜审片", res["error"])

    def test_approval_goes_stale_when_clips_change(self):
        approval.act(self.job, "submit", "agent")
        approval.act(self.job, "approve", "director", "过")
        self.assertEqual(approval.load(self.job)["state"], "approved")
        # 批准后 clip 被重出 → 批准自动失效
        import os
        import time
        future = time.time() + 60
        os.utime(self.job / "clips" / "manifest.json", (future, future))
        (self.job / "clips" / "01.mp4").write_text("x", encoding="utf-8")
        os.utime(self.job / "clips" / "01.mp4", (future, future))
        data = approval.stale(self.job, approval.load(self.job))
        self.assertEqual(data["state"], "pending")

    def test_markdown_contains_state_and_history(self):
        approval.act(self.job, "submit", "agent")
        md = approval.render_md(self.job)
        self.assertIn("submitted", md)
        self.assertIn("决议历史", md)


if __name__ == "__main__":
    unittest.main()
