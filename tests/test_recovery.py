import tempfile
import unittest
from pathlib import Path

from jmbot.core.persistent_queue import PersistentAlbumQueue
from jmbot.core.recovery import recover_interrupted_jobs


class RecoveryTest(unittest.TestCase):
    def test_recover_interrupted_jobs_cleans_downloads_and_preserves_pdf(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue = PersistentAlbumQueue(root / "queue.sqlite3")
            queued = queue.enqueue_private("111111", user_id=42)
            queue.claim_next()
            album_dir = root / "downloads" / "111111"
            album_dir.mkdir(parents=True)
            (album_dir / "001.jpg").write_bytes(b"image")
            output_dir = root / "output"
            output_dir.mkdir()
            cached_pdf = output_dir / "JM111111.pdf"
            cached_pdf.write_bytes(b"pdf")

            recovered = recover_interrupted_jobs(queue, root / "downloads")
            claimed = queue.claim_next()
            album_exists = album_dir.exists()
            cached_pdf_exists = cached_pdf.exists()

        self.assertEqual([job.id for job in recovered], [queued.id])
        self.assertEqual(claimed.id, queued.id)
        self.assertFalse(album_exists)
        self.assertTrue(cached_pdf_exists)

    def test_recover_interrupted_variant_job_cleans_its_isolated_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue = PersistentAlbumQueue(root / "queue.sqlite3")
            queued = queue.enqueue_private(
                "111111",
                user_id=42,
                output_format="zip",
                chapters=(1, 3),
            )
            queue.claim_next()
            album_dir = root / "downloads" / "111111__ch1-3_zip"
            album_dir.mkdir(parents=True)
            (album_dir / "001.jpg").write_bytes(b"image")

            recovered = recover_interrupted_jobs(queue, root / "downloads")
            album_exists = album_dir.exists()

        self.assertEqual([job.id for job in recovered], [queued.id])
        self.assertFalse(album_exists)


if __name__ == "__main__":
    unittest.main()
