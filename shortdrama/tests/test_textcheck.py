"""画面内文字检查（textcheck）的自检。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shortdrama import textcheck  # noqa: E402


class TextCheckTest(unittest.TestCase):
    def test_legible_date_flagged(self) -> None:
        shot = {"idx": 1, "beat": "旧照片特写，焦痕下露出印刷日期2025.1.15",
                "prompt_en": "close up of a photo with a printed date visible"}
        self.assertTrue(textcheck.check_shot(shot))

    def test_illegible_handwriting_ok(self) -> None:
        shot = {"idx": 35, "beat": "照片背面有手写字迹，被泪水完全模糊",
                "prompt_en": "extreme close up of the back of a photo, blurred handwriting illegible"}
        self.assertEqual(textcheck.check_shot(shot), [])

    def test_no_text_ok(self) -> None:
        shot = {"idx": 3, "beat": "便利店全景，凌晨三点，一根灯管在闪",
                "prompt_en": "wide shot of a convenience store interior at night, one fluorescent tube flickering"}
        self.assertEqual(textcheck.check_shot(shot), [])

    def test_timestamp_flagged(self) -> None:
        shot = {"idx": 1, "beat": "监控屏幕显示时间戳02:47:13",
                "prompt_en": "a security monitor displaying a timestamp"}
        self.assertTrue(textcheck.check_shot(shot))

    def test_find_shots_filters(self) -> None:
        story = {"scenes": [
            {"idx": 1, "beat": "printed date", "prompt_en": "a photo with a printed date"},
            {"idx": 2, "beat": "rain", "prompt_en": "rain on a window"},
        ]}
        hits = textcheck.find_shots(story)
        self.assertEqual([s["idx"] for s in hits], [1])


if __name__ == "__main__":
    unittest.main()
