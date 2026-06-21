import unittest
from pathlib import Path

from PIL import Image

from jmbot.core.pdf_builder import build_pdf, collect_images


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
