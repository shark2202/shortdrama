"""字幕可见性判据的回归测试（纯像素，不碰 GPU / ComfyUI）。

背景（两个真实误报/漏判）：
- 固定阈值 >225：整集亮度校正把白字压到 225 → 把"有字幕"判成"无字幕"（E02_P 假阻断）
- 纯亮像素计数：亮场景整片过阈值 → 把"没字幕"判成"有字幕"
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from shortdrama import episode  # noqa: E402


def band(w=1080, h=220, fill=30, text_boxes=((400, 90, 160, 40),), text=225, box=0):
    """造一条字幕带：背景 fill，上面有几个黑框 + 亮字块。"""
    px = [fill] * (w * h)
    for (x0, y0, bw, bh) in text_boxes:
        for y in range(y0, min(h, y0 + bh)):
            for x in range(x0, min(w, x0 + bw)):
                px[y * w + x] = box
        # 字：黑框中间留几行亮像素（模拟笔画）
        for y in range(y0 + 6, min(h, y0 + bh - 6)):
            for x in range(x0 + 8, min(w, x0 + bw - 8), 3):
                px[y * w + x] = text
    return px


class TestBandHasText(unittest.TestCase):
    def test_darkened_white_text_is_detected(self):
        # 亮度校正后白字 = 225（原 255 → −10.4%），旧固定阈值 >225 会漏
        px = band(text=225)
        self.assertTrue(episode.band_has_text(px, 1080))

    def test_normal_white_text_is_detected(self):
        self.assertTrue(episode.band_has_text(band(text=255), 1080))

    def test_empty_dark_band_has_no_text(self):
        self.assertFalse(episode.band_has_text(band(text_boxes=()), 1080))

    def test_uniform_bright_band_is_not_text(self):
        # 亮场景（整条带都是 210）不能判成"有字幕"
        px = band(text_boxes=(), fill=210)
        self.assertFalse(episode.band_has_text(px, 1080))

    def test_band_filled_with_bright_pixels_is_not_text(self):
        # 亮像素占比过高 → 是场景不是字
        px = band(text_boxes=((0, 0, 1080, 220),), text=255, box=255)
        self.assertFalse(episode.band_has_text(px, 1080))

    def test_empty_list(self):
        self.assertFalse(episode.band_has_text([], 1080))


if __name__ == "__main__":
    unittest.main()
