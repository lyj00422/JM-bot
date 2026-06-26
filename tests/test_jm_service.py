import tempfile
import unittest
from pathlib import Path

from PIL import Image

from jmbot.core.jm_service import (
    DownloadRequest,
    DownloadSettings,
    count_request_pages,
    create_pdf_for_album,
    download_comic,
    run_album_job,
)


class JmServiceTest(unittest.TestCase):
    def test_count_request_pages_sums_selected_chapters(self):
        class FakeClient:
            def get_album_detail(self, album_id):
                return type(
                    "Album",
                    (),
                    {"episode_list": [("p1", "1", "一"), ("p2", "2", "二"), ("p3", "3", "三")]},
                )()

            def get_photo_detail(self, photo_id, fetch_album=False, fetch_scramble_id=True):
                counts = {"p1": 2, "p2": 3, "p3": 4}
                return type("Photo", (), {"page_arr": list(range(counts[photo_id]))})()

        total = count_request_pages(
            DownloadRequest("123456", chapters=(1, 3)),
            client=FakeClient(),
        )

        self.assertEqual(total, 6)

    def test_precise_progress_writes_total_before_download_starts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def fake_downloader(album_id, option):
                album_dir = root / "downloads" / album_id
                marker = album_dir / ".progress-total"
                self.assertEqual(marker.read_text(encoding="utf-8"), "7")
                Image.new("RGB", (8, 8), (255, 0, 0)).save(album_dir / "001.jpg")

            download_comic(
                "123456",
                DownloadSettings(
                    download_root=root / "downloads",
                    option_root=root / "options",
                    progress_mode="precise",
                ),
                create_option=lambda option_path: object(),
                download_album=fake_downloader,
                page_total_fetcher=lambda request: 7,
            )

    def test_precise_progress_count_failure_does_not_block_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def fake_downloader(album_id, option):
                album_dir = root / "downloads" / album_id
                Image.new("RGB", (8, 8), (255, 0, 0)).save(album_dir / "001.jpg")

            result = download_comic(
                "123456",
                DownloadSettings(
                    download_root=root / "downloads",
                    option_root=root / "options",
                    progress_mode="precise",
                ),
                create_option=lambda option_path: object(),
                download_album=fake_downloader,
                page_total_fetcher=lambda request: (_ for _ in ()).throw(RuntimeError("metadata failed")),
            )

        self.assertEqual(result.album_id, "123456")

    def test_run_album_job_builds_full_zip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def fake_downloader(album_id, option):
                album_dir = root / "downloads" / f"{album_id}__zip"
                chapter = album_dir / "chapter"
                chapter.mkdir(parents=True)
                Image.new("RGB", (8, 8), (255, 0, 0)).save(chapter / "001.jpg")

            result = run_album_job(
                DownloadRequest("123456", output_format="zip"),
                DownloadSettings(download_root=root / "downloads", option_root=root / "options"),
                root / "output",
                create_option=lambda option_path: object(),
                download_album=fake_downloader,
            )

        self.assertEqual(result.output_paths[0].name, "JM123456.zip")

    def test_zip_request_does_not_reuse_pdf_split_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output_dir = root / "output"
            output_dir.mkdir()
            (output_dir / "JM123456.part1.pdf").write_bytes(b"pdf")
            (output_dir / "JM123456.parts.txt").write_text(
                "JM123456.part1.pdf\n",
                encoding="utf-8",
            )

            def fake_downloader(album_id, option):
                album_dir = root / "downloads" / f"{album_id}__zip"
                album_dir.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", (8, 8), (255, 0, 0)).save(album_dir / "001.jpg")

            result = run_album_job(
                DownloadRequest("123456", output_format="zip"),
                DownloadSettings(
                    download_root=root / "downloads",
                    option_root=root / "options",
                    auto_split_pdf=True,
                ),
                output_dir,
                create_option=lambda option_path: object(),
                download_album=fake_downloader,
            )

        self.assertEqual(result.output_paths[0].name, "JM123456.zip")

    def test_run_album_job_downloads_selected_chapters_and_marks_filename(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            calls = []

            def fake_album_detail(album_id):
                return type(
                    "Album",
                    (),
                    {"episode_list": [("p1", "1", "一"), ("p2", "2", "二"), ("p3", "3", "三")]},
                )()

            def fake_download_photo(photo_ids, option):
                calls.append(tuple(photo_ids))
                album_dir = root / "downloads" / "123456__ch1-3_pdf"
                album_dir.mkdir(parents=True, exist_ok=True)
                for index, photo_id in enumerate(photo_ids, start=1):
                    chapter = album_dir / photo_id
                    chapter.mkdir()
                    Image.new("RGB", (8, 8), (index, 0, 0)).save(chapter / "001.jpg")

            result = run_album_job(
                DownloadRequest("123456", output_format="pdf", chapters=(1, 3)),
                DownloadSettings(download_root=root / "downloads", option_root=root / "options"),
                root / "output",
                create_option=lambda option_path: object(),
                album_detail_fetcher=fake_album_detail,
                download_photo=fake_download_photo,
            )

        self.assertEqual(calls, [("p1", "p3")])
        self.assertEqual(result.output_paths[0].name, "JM123456（章节1,3）.pdf")

    def test_run_album_job_rejects_missing_chapter_number(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            with self.assertRaisesRegex(ValueError, "章节 3"):
                run_album_job(
                    DownloadRequest("123456", chapters=(3,)),
                    DownloadSettings(download_root=root / "downloads", option_root=root / "options"),
                    root / "output",
                    create_option=lambda option_path: object(),
                    album_detail_fetcher=lambda album_id: type(
                        "Album", (), {"episode_list": [("p1", "1", "一")]}
                    )(),
                    download_photo=lambda photo_ids, option: None,
                )

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
                DownloadSettings(
                    download_root=root / "downloads",
                    option_root=root / "options",
                    download_image_threads=3,
                    download_photo_threads=1,
                    jmcomic_log=False,
                    jmcomic_retry_times=2,
                ),
                create_option=fake_create_option,
                download_album=fake_downloader,
            )

            self.assertEqual(result.album_id, "123456")
            self.assertEqual(result.album_dir, root / "downloads" / "123456")
            self.assertEqual(calls["album_id"], "123456")
            self.assertIn("base_dir:", calls["option_text"])
            self.assertIn("123456", calls["option_text"])
            self.assertIn("log: false", calls["option_text"])
            self.assertIn("    image: 3", calls["option_text"])
            self.assertIn("    photo: 1", calls["option_text"])
            self.assertIn("  retry_times: 2", calls["option_text"])
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

    def test_run_album_job_uses_existing_pdf_without_downloading(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output_dir = root / "output"
            output_dir.mkdir()
            cached_pdf = output_dir / "JM123456.pdf"
            cached_pdf.write_bytes(b"pdf")

            def fake_create_option(option_path):
                raise AssertionError("create_option should not run when cached PDF exists")

            def fake_downloader(album_id, option):
                raise AssertionError("download_album should not run when cached PDF exists")

            result = run_album_job(
                "123456",
                DownloadSettings(download_root=root / "downloads", option_root=root / "options"),
                output_dir,
                create_option=fake_create_option,
                download_album=fake_downloader,
            )

        self.assertEqual(result.album_id, "123456")
        self.assertEqual(result.album_dir, root / "downloads" / "123456")
        self.assertEqual(result.pdf_path, cached_pdf)

    def test_run_album_job_uses_existing_split_pdfs_without_downloading(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output_dir = root / "output"
            output_dir.mkdir()
            part1 = output_dir / "JM123456.part1.pdf"
            part2 = output_dir / "JM123456.part2.pdf"
            manifest = output_dir / "JM123456.parts.txt"
            part1.write_bytes(b"pdf1")
            part2.write_bytes(b"pdf2")
            manifest.write_text("JM123456.part1.pdf\nJM123456.part2.pdf\n", encoding="utf-8")

            def fake_create_option(option_path):
                raise AssertionError("create_option should not run when split PDFs exist")

            def fake_downloader(album_id, option):
                raise AssertionError("download_album should not run when split PDFs exist")

            result = run_album_job(
                "123456",
                DownloadSettings(download_root=root / "downloads", option_root=root / "options", auto_split_pdf=True),
                output_dir,
                create_option=fake_create_option,
                download_album=fake_downloader,
            )

        self.assertEqual(result.album_id, "123456")
        self.assertEqual(result.pdf_path, part1)
        self.assertEqual(result.output_paths, (part1, part2))

    def test_run_album_job_ignores_incomplete_split_cache_without_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output_dir = root / "output"
            output_dir.mkdir()
            stale_part = output_dir / "JM123456.part1.pdf"
            stale_part.write_bytes(b"incomplete")
            calls = []

            def fake_downloader(album_id, option):
                calls.append(album_id)
                album_dir = root / "downloads" / album_id
                album_dir.mkdir(parents=True, exist_ok=True)
                for index in range(1, 4):
                    Image.new("RGB", (8, 8), (index, 0, 0)).save(album_dir / f"{index:03d}.jpg")

            result = run_album_job(
                "123456",
                DownloadSettings(
                    download_root=root / "downloads",
                    option_root=root / "options",
                    auto_split_pdf=True,
                    split_image_count=2,
                ),
                output_dir,
                create_option=lambda option_path: object(),
                download_album=fake_downloader,
            )

        self.assertEqual(calls, ["123456"])
        self.assertEqual([path.name for path in result.output_paths], ["JM123456.part1.pdf", "JM123456.part2.pdf"])

    def test_run_album_job_splits_large_album_into_part_pdfs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def fake_downloader(album_id, option):
                album_dir = root / "downloads" / album_id
                album_dir.mkdir(parents=True, exist_ok=True)
                for index in range(1, 6):
                    Image.new("RGB", (8, 8), (index, 0, 0)).save(album_dir / f"{index:03d}.jpg")

            result = run_album_job(
                "123456",
                DownloadSettings(
                    download_root=root / "downloads",
                    option_root=root / "options",
                    auto_split_pdf=True,
                    split_image_count=2,
                ),
                root / "output",
                create_option=lambda option_path: object(),
                download_album=fake_downloader,
            )

        self.assertEqual([path.name for path in result.output_paths], ["JM123456.part1.pdf", "JM123456.part2.pdf", "JM123456.part3.pdf"])


if __name__ == "__main__":
    unittest.main()
