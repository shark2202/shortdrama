"""分镜/字幕两个纯函数的回归测试（不需要 GPU、不需要 ComfyUI）。"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from shortdrama import episode  # noqa: E402

STORY = {
    "style_en": "cinematic, film grain",
    "characters": [{"id": "c1"}, {"id": "c2"}],
    "scenes": [
        {"id": "s1", "idx": 1, "chars": ["c1"], "primary": "c1",
         "prompt_en": "A woman stands behind a counter.", "sfx_en": "rain",
         "subtitle_cn": "第一句", "duration_s": 5.2},
        {"id": "s2", "idx": 2, "chars": ["c2"], "primary": "c2",
         "prompt_en": "A man opens the door.", "sfx_en": "a chime",
         "subtitle_cn": "第二句", "duration_s": 5.2},
        # 物镜：ref=None → 不加角色句式，走 t2v（加了就会变成画人）
        {"id": "s3", "idx": 3, "chars": [], "primary": None, "ref": None,
         "prompt_en": "Close up of a photograph.", "sfx_en": "rain",
         "subtitle_cn": "", "duration_s": 5.2},
    ],
}


class TestBuildShots(unittest.TestCase):
    def setUp(self):
        self.shots = episode.build_shots(STORY)

    def test_count_and_order(self):
        self.assertEqual([s["idx"] for s in self.shots], [1, 2, 3])

    def test_prompt_sentence_shape(self):
        p = self.shots[0]["prompt_en"]
        self.assertTrue(p.startswith("<Picture 1> and <Picture 2> show the same character."))
        self.assertIn("cinematic, film grain", p)
        self.assertTrue(p.endswith("natural ambient audio: rain"))

    def test_primary_character_drives_reference(self):
        self.assertEqual(self.shots[0]["character"], "c1")
        self.assertEqual(self.shots[1]["character"], "c2")

    def test_object_shot_has_no_character_prefix(self):
        """物镜（ref=None）不能带角色句式——带了 H3 会去画人而不是画那个东西。"""
        p = self.shots[2]["prompt_en"]
        self.assertIsNone(self.shots[2]["ref"])
        self.assertFalse(p.startswith("<Picture 1>"))
        self.assertTrue(p.startswith("Close up of a photograph,"))
        self.assertIn("natural ambient audio: rain", p)

class TestBuildSrt(unittest.TestCase):
    def test_timeline_follows_actual_durations(self):
        srt = episode.build_srt(
            [{"subtitle_cn": "甲"}, {"subtitle_cn": "乙"}], [2.5, 3.0])
        self.assertIn("00:00:00,000 --> 00:00:02,500", srt)
        self.assertIn("00:00:02,500 --> 00:00:05,500", srt)
        self.assertIn("甲", srt)
        self.assertIn("乙", srt)

    def test_empty_subtitle_is_skipped_but_time_still_advances(self):
        srt = episode.build_srt([{"subtitle_cn": ""}, {"subtitle_cn": "乙"}], [2.0, 2.0])
        self.assertNotIn("甲", srt)
        self.assertIn("00:00:02,000 --> 00:00:04,000", srt)


if __name__ == "__main__":
    unittest.main()
