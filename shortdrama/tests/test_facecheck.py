"""人物造型检查（facecheck）的自检。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shortdrama import facecheck  # noqa: E402


class CheckTest(unittest.TestCase):
    def test_vague_description_fails(self) -> None:
        p = facecheck.check("亚洲年轻女性，24岁，漂亮，温柔，气质出众")
        joined = " ".join(p)
        self.assertIn("空泛词", joined)
        self.assertIn("脸型", joined)

    def test_good_description_passes(self) -> None:
        # 大纲里 c1 的实际描述（身份 + 造型分开）
        ch = {
            "id": "c1", "name": "林晚",
            "identity_cn": "亚洲女性，24岁",
            "desc_cn": ("长脸，颧骨微突，下颌线偏硬。深棕发尾挑极细银丝，刘海不对称——左到眉右到额，"
                        "发际线略高。眼下淡青色细纹。蓝色便利店制服，袖子卷过肘，挂绳工牌歪在左胸，"
                        "左手腕旧疤创可贴边缘总翘，口袋露出半截圆珠笔。"),
        }
        self.assertEqual(facecheck.check_character(ch), [])

    def test_missing_identity_is_reported(self) -> None:
        # 实测教训：不给性别/年龄，Z-Image 默认出男性
        ch = {"id": "c1", "desc_cn": ("长脸，颧骨微突，下颌线偏硬。深棕发尾挑极细银丝，"
                                    "刘海不对称。眼下淡青色细纹。蓝色便利店制服，挂绳工牌。")}
        joined = " ".join(facecheck.check_character(ch))
        self.assertIn("identity_cn", joined)

    def test_cast_prompt_joins_identity_and_style(self) -> None:
        ch = {"identity_cn": "亚洲女性，24岁", "desc_cn": "长脸，短发"}
        story = {"cast_prompt_cn": "人物定妆照，中性柔光，纯灰背景"}
        p = facecheck.cast_prompt(ch, story)
        self.assertIn("亚洲女性，24岁", p)
        self.assertIn("中性柔光", p)

    def test_missing_dimensions_each_reported(self) -> None:
        cases = {
            "没有脸型": "个子不高，穿蓝色便利店制服，左腕有疤，有法令纹，手里拿着一支笔",
            "没有不对称": "方脸下颌角明显，穿蓝色便利店制服，有法令纹，手里拿着一支笔",
            "没有年龄痕迹": "方脸下颌角明显，脸颊有颗痣，穿蓝色便利店制服，手里拿着一支笔",
            "没有衣着细节": "方脸下颌角明显，脸颊有颗痣，有法令纹，手里拿着一支笔",
            "没有物件细节": "方脸下颌角明显，脸颊有颗痣，有法令纹，穿蓝色便利店制服",
        }
        for expect, desc in cases.items():
            joined = " ".join(facecheck.check(desc))
            self.assertIn(expect, joined, f"{desc!r} 应报 {expect}")

    def test_empty_description(self) -> None:
        self.assertEqual(facecheck.check(""), ["造型描述为空"])

    def test_style_pollution_detected(self) -> None:
        bad = {"cast_prompt_cn": "人物定妆照，中性柔光，冷调青蓝加霓虹洋红，低照度"}
        self.assertTrue(facecheck.style_pollution(bad))
        good = {"cast_prompt_cn": "人物定妆照，胸部以上，正面，中性柔光，纯灰背景，高细节"}
        self.assertEqual(facecheck.style_pollution(good), [])

    def test_check_characters_prefixes_id(self) -> None:
        out = facecheck.check_characters([{"id": "c1", "name": "林晚", "desc_cn": "漂亮"}])
        self.assertTrue(out)
        self.assertTrue(all(o.startswith("c1") for o in out))


if __name__ == "__main__":
    unittest.main()
