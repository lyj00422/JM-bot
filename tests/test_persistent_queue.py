import tempfile
import unittest
from pathlib import Path

from jmbot.core.persistent_queue import DuplicateAlbumJobError, PersistentAlbumQueue


class PersistentAlbumQueueTest(unittest.TestCase):
    def test_new_jobs_persist_format_chapters_and_progress_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = PersistentAlbumQueue(Path(tmp) / "queue.sqlite3")

            job = queue.enqueue_private(
                "123456",
                user_id=42,
                output_format="zip",
                chapters=(1, 3, 4),
            )

        self.assertEqual(job.output_format, "zip")
        self.assertEqual(job.chapters, (1, 3, 4))
        self.assertEqual(job.stage, "queued")
        self.assertEqual(job.progress_current, 0)
        self.assertEqual(job.progress_total, 0)
        self.assertIsNone(job.started_at)

    def test_duplicate_identity_includes_format_and_chapters(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = PersistentAlbumQueue(Path(tmp) / "queue.sqlite3")
            queue.enqueue_private("123456", user_id=42, output_format="pdf", chapters=())
            queue.enqueue_private("123456", user_id=42, output_format="zip", chapters=())
            queue.enqueue_private("123456", user_id=42, output_format="pdf", chapters=(1,))

            with self.assertRaises(DuplicateAlbumJobError):
                queue.enqueue_private("123456", user_id=42, output_format="pdf", chapters=(1,))

            jobs = queue.list_jobs()

        self.assertEqual(len(jobs), 3)

    def test_updates_stage_and_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = PersistentAlbumQueue(Path(tmp) / "queue.sqlite3")
            job = queue.enqueue_private("123456", user_id=42)

            queue.update_progress(job.id, stage="downloading", current=3, total=10)
            updated = queue.list_jobs()[0]

        self.assertEqual(updated.stage, "downloading")
        self.assertEqual(updated.progress_current, 3)
        self.assertEqual(updated.progress_total, 10)

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

    def test_mark_done_if_processing_does_not_overwrite_canceled_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = PersistentAlbumQueue(Path(tmp) / "queue.sqlite3")
            queued = queue.enqueue_private("333333", user_id=44)
            claimed = queue.claim_next()
            self.assertEqual(claimed.id, queued.id)
            queue.admin_cancel_album(
                queued.album_id,
                destination_type="private",
                user_id=queued.user_id,
            )

            changed = queue.mark_done_if_processing(queued.id)
            row = queue.list_jobs()[0]

        self.assertFalse(changed)
        self.assertEqual(row.status, "canceled")

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

    def test_lists_active_jobs_for_current_user_context_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = PersistentAlbumQueue(Path(tmp) / "queue.sqlite3")
            own_group = queue.enqueue_group("111111", group_id=10001, user_id=42)
            queue.enqueue_group("222222", group_id=10001, user_id=99)
            queue.enqueue_group("333333", group_id=10002, user_id=42)
            queue.enqueue_private("444444", user_id=42)

            jobs = queue.list_active_for_user(
                destination_type="group",
                group_id=10001,
                user_id=42,
            )

        self.assertEqual([job.id for job in jobs], [own_group.id])

    def test_lists_history_for_current_user_context_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = PersistentAlbumQueue(Path(tmp) / "queue.sqlite3")
            own_done = queue.enqueue_group("111111", group_id=10001, user_id=42)
            own_failed = queue.enqueue_group("222222", group_id=10001, user_id=42)
            other_user = queue.enqueue_group("333333", group_id=10001, user_id=99)
            other_group = queue.enqueue_group("444444", group_id=10002, user_id=42)
            active = queue.enqueue_group("555555", group_id=10001, user_id=42)

            queue.mark_done(own_done.id)
            queue.mark_failed(own_failed.id, "network failed")
            queue.mark_done(other_user.id)
            queue.mark_failed(other_group.id, "other group failed")

            jobs = queue.list_history_for_user(
                destination_type="group",
                group_id=10001,
                user_id=42,
            )
            failed_jobs = queue.list_history_for_user(
                destination_type="group",
                group_id=10001,
                user_id=42,
                failed_only=True,
            )

        self.assertEqual([job.id for job in jobs], [own_failed.id, own_done.id])
        self.assertEqual([job.id for job in failed_jobs], [own_failed.id])
        self.assertNotIn(active.id, [job.id for job in jobs])

    def test_finds_latest_failed_job_for_current_user_context_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = PersistentAlbumQueue(Path(tmp) / "queue.sqlite3")
            other_user = queue.enqueue_group("111111", group_id=10001, user_id=99)
            queue.mark_failed(other_user.id, "other user failed")
            own_failed = queue.enqueue_group("111111", group_id=10001, user_id=42)
            other_group = queue.enqueue_group("111111", group_id=10002, user_id=42)

            queue.mark_failed(own_failed.id, "own failed")
            queue.mark_failed(other_group.id, "other group failed")

            found = queue.find_latest_failed_for_user(
                "111111",
                destination_type="group",
                group_id=10001,
                user_id=42,
            )
            missing = queue.find_latest_failed_for_user(
                "111111",
                destination_type="private",
                group_id=None,
                user_id=42,
            )

        self.assertIsNotNone(found)
        self.assertEqual(found.id, own_failed.id)
        self.assertIsNone(missing)

    def test_lists_recent_failed_jobs_globally_for_admin(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = PersistentAlbumQueue(Path(tmp) / "queue.sqlite3")
            failed = queue.enqueue_private("111111", user_id=42)
            done = queue.enqueue_private("222222", user_id=43)

            queue.mark_failed(failed.id, "network failed")
            queue.mark_done(done.id)

            jobs = queue.list_failed_jobs(limit=5)

        self.assertEqual([job.id for job in jobs], [failed.id])


if __name__ == "__main__":
    unittest.main()
