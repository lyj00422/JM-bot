import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from jmbot.core.preview_service import build_preview, download_cover


class FakeImage:
    def __init__(self, index):
        self.index = index
        self.download_url = f"https://example.test/{index:03d}.webp"


class FakePhoto:
    def __init__(self, count):
        self.page_arr = [f"{index:03d}.webp" for index in range(1, count + 1)]

    def create_image_detail(self, index):
        return FakeImage(index + 1)


class FakeClient:
    def __init__(self, page_count=12):
        self.page_count = page_count
        self.calls = []

    def download_album_cover(self, album_id, save_path, size=""):
        self.calls.append(("cover", album_id, Path(save_path), size))
        Path(save_path).write_bytes(b"cover")

    def get_album_detail(self, album_id):
        self.calls.append(("album", album_id))
        return SimpleNamespace(episode_list=[("900001", "1", "第一话")])

    def get_photo_detail(self, photo_id, fetch_album=False, fetch_scramble_id=True):
        self.calls.append(("photo", photo_id, fetch_album, fetch_scramble_id))
        return FakePhoto(self.page_count)

    def download_by_image_detail(self, image, save_path, decode_image=True):
        self.calls.append(("image", image.index, Path(save_path), decode_image))
        Path(save_path).write_bytes(f"page-{image.index}".encode())


class PreviewServiceTest(unittest.TestCase):
    def test_download_cover_uses_jmcomic_cover_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = FakeClient()

            result = download_cover("271673", Path(tmp), client=client)

            self.assertEqual(result.album_id, "271673")
            self.assertEqual(result.cover_path.name, "JM271673-cover.jpg")
            self.assertEqual(result.cover_path.read_bytes(), b"cover")
            self.assertEqual(client.calls[0][0:2], ("cover", "271673"))

    def test_build_preview_downloads_first_ten_body_pages(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = FakeClient(page_count=12)
            built = []

            def pdf_builder(image_root, output):
                images = sorted(Path(image_root).glob("page-*"))
                built.append([path.name for path in images])
                Path(output).write_bytes(b"pdf")
                return Path(output)

            result = build_preview(
                "271673",
                Path(tmp),
                page_count=10,
                client=client,
                pdf_builder=pdf_builder,
            )

            image_calls = [call for call in client.calls if call[0] == "image"]
            self.assertEqual([call[1] for call in image_calls], list(range(1, 11)))
            self.assertEqual(len(result.page_paths), 10)
            self.assertEqual(result.pdf_path.name, "JM271673（预览）.pdf")
            self.assertEqual(built, [[f"page-{index:03d}.webp" for index in range(1, 11)]])
            self.assertFalse(any("cover" in name for name in built[0]))

    def test_build_preview_accepts_chapter_with_fewer_than_ten_pages(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = FakeClient(page_count=3)

            def pdf_builder(image_root, output):
                Path(output).write_bytes(b"pdf")
                return Path(output)

            result = build_preview(
                "271673",
                Path(tmp),
                page_count=10,
                client=client,
                pdf_builder=pdf_builder,
            )

            self.assertEqual(len(result.page_paths), 3)


if __name__ == "__main__":
    unittest.main()
