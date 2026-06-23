import tempfile
import unittest
from pathlib import Path

from jmbot.core.persistent_queue import DuplicateAlbumJobError, PersistentAlbumQueue


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

    def test_rejects_duplicate_active_job_for_same_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = PersistentAlbumQueue(Path(tmp) / "queue.sqlite3")
            first = queue.enqueue_group("111111", group_id=10001, user_id=42)

            with self.assertRaises(DuplicateAlbumJobError) as raised:
                queue.enqueue_group("111111", group_id=10001, user_id=99)

        self.assertEqual(raised.exception.existing_job.id, first.id)

    def test_allows_same_album_for_different_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = PersistentAlbumQueue(Path(tmp) / "queue.sqlite3")
            group_job = queue.enqueue_group("111111", group_id=10001, user_id=42)
            other_group_job = queue.enqueue_group("111111", group_id=10002, user_id=43)
            private_job = queue.enqueue_private("111111", user_id=42)

            jobs = queue.list_jobs()

        self.assertEqual([job.id for job in jobs], [group_job.id, other_group_job.id, private_job.id])

    def test_user_cancel_only_cancels_own_pending_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = PersistentAlbumQueue(Path(tmp) / "queue.sqlite3")
            job = queue.enqueue_group("111111", group_id=10001, user_id=42)

            result = queue.cancel_pending_for_user("111111", destination_type="group", group_id=10001, user_id=42)
            rows = queue.list_jobs()

        self.assertEqual(result.status, "canceled")
        self.assertEqual(result.job.id, job.id)
        self.assertEqual(rows[0].status, "canceled")

    def test_user_cancel_reports_processing_without_canceling(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = PersistentAlbumQueue(Path(tmp) / "queue.sqlite3")
            job = queue.enqueue_private("111111", user_id=42)
            queue.claim_next()

            result = queue.cancel_pending_for_user("111111", destination_type="private", group_id=None, user_id=42)
            rows = queue.list_jobs()

        self.assertEqual(result.status, "processing")
        self.assertEqual(result.job.id, job.id)
        self.assertEqual(rows[0].status, "processing")

    def test_admin_clear_and_kill_mark_jobs_canceled(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = PersistentAlbumQueue(Path(tmp) / "queue.sqlite3")
            first = queue.enqueue_group("111111", group_id=10001, user_id=42)
            second = queue.enqueue_private("222222", user_id=43)
            queue.claim_next()

            killed = queue.admin_cancel_album("111111", destination_type="group", group_id=10001)
            cleared = queue.clear_pending()
            rows = queue.list_jobs()

        self.assertEqual([job.id for job in killed], [first.id])
        self.assertEqual(cleared, 1)
        self.assertEqual([job.status for job in rows], ["canceled", "canceled"])
        self.assertEqual(rows[0].error, "canceled by admin")
        self.assertEqual(rows[1].error, "cleared by admin")

    def test_admin_jump_moves_job_to_front(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = PersistentAlbumQueue(Path(tmp) / "queue.sqlite3")
            first = queue.enqueue_private("111111", user_id=42)
            second = queue.enqueue_private("222222", user_id=43)

            moved = queue.move_to_front(second.id)
            claimed = queue.claim_next()

        self.assertEqual(moved.id, second.id)
        self.assertEqual(claimed.id, second.id)


if __name__ == "__main__":
    unittest.main()
