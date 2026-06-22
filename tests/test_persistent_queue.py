import tempfile
import unittest
from pathlib import Path

from jmbot.core.persistent_queue import PersistentAlbumQueue


class PersistentAlbumQueueTest(unittest.TestCase):
    def test_enqueues_group_and_private_jobs_in_created_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = PersistentAlbumQueue(Path(tmp) / "queue.sqlite3")

            first = queue.enqueue_group("111111", group_id=10001, user_id=42)
            second = queue.enqueue_private("222222", user_id=43)

            self.assertEqual(queue.count_ahead(first.id), 0)
            self.assertEqual(queue.count_ahead(second.id), 1)

            claimed_first = queue.claim_next()
            claimed_second = queue.claim_next()

        self.assertIsNotNone(claimed_first)
        self.assertIsNotNone(claimed_second)
        self.assertEqual(claimed_first.id, first.id)
        self.assertEqual(claimed_first.destination_type, "group")
        self.assertEqual(claimed_first.group_id, 10001)
        self.assertEqual(claimed_first.user_id, 42)
        self.assertEqual(claimed_second.id, second.id)
        self.assertEqual(claimed_second.destination_type, "private")
        self.assertIsNone(claimed_second.group_id)
        self.assertEqual(claimed_second.user_id, 43)

    def test_recovers_processing_jobs_after_restart_before_new_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "queue.sqlite3"
            queue = PersistentAlbumQueue(db_path)
            first = queue.enqueue_private("111111", user_id=42)
            queue.enqueue_private("222222", user_id=43)
            claimed = queue.claim_next()
            self.assertEqual(claimed.id, first.id)

            restarted = PersistentAlbumQueue(db_path)
            restarted.recover_interrupted_jobs()

            recovered = restarted.claim_next()

        self.assertIsNotNone(recovered)
        self.assertEqual(recovered.id, first.id)
        self.assertEqual(recovered.status, "processing")
        self.assertEqual(recovered.attempts, 2)

    def test_marks_jobs_done_and_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = PersistentAlbumQueue(Path(tmp) / "queue.sqlite3")
            done_job = queue.enqueue_private("111111", user_id=42)
            failed_job = queue.enqueue_private("222222", user_id=43)

            queue.mark_done(done_job.id)
            queue.mark_failed(failed_job.id, "network failed")

            self.assertEqual(queue.pending_count(), 0)
            failed_rows = queue.list_jobs(status="failed")

        self.assertEqual(len(failed_rows), 1)
        self.assertEqual(failed_rows[0].id, failed_job.id)
        self.assertEqual(failed_rows[0].error, "network failed")


if __name__ == "__main__":
    unittest.main()
