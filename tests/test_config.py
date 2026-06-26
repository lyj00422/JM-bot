import unittest
from pathlib import Path

from jmbot.config import load_config, parse_int_set


class ConfigTest(unittest.TestCase):
    def test_parse_int_set_ignores_empty_parts(self):
        self.assertEqual(parse_int_set("123, 456,,789"), {123, 456, 789})

    def test_parse_int_set_rejects_invalid_value(self):
        with self.assertRaises(ValueError):
            parse_int_set("123,abc")

    def test_load_config_uses_environment_values(self):
        config = load_config(
            {
                "JM_BOT_HOST": "127.0.0.1",
                "JM_BOT_PORT": "9090",
                "JM_BOT_DOWNLOAD_ROOT": "downloads",
                "JM_BOT_OUTPUT_ROOT": "output",
                "JM_BOT_OPTION_ROOT": "options",
                "JM_BOT_QUEUE_DB": "queue/jobs.sqlite3",
                "JM_BOT_PREVIEW_ROOT": "preview",
                "JM_BOT_MAX_CONCURRENCY": "3",
                "JM_BOT_DOWNLOAD_IMAGE_THREADS": "3",
                "JM_BOT_DOWNLOAD_PHOTO_THREADS": "1",
                "JM_BOT_JMCOMIC_LOG": "false",
                "JM_BOT_JMCOMIC_RETRY_TIMES": "2",
                "JM_BOT_JOB_TIMEOUT_SECONDS": "600",
                "JM_BOT_AUTO_SPLIT_PDF": "false",
                "JM_BOT_SPLIT_IMAGE_COUNT": "50",
                "JM_BOT_QUERY_TIMEOUT_SECONDS": "15",
                "JM_BOT_QUERY_MAX_CONCURRENCY": "2",
                "JM_BOT_DETAIL_CACHE_TTL_SECONDS": "200",
                "JM_BOT_QUERY_CACHE_MAX_ENTRIES": "64",
                "JM_BOT_PREVIEW_PAGE_COUNT": "8",
                "JM_BOT_PREVIEW_TIMEOUT_SECONDS": "120",
                "JM_BOT_DEFAULT_FORMAT": "zip",
                "JM_BOT_ALLOWED_FORMATS": "pdf,zip",
                "JM_BOT_PROGRESS_MODE": "precise",
                "JM_BOT_ALLOWED_GROUPS": "10001,10002",
                "JM_BOT_SUPERUSERS": "42",
                "JM_BOT_CLEAN_AFTER_UPLOAD": "false",
                "JM_BOT_GROUP_FOLDER": "Archive",
            }
        )

        self.assertEqual(config.host, "127.0.0.1")
        self.assertEqual(config.port, 9090)
        self.assertEqual(config.download_root, Path("downloads"))
        self.assertEqual(config.output_root, Path("output"))
        self.assertEqual(config.option_root, Path("options"))
        self.assertEqual(config.queue_db_path, Path("queue/jobs.sqlite3"))
        self.assertEqual(config.preview_root, Path("preview"))
        self.assertEqual(config.max_concurrency, 3)
        self.assertEqual(config.download_image_threads, 3)
        self.assertEqual(config.download_photo_threads, 1)
        self.assertFalse(config.jmcomic_log)
        self.assertEqual(config.jmcomic_retry_times, 2)
        self.assertEqual(config.job_timeout_seconds, 600)
        self.assertFalse(config.auto_split_pdf)
        self.assertEqual(config.split_image_count, 50)
        self.assertEqual(config.query_timeout_seconds, 15)
        self.assertEqual(config.query_max_concurrency, 2)
        self.assertEqual(config.detail_cache_ttl_seconds, 200)
        self.assertEqual(config.query_cache_max_entries, 64)
        self.assertEqual(config.preview_page_count, 8)
        self.assertEqual(config.preview_timeout_seconds, 120)
        self.assertEqual(config.default_format, "zip")
        self.assertEqual(config.allowed_formats, {"pdf", "zip"})
        self.assertEqual(config.progress_mode, "precise")
        self.assertEqual(config.allowed_group_ids, {10001, 10002})
        self.assertEqual(config.superuser_ids, {42})
        self.assertFalse(config.clean_after_upload)
        self.assertEqual(config.group_folder_name, "Archive")

    def test_load_config_cleans_after_upload_by_default(self):
        self.assertTrue(load_config({}).clean_after_upload)

    def test_load_config_uses_jm_group_folder_by_default(self):
        self.assertEqual(load_config({}).group_folder_name, "JM")

    def test_load_config_uses_queue_db_default(self):
        self.assertEqual(load_config({}).queue_db_path, Path("data/queue.sqlite3"))

    def test_load_config_enables_general_defaults(self):
        config = load_config({})

        self.assertTrue(config.auto_split_pdf)
        self.assertEqual(config.split_image_count, 80)
        self.assertEqual(config.query_timeout_seconds, 20)
        self.assertEqual(config.query_max_concurrency, 1)
        self.assertEqual(config.detail_cache_ttl_seconds, 300)
        self.assertEqual(config.query_cache_max_entries, 128)
        self.assertEqual(config.preview_root, Path("data/preview"))
        self.assertEqual(config.preview_page_count, 10)
        self.assertEqual(config.preview_timeout_seconds, 180)
        self.assertEqual(config.default_format, "pdf")
        self.assertEqual(config.allowed_formats, {"pdf", "zip"})
        self.assertEqual(config.progress_mode, "precise")

    def test_load_config_rejects_invalid_resource_limits(self):
        invalid_cases = [
            {"JM_BOT_DOWNLOAD_IMAGE_THREADS": "0"},
            {"JM_BOT_DOWNLOAD_PHOTO_THREADS": "0"},
            {"JM_BOT_JMCOMIC_RETRY_TIMES": "0"},
            {"JM_BOT_JOB_TIMEOUT_SECONDS": "0"},
            {"JM_BOT_SPLIT_IMAGE_COUNT": "0"},
            {"JM_BOT_QUERY_TIMEOUT_SECONDS": "0"},
            {"JM_BOT_QUERY_MAX_CONCURRENCY": "0"},
            {"JM_BOT_DETAIL_CACHE_TTL_SECONDS": "0"},
            {"JM_BOT_QUERY_CACHE_MAX_ENTRIES": "0"},
            {"JM_BOT_PREVIEW_PAGE_COUNT": "0"},
            {"JM_BOT_PREVIEW_TIMEOUT_SECONDS": "0"},
            {"JM_BOT_DEFAULT_FORMAT": "rar"},
            {"JM_BOT_ALLOWED_FORMATS": "pdf,rar"},
            {"JM_BOT_PROGRESS_MODE": "verbose"},
        ]

        for env in invalid_cases:
            with self.subTest(env=env):
                with self.assertRaises(ValueError):
                    load_config(env)


if __name__ == "__main__":
    unittest.main()
