import tempfile
import unittest
from pathlib import Path

from jmbot.core.jm_service import AlbumJobResult
from jmbot.core.persistent_queue import PersistentAlbumQueue
from jmbot.core.queue_processor import process_next_album_job


class QueueProcessorTest(unittest.IsolatedAsyncioTestCase):
    async def test_processes_group_job_and_marks_done(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = PersistentAlbumQueue(Path(tmp) / "queue.sqlite3")
            queued = queue.enqueue_group("111111", group_id=10001, user_id=42)
            calls = []
            pdf_path = Path(tmp) / "JM111111.pdf"
            pdf_path.write_bytes(b"pdf")

            async def run_job(album_id):
                calls.append(("run", album_id))
                return AlbumJobResult(album_id=album_id, album_dir=Path(tmp) / album_id, pdf_path=pdf_path)

            async def upload_group(group_id, path):
                calls.append(("upload_group", group_id, path))

            async def upload_private(user_id, path):
                calls.append(("upload_private", user_id, path))

            async def notify(job, message):
                calls.append(("notify", job.id, message))

            async def cleanup(album_dir, path):
                calls.append(("cleanup", album_dir, path))

            processed = await process_next_album_job(
                queue,
                run_job=run_job,
                upload_group=upload_group,
                upload_private=upload_private,
                notify=notify,
                cleanup=cleanup,
            )

            rows = queue.list_jobs()

        self.assertTrue(processed)
        self.assertEqual(rows[0].id, queued.id)
        self.assertEqual(rows[0].status, "done")
        self.assertIn(("run", "111111"), calls)
        self.assertIn(("upload_group", 10001, pdf_path), calls)
        self.assertNotIn(("upload_private", 42, pdf_path), calls)
        self.assertIn(("cleanup", Path(tmp) / "111111", pdf_path), calls)

    async def test_processes_group_job_with_multiple_pdf_parts(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = PersistentAlbumQueue(Path(tmp) / "queue.sqlite3")
            queued = queue.enqueue_group("111111", group_id=10001, user_id=42)
            calls = []
            part1 = Path(tmp) / "JM111111.part1.pdf"
            part2 = Path(tmp) / "JM111111.part2.pdf"
            part1.write_bytes(b"pdf1")
            part2.write_bytes(b"pdf2")

            async def run_job(album_id):
                return AlbumJobResult(
                    album_id=album_id,
                    album_dir=Path(tmp) / album_id,
                    pdf_path=part1,
                    pdf_paths=(part1, part2),
                )

            async def upload_group(group_id, path):
                calls.append(("upload_group", group_id, path))

            async def upload_private(user_id, path):
                calls.append(("upload_private", user_id, path))

            async def notify(job, message):
                calls.append(("notify", message))

            async def cleanup(album_dir, path):
                calls.append(("cleanup", album_dir, path))

            processed = await process_next_album_job(
                queue,
                run_job=run_job,
                upload_group=upload_group,
                upload_private=upload_private,
                notify=notify,
                cleanup=cleanup,
            )

            rows = queue.list_jobs()

        self.assertTrue(processed)
        self.assertEqual(rows[0].id, queued.id)
        self.assertEqual(rows[0].status, "done")
        self.assertIn(("upload_group", 10001, part1), calls)
        self.assertIn(("upload_group", 10001, part2), calls)
        self.assertNotIn(("upload_private", 42, part1), calls)
        self.assertIn(("cleanup", Path(tmp) / "111111", part1), calls)
        self.assertIn(("cleanup", Path(tmp) / "111111", part2), calls)
        notify_messages = [call[1] for call in calls if call[0] == "notify"]
        self.assertTrue(any("JM111111.part1.pdf" in message and "JM111111.part2.pdf" in message for message in notify_messages))

    async def test_processes_private_job_and_marks_failed_on_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = PersistentAlbumQueue(Path(tmp) / "queue.sqlite3")
            queued = queue.enqueue_private("222222", user_id=43)
            messages = []

            async def run_job(album_id):
                raise RuntimeError(f"download failed for {album_id}")

            async def upload_group(group_id, path):
                raise AssertionError("group upload should not run")

            async def upload_private(user_id, path):
                raise AssertionError("private upload should not run")

            async def notify(job, message):
                messages.append((job.id, message))

            processed = await process_next_album_job(
                queue,
                run_job=run_job,
                upload_group=upload_group,
                upload_private=upload_private,
                notify=notify,
            )

            failed_rows = queue.list_jobs(status="failed")

        self.assertTrue(processed)
        self.assertEqual(failed_rows[0].id, queued.id)
        self.assertEqual(failed_rows[0].error, "download failed for 222222")
        self.assertTrue(any("处理失败" in message for _, message in messages))

    async def test_failure_notification_hides_raw_error_but_persists_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = PersistentAlbumQueue(Path(tmp) / "queue.sqlite3")
            queued = queue.enqueue_private("555555", user_id=45)
            messages = []

            async def run_job(album_id):
                raise RuntimeError("secret upstream failure")

            async def upload_group(group_id, path):
                raise AssertionError("group upload should not run")

            async def upload_private(user_id, path):
                raise AssertionError("private upload should not run")

            async def notify(job, message):
                messages.append(message)

            processed = await process_next_album_job(
                queue,
                run_job=run_job,
                upload_group=upload_group,
                upload_private=upload_private,
                notify=notify,
            )

            failed_rows = queue.list_jobs(status="failed")

        self.assertTrue(processed)
        self.assertEqual(failed_rows[0].id, queued.id)
        self.assertEqual(failed_rows[0].error, "secret upstream failure")
        self.assertTrue(any("JM555555 处理失败" in message for message in messages))
        self.assertFalse(any("secret upstream failure" in message for message in messages))

    async def test_keeps_job_done_when_success_notification_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = PersistentAlbumQueue(Path(tmp) / "queue.sqlite3")
            queued = queue.enqueue_private("333333", user_id=44)
            pdf_path = Path(tmp) / "JM333333.pdf"
            pdf_path.write_bytes(b"pdf")

            async def run_job(album_id):
                return AlbumJobResult(album_id=album_id, album_dir=Path(tmp) / album_id, pdf_path=pdf_path)

            async def upload_group(group_id, path):
                raise AssertionError("group upload should not run")

            async def upload_private(user_id, path):
                return None

            async def notify(job, message):
                if "已上传" in message:
                    raise RuntimeError("message API failed")

            processed = await process_next_album_job(
                queue,
                run_job=run_job,
                upload_group=upload_group,
                upload_private=upload_private,
                notify=notify,
            )

            rows = queue.list_jobs()

        self.assertTrue(processed)
        self.assertEqual(rows[0].id, queued.id)
        self.assertEqual(rows[0].status, "done")

    async def test_skips_upload_when_job_is_canceled_after_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = PersistentAlbumQueue(Path(tmp) / "queue.sqlite3")
            queued = queue.enqueue_private("444444", user_id=44)
            pdf_path = Path(tmp) / "JM444444.pdf"
            pdf_path.write_bytes(b"pdf")
            calls = []

            async def run_job(album_id):
                queue.admin_cancel_album(album_id, destination_type="private", user_id=44)
                return AlbumJobResult(album_id=album_id, album_dir=Path(tmp) / album_id, pdf_path=pdf_path)

            async def upload_group(group_id, path):
                raise AssertionError("group upload should not run")

            async def upload_private(user_id, path):
                calls.append(("upload_private", user_id, path))

            async def notify(job, message):
                calls.append(("notify", message))

            processed = await process_next_album_job(
                queue,
                run_job=run_job,
                upload_group=upload_group,
                upload_private=upload_private,
                notify=notify,
                is_canceled=queue.is_canceled,
            )

            rows = queue.list_jobs()

        self.assertTrue(processed)
        self.assertEqual(rows[0].id, queued.id)
        self.assertEqual(rows[0].status, "canceled")
        self.assertNotIn(("upload_private", 44, pdf_path), calls)
        self.assertTrue(any("已取消" in message for _, message in calls))

    async def test_returns_false_when_queue_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = PersistentAlbumQueue(Path(tmp) / "queue.sqlite3")

            processed = await process_next_album_job(
                queue,
                run_job=lambda album_id: None,
                upload_group=lambda group_id, path: None,
                upload_private=lambda user_id, path: None,
                notify=lambda job, message: None,
            )

        self.assertFalse(processed)


if __name__ == "__main__":
    unittest.main()
