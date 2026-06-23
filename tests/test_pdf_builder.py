import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from jmbot.core.pdf_builder import build_pdf, build_split_pdfs, collect_images


def make_image(path: Path, color: tuple[int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (16, 16), color).save(path)


class PdfBuilderTest(unittest.TestCase):
    def test_collect_images_orders_nested_numeric_files(self):
        root = Path(self._testMethodName)
        try:
            make_image(root / "chapter2" / "002.png", (0, 255, 0))
            make_image(root / "chapter1" / "001.jpg", (255, 0, 0))
            (root / "chapter1" / "note.txt").write_text("ignore", encoding="utf-8")

            images = collect_images(root)

            self.assertEqual([p.name for p in images], ["001.jpg", "002.png"])
        finally:
            self._remove_tree(root)

    def test_build_pdf_creates_non_empty_pdf(self):
        root = Path(self._testMethodName)
        try:
            make_image(root / "001.jpg", (255, 0, 0))
            make_image(root / "002.png", (0, 0, 255))

            pdf_path = build_pdf(root, root / "out.pdf")

            self.assertTrue(pdf_path.exists())
            self.assertGreater(pdf_path.stat().st_size, 0)
        finally:
            self._remove_tree(root)

    def test_build_pdf_uses_img2pdf_converter_when_available(self):
        root = Path(self._testMethodName)
        calls = []

        def convert(paths):
            calls.append(paths)
            return b"%PDF-1.4\n% fake\n"

        try:
            make_image(root / "001.jpg", (255, 0, 0))
            make_image(root / "002.png", (0, 0, 255))

            fake_img2pdf = types.SimpleNamespace(convert=convert)
            with patch.dict(sys.modules, {"img2pdf": fake_img2pdf}):
                pdf_path = build_pdf(root, root / "out.pdf")

            self.assertEqual(pdf_path.read_bytes(), b"%PDF-1.4\n% fake\n")
            self.assertEqual(calls, [[str(root / "001.jpg"), str(root / "002.png")]])
        finally:
            self._remove_tree(root)

    def test_build_split_pdfs_creates_numbered_parts(self):
        root = Path(self._testMethodName)
        calls = []

        def convert(paths):
            calls.append(paths)
            return f"%PDF-1.4\n% part {len(calls)}\n".encode()

        try:
            for index in range(1, 6):
                make_image(root / f"{index:03d}.jpg", (index, 0, 0))

            fake_img2pdf = types.SimpleNamespace(convert=convert)
            with patch.dict(sys.modules, {"img2pdf": fake_img2pdf}):
                parts = build_split_pdfs(root, root / "output", "JM123456", images_per_part=2)

            self.assertEqual([part.name for part in parts], ["JM123456.part1.pdf", "JM123456.part2.pdf", "JM123456.part3.pdf"])
            self.assertEqual([len(call) for call in calls], [2, 2, 1])
            self.assertTrue(all(part.exists() for part in parts))
        finally:
            self._remove_tree(root)

    def test_build_pdf_raises_when_no_images(self):
        root = Path(self._testMethodName)
        try:
            root.mkdir(exist_ok=True)

            with self.assertRaises(ValueError):
                build_pdf(root, root / "out.pdf")
        finally:
            self._remove_tree(root)

    @staticmethod
    def _remove_tree(path: Path) -> None:
        if not path.exists():
            return
        for child in sorted(path.rglob("*"), reverse=True):
            if child.is_file():
                child.unlink()
            else:
                child.rmdir()
        path.rmdir()


if __name__ == "__main__":
    unittest.main()
