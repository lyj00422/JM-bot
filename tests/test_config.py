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
                "JM_BOT_MAX_CONCURRENCY": "3",
                "JM_BOT_DOWNLOAD_IMAGE_THREADS": "3",
                "JM_BOT_DOWNLOAD_PHOTO_THREADS": "1",
                "JM_BOT_JMCOMIC_LOG": "false",
                "JM_BOT_JMCOMIC_RETRY_TIMES": "2",
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
        self.assertEqual(config.max_concurrency, 3)
        self.assertEqual(config.download_image_threads, 3)
        self.assertEqual(config.download_photo_threads, 1)
        self.assertFalse(config.jmcomic_log)
        self.assertEqual(config.jmcomic_retry_times, 2)
        self.assertEqual(config.allowed_group_ids, {10001, 10002})
        self.assertEqual(config.superuser_ids, {42})
        self.assertFalse(config.clean_after_upload)
        self.assertEqual(config.group_folder_name, "Archive")

    def test_load_config_cleans_after_upload_by_default(self):
        self.assertTrue(load_config({}).clean_after_upload)

    def test_load_config_uses_jm_group_folder_by_default(self):
        self.assertEqual(load_config({}).group_folder_name, "JM")

    def test_load_config_rejects_invalid_resource_limits(self):
        invalid_cases = [
            {"JM_BOT_DOWNLOAD_IMAGE_THREADS": "0"},
            {"JM_BOT_DOWNLOAD_PHOTO_THREADS": "0"},
            {"JM_BOT_JMCOMIC_RETRY_TIMES": "0"},
        ]

        for env in invalid_cases:
            with self.subTest(env=env):
                with self.assertRaises(ValueError):
                    load_config(env)


if __name__ == "__main__":
    unittest.main()
