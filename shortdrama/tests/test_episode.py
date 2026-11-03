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

    def test_duplicate_subtitle_is_burned_only_once(self):
        """同一句在多镜出现时，成片只烧第一次（观众不该看三遍同一句）。"""
        srt = episode.build_srt(
            [{"subtitle_cn": "你该回来了。"}, {"subtitle_cn": "别的"},
             {"subtitle_cn": "你该回来了。"}], [2.0, 2.0, 2.0])
        self.assertEqual(srt.count("你该回来了。"), 1)
        self.assertIn("别的", srt)
        self.assertIn("00:00:00,000 --> 00:00:02,000", srt)


class TestLintStory(unittest.TestCase):
    """剧本预检：毁集级的错必须挡住，缺字段只提示。"""

    def _story(self, **over):
        s = {
            "shot_count": 1, "duration_s": 5.2, "style_cn": "电影感",
            "characters": [{"id": "c1", "desc_cn": "亚洲年轻女性，24岁，黑色长直发，米色针织衫"}],
            "scenes": [{"id": "s1", "idx": 1, "ref": "c1", "duration_s": 5.2,
                        "prompt_en": "A woman stands behind a counter.",
                        "sfx_en": "rain", "subtitle_cn": "字幕", "prompt_cn": "收银台后的女人"}],
        }
        s.update(over)
        return s

    def _msgs(self, story, level=None):
        ps = episode.lint_story(story)
        if level:
            ps = [p for p in ps if p["level"] == level]
        return " | ".join(p["message"] for p in ps)

    def test_clean_story_has_no_blocking_problem(self):
        self.assertEqual("", self._msgs(self._story(), "block"))

    def test_chinese_prompt_is_blocking(self):
        story = self._story()
        story["scenes"][0]["prompt_en"] = "一个女孩站在收银台后面。"
        self.assertIn("含中文", self._msgs(story, "block"))

    def test_unknown_reference_character_is_blocking(self):
        story = self._story()
        story["scenes"][0]["ref"] = "c9"
        self.assertIn("不存在", self._msgs(story, "block"))

    def test_shot_count_mismatch_is_blocking(self):
        self.assertIn("shot_count", self._msgs(self._story(shot_count=3), "block"))

    def test_missing_sound_and_subtitle_are_warnings_only(self):
        story = self._story()
        story["scenes"][0]["sfx_en"] = ""
        story["scenes"][0]["subtitle_cn"] = ""
        msgs = self._msgs(story)
        self.assertIn("没有 sfx_en", msgs)
        self.assertIn("没有 subtitle_cn", msgs)
        self.assertEqual("", self._msgs(story, "block"))


class TestFramePrompt(unittest.TestCase):
    def test_prompt_contains_scene_character_and_style(self):
        story = {"style_cn": "电影感，冷调"}
        sc = {"prompt_cn": "便利店里的女人"}
        ch = {"desc_cn": "亚洲年轻女性，24岁"}
        p = episode.frame_prompt(story, sc, ch)
        self.assertIn("便利店里的女人", p)
        self.assertIn("亚洲年轻女性", p)
        self.assertIn("电影感", p)

    def test_falls_back_to_beat_when_prompt_cn_missing(self):
        p = episode.frame_prompt({"style_cn": ""}, {"beat": "开场：雨夜竹林"}, None)
        self.assertIn("开场：雨夜竹林", p)


if __name__ == "__main__":
    unittest.main()
