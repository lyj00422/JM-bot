import tempfile
import unittest
from pathlib import Path

from PIL import Image

from core.jm_service import DownloadSettings, create_pdf_for_album, download_comic


class JmServiceTest(unittest.TestCase):
    def test_download_comic_writes_option_and_calls_downloader(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            calls = {}

            def fake_create_option(option_path):
                calls["option_path_type"] = type(option_path)
                calls["option_path"] = Path(option_path)
                calls["option_text"] = Path(option_path).read_text(encoding="utf-8")
                return object()

            def fake_downloader(album_id, option):
                calls["album_id"] = album_id
                calls["option"] = option
                album_dir = root / "downloads" / album_id
                album_dir.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", (8, 8), (255, 0, 0)).save(album_dir / "001.jpg")

            result = download_comic(
                "123456",
                DownloadSettings(download_root=root / "downloads", option_root=root / "options"),
                create_option=fake_create_option,
                download_album=fake_downloader,
            )

            self.assertEqual(result.album_id, "123456")
            self.assertEqual(result.album_dir, root / "downloads" / "123456")
            self.assertEqual(calls["album_id"], "123456")
            self.assertIn("base_dir:", calls["option_text"])
            self.assertIn("123456", calls["option_text"])
            self.assertTrue(calls["option_path"].exists())
            self.assertIs(calls["option_path_type"], str)

    def test_create_pdf_for_album_uses_album_id_filename(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            album_dir = root / "downloads" / "123456"
            album_dir.mkdir(parents=True)
            Image.new("RGB", (8, 8), (255, 0, 0)).save(album_dir / "001.jpg")

            pdf_path = create_pdf_for_album("123456", album_dir, root / "output")

            self.assertEqual(pdf_path, root / "output" / "JM123456.pdf")
            self.assertGreater(pdf_path.stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
