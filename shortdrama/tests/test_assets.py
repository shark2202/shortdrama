"""sd assets 的自检（纯标准库，python3 -m unittest discover -s shortdrama/tests）。

覆盖：扫描分类 / sidecar 读取 / 静态页里的相对路径引用（这是最容易坏的地方：
页面和产物不在同一根目录时，相对路径会指向不存在的文件）。
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shortdrama import assets  # noqa: E402


class ScanTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "video").mkdir()
        (self.root / "image").mkdir()
        (self.root / "video" / "clip01.mp4").write_bytes(b"0" * 100)
        (self.root / "image" / "c1.png").write_bytes(b"\x89PNG" + b"0" * 50)
        (self.root / "notes.txt").write_text("hello")
        (self.root / "ignored.bin").write_bytes(b"x")
        # sidecar：记录某次运行的元数据
        (self.root / "video" / "clip01.mp4.json").write_text(
            json.dumps({"seed": 3013, "tier": "t2v-bypass"}), encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_scan_classifies_and_ignores_unknown(self) -> None:
        items = assets.scan(self.root)
        kinds = {a.name: a.kind for a in items}
        self.assertEqual(kinds["clip01.mp4"], "video")
        self.assertEqual(kinds["c1.png"], "image")
        self.assertEqual(kinds["notes.txt"], "text")
        self.assertNotIn("ignored.bin", kinds)

    def test_sidecar_is_picked_up(self) -> None:
        items = {a.name: a for a in assets.scan(self.root)}
        self.assertEqual(items["clip01.mp4"].sidecar["seed"], 3013)

    def test_hash_computed_for_small_files(self) -> None:
        items = {a.name: a for a in assets.scan(self.root)}
        self.assertEqual(len(items["c1.png"].sha256), 64)

    def test_inventory_shape(self) -> None:
        inv = assets.inventory(self.root)
        self.assertEqual(inv["count"], 4)          # clip01.mp4 + clip01.mp4.json + c1.png + notes.txt
        self.assertEqual(inv["by_kind"]["video"], 1)
        self.assertIn("total_bytes", inv)


class HtmlTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "clips").mkdir()
        (self.root / "clips" / "01.mp4").write_bytes(b"0" * 10)
        (self.root / "cast").mkdir()
        (self.root / "cast" / "c1.png").write_bytes(b"\x89PNG")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_relative_paths_point_at_real_files(self) -> None:
        out = assets.write_html(self.root, out=self.root / "_assets.html")
        text = out.read_text(encoding="utf-8")
        self.assertIn('src="clips/01.mp4"', text)
        self.assertIn('src="cast/c1.png"', text)
        for rel in ("clips/01.mp4", "cast/c1.png"):
            self.assertTrue((self.root / rel).is_file())

    def test_kind_filter_buttons_present(self) -> None:
        out = assets.write_html(self.root)
        text = out.read_text(encoding="utf-8")
        self.assertIn('data-v="video"', text)
        self.assertIn('data-v="image"', text)

    def test_html_escapes_names(self) -> None:
        (self.root / "clips" / 'a<b>&"c.mp4').write_bytes(b"0")
        text = assets.write_html(self.root).read_text(encoding="utf-8")
        self.assertNotIn("a<b>", text)
        self.assertIn("a&lt;b&gt;", text)


if __name__ == "__main__":
    unittest.main()
