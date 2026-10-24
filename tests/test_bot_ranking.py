import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image

import jmbot.bot as bot_module
from jmbot.core.catalog_service import RankingQuery, RankingResult, SearchItem
from jmbot.core.command_service import QueueTarget, RankingReply
from jmbot.core.preview_service import CoverResult


class BotRankingTest(unittest.IsolatedAsyncioTestCase):
    async def test_key_commands_are_redacted_from_nonebot_event_logs(self):
        event = SimpleNamespace(
            get_plaintext=lambda: "/ds 学校 very-secret-key",
            user_id=42,
            group_id=99,
        )
        description = bot_module.GroupMessageEvent.get_event_description(event)
        self.assertIn("内容已脱敏", description)
        self.assertNotIn("very-secret-key", description)

    async def test_nonebot_does_not_register_legacy_chat_decisions(self):
        self.assertFalse(hasattr(bot_module, "chat_matcher"))
        self.assertFalse(hasattr(bot_module, "_attention_loop"))
        self.assertFalse(hasattr(bot_module, "_handle_group_chat"))

    async def test_send_ranking_card_copies_covers_delivers_and_cleans(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            query = RankingQuery(period="week", category="hanman")
            result = RankingResult(
                query,
                1,
                1,
                2,
                (
                    SearchItem("111111", "作品一", ()),
                    SearchItem("222222", "作品二", ()),
                ),
            )
            reply = RankingReply("fallback", result, ("111111", "222222"))
            cover_dirs = []

            def query_cover(album_id, preview_root, *, timeout_seconds):
                media_dir = root / "source" / album_id
                media_dir.mkdir(parents=True)
                cover_path = media_dir / "cover.jpg"
                Image.new("RGB", (120, 180), (220, 40, 40)).save(cover_path)
                cover_dirs.append(media_dir)
                return CoverResult(album_id, media_dir, cover_path)

            config = SimpleNamespace(
                preview_root=root / "preview",
                query_timeout_seconds=20,
                command_prefix="/jm",
            )
            target = QueueTarget(destination_type="private", group_id=None, user_id=42)
            delivered = AsyncMock()

            with patch.object(bot_module, "CONFIG", config), patch.object(
                bot_module, "query_cover", side_effect=query_cover
            ), patch.object(bot_module, "_send_cover", delivered):
                error = await bot_module._send_ranking_card(AsyncMock(), target, reply)

            self.assertIsNone(error)
            delivered.assert_awaited_once()
            self.assertTrue(all(not media_dir.exists() for media_dir in cover_dirs))
            self.assertFalse((root / "preview" / "ranking").exists())


if __name__ == "__main__":
    unittest.main()
