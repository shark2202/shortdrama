"""证据表的回归测试（用 ffmpeg 合成两个色块 clip，不碰 GPU、不碰 ComfyUI）。"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from shortdrama import evidence  # noqa: E402


def _make_clip(path: Path, color: str) -> None:
    subprocess.run([evidence.ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", f"color=c={color}:s=640x352:d=2",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "24", str(path)],
                   check=True)


class TestEvidence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.job = Path(self.tmp.name) / "D202611_001_E01"
        (self.job / "clips").mkdir(parents=True)
        _make_clip(self.job / "clips" / "01.mp4", "red")
        _make_clip(self.job / "clips" / "02.mp4", "blue")
        self.shots = [{"idx": 1, "ref": "c1", "subtitle_cn": "一句"},
                      {"idx": 2, "ref": None, "subtitle_cn": ""}]
        (self.job / "shots.json").write_text(
            json.dumps({"shots": self.shots}, ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_facts_have_measurable_fields(self):
        facts, meta = evidence.shot_facts(self.job, self.shots)
        self.assertEqual(len(facts), 2)
        self.assertIsNotNone(facts[0]["yavg"])
        self.assertIsNotNone(facts[0]["sig"])
        self.assertEqual(meta["shots"], 2)
        # 红与蓝：饱和度差异能被测出来（取样帧的 SATAVG）
        self.assertNotEqual(facts[0]["sat"], facts[1]["sat"])

    def test_brightness_jump_is_flagged(self):
        facts, _ = evidence.shot_facts(self.job, self.shots)
        flags = " ".join(facts[1].get("flags") or [])
        self.assertTrue("亮度跳变" in flags or "相同" in flags, flags)

    def test_write_evidence_files(self):
        facts, meta = evidence.shot_facts(self.job, self.shots)
        jp, mp = evidence.write_evidence(self.job, facts, meta)
        self.assertTrue(jp.exists() and mp.exists())
        text = mp.read_text(encoding="utf-8")
        self.assertIn("| 镜 |", text)
        data = json.loads(jp.read_text(encoding="utf-8"))
        self.assertEqual(len(data["shots"]), 2)


if __name__ == "__main__":
    unittest.main()
