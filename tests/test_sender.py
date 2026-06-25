import tempfile
import unittest
from pathlib import Path

from jmbot.core.sender import (
    ensure_group_folder,
    send_group_image,
    send_private_image,
    upload_group_pdf,
    upload_private_pdf,
)


class FakeBot:
    def __init__(self, folders=None):
        self.folders = list(folders or [])
        self.calls = []

    async def call_api(self, action, **params):
        self.calls.append((action, params))

        if action == "get_group_root_files":
            return {"files": [], "folders": list(self.folders)}

        if action == "create_group_file_folder":
            self.folders.append(
                {
                    "folder_id": f"folder-{params['name']}",
                    "folder_name": params["name"],
                }
            )
            return None

        if action in {"upload_group_file", "upload_private_file", "send_group_msg", "send_private_msg"}:
            return None

        raise AssertionError(f"Unexpected action: {action}")


class SenderTest(unittest.IsolatedAsyncioTestCase):
    async def test_ensure_group_folder_reuses_existing_folder(self):
        bot = FakeBot([{"folder_id": "abc", "folder_name": "JM"}])

        folder_id = await ensure_group_folder(bot, 1053512745, "JM")

        self.assertEqual(folder_id, "abc")
        self.assertEqual(bot.calls, [("get_group_root_files", {"group_id": 1053512745})])

    async def test_ensure_group_folder_creates_missing_folder(self):
        bot = FakeBot()

        folder_id = await ensure_group_folder(bot, 1053512745, "JM")

        self.assertEqual(folder_id, "folder-JM")
        self.assertEqual(
            bot.calls,
            [
                ("get_group_root_files", {"group_id": 1053512745}),
                (
                    "create_group_file_folder",
                    {"group_id": 1053512745, "name": "JM", "parent_id": "/"},
                ),
                ("get_group_root_files", {"group_id": 1053512745}),
            ],
        )

    async def test_upload_group_pdf_uploads_to_folder(self):
        bot = FakeBot([{"folder_id": "abc", "folder_name": "JM"}])
        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = Path(tmp) / "JM1949101.pdf"
            pdf_path.write_bytes(b"pdf")

            await upload_group_pdf(bot, 1053512745, pdf_path, "JM")

        self.assertEqual(
            bot.calls[-1],
            (
                "upload_group_file",
                {
                    "group_id": 1053512745,
                    "file": pdf_path.resolve().as_uri(),
                    "name": "JM1949101.pdf",
                    "folder": "abc",
                },
            ),
        )

    async def test_upload_private_pdf_uploads_to_friend(self):
        bot = FakeBot()
        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = Path(tmp) / "JM1949101.pdf"
            pdf_path.write_bytes(b"pdf")

            await upload_private_pdf(bot, 2661195769, pdf_path)

        self.assertEqual(
            bot.calls[-1],
            (
                "upload_private_file",
                {
                    "user_id": 2661195769,
                    "file": pdf_path.resolve().as_uri(),
                    "name": "JM1949101.pdf",
                },
            ),
        )

    async def test_send_group_image_uses_image_message_segment(self):
        bot = FakeBot()
        with tempfile.TemporaryDirectory() as tmp:
            image_path = Path(tmp) / "cover.jpg"
            image_path.write_bytes(b"image")

            await send_group_image(bot, 1053512745, image_path)

        self.assertEqual(
            bot.calls[-1],
            (
                "send_group_msg",
                {
                    "group_id": 1053512745,
                    "message": [{"type": "image", "data": {"file": image_path.resolve().as_uri()}}],
                },
            ),
        )

    async def test_send_private_image_uses_image_message_segment(self):
        bot = FakeBot()
        with tempfile.TemporaryDirectory() as tmp:
            image_path = Path(tmp) / "cover.jpg"
            image_path.write_bytes(b"image")

            await send_private_image(bot, 2661195769, image_path)

        self.assertEqual(
            bot.calls[-1],
            (
                "send_private_msg",
                {
                    "user_id": 2661195769,
                    "message": [{"type": "image", "data": {"file": image_path.resolve().as_uri()}}],
                },
            ),
        )


if __name__ == "__main__":
    unittest.main()
