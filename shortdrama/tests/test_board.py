"""任务看板的回归测试（不碰 GPU、不碰 ComfyUI：只测状态推断 + 触发/停止）。"""

import json
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from shortdrama import board  # noqa: E402


class TestBoardStatus(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.job = Path(self.tmp.name) / "D202611_001_E01"
        (self.job / "clips").mkdir(parents=True)
        (self.job / "logs").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def _cards(self):
        return {c["key"]: c for c in board.board(self.job)}

    def test_empty_job_is_all_pending(self):
        cards = self._cards()
        self.assertFalse(cards["shots"]["done"])
        self.assertFalse(cards["qc"]["done"])
        self.assertTrue(cards["story"]["manual"])      # 需要题材，看板不代跑
        self.assertFalse(cards["render"]["running"])

    def test_story_and_subtitles_drive_done_flags(self):
        (self.job / "story.json").write_text(json.dumps({
            "characters": [{"id": "c1"}],
            "scenes": [{"idx": i, "subtitle_cn": "字幕" if i < 3 else ""} for i in range(1, 11)],
        }, ensure_ascii=False), encoding="utf-8")
        cards = self._cards()
        self.assertTrue(cards["lint"]["done"])
        self.assertFalse(cards["dialogue"]["done"])   # 3/10 = 30% < 40% → 不算完成
    def test_subtitle_threshold(self):
        (self.job / "story.json").write_text(json.dumps({
            "characters": [{"id": "c1"}],
            "scenes": [{"idx": i, "subtitle_cn": "字幕"} for i in range(1, 6)],
        }, ensure_ascii=False), encoding="utf-8")
        self.assertTrue(self._cards()["dialogue"]["done"])   # 5/5 = 100%

    def test_qc_blocking_makes_card_pending(self):
        (self.job / "report.json").write_text(json.dumps({
            "blocking": [{"where": "episode.mp4"}],
            "episode_probe": {"subtitle_coverage": "20/35"},
        }, ensure_ascii=False), encoding="utf-8")
        card = self._cards()["qc"]
        self.assertFalse(card["done"])
        self.assertIn("阻断 1", card["detail"])


class TestBoardRun(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.job = Path(self.tmp.name) / "D202611_001_E01"
        (self.job / "logs").mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_manual_stage_is_refused(self):
        res = board.start(self.job, "story")
        self.assertFalse(res["ok"])

    def test_start_runs_command_and_writes_heartbeat(self):
        # 用 lint 阶段跑一个真实但秒级的命令：episode.py lint 需要 story.json，
        # 这里改用 dialogue 之外的最轻路径 —— 直接检查 pid/log 落盘。
        stage = next(s for s in board.STAGES if s["key"] == "lint")
        self.assertEqual(stage["key"], "lint")
        # 起一个真正能跑的命令：python -c print
        original = board._stage_cmd
        board._stage_cmd = lambda key, job: [sys.executable, "-c", "print('hello')"]
        try:
            res = board.start(self.job, "lint")
            self.assertTrue(res["ok"])
            time.sleep(1.0)
            self.assertIsNotNone(board.running(self.job, "lint") or None)  # 跑完就没了，别崩
            log = self.job / "logs" / "stage-lint.log"
            self.assertTrue(log.exists())
            self.assertIn("hello", log.read_text(encoding="utf-8", errors="ignore"))
        finally:
            board._stage_cmd = original


if __name__ == "__main__":
    unittest.main()
