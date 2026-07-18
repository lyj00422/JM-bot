import tempfile
import unittest
from pathlib import Path

from PIL import Image

from jmbot.core.catalog_service import RankingQuery, RankingResult, SearchItem, SearchResult
from jmbot.core.search_card import build_ranking_result_card, build_search_result_card


class SearchCardTest(unittest.TestCase):
    def test_build_search_result_card_merges_covers_and_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            covers = []
            for index, color in enumerate([(220, 40, 40), (40, 120, 220)], start=1):
                cover_path = root / f"cover-{index}.jpg"
                Image.new("RGB", (120, 180), color).save(cover_path)
                covers.append(cover_path)

            result = SearchResult(
                keyword="author-name",
                kind="author",
                page=1,
                page_count=2,
                total=6,
                items=(
                    SearchItem("111111", "Title One", ("tag-a", "tag-b")),
                    SearchItem("222222", "Title Two", ()),
                ),
            )
            output = root / "card.png"

            built = build_search_result_card(result, covers, output, command_prefix="/jm")

            self.assertEqual(built, output)
            self.assertTrue(output.is_file())
            with Image.open(output) as image:
                self.assertEqual(image.format, "PNG")
                self.assertGreaterEqual(image.width, 900)
                self.assertGreaterEqual(image.height, 420)
                red, green, blue = image.getpixel((60, 120))[:3]
                self.assertGreater(red, 180)
                self.assertLess(green, 90)
                self.assertLess(blue, 90)

    def test_build_ranking_result_card_uses_shared_layout_and_global_rank(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            covers = []
            for index in range(2):
                cover_path = root / f"ranking-{index}.jpg"
                Image.new("RGB", (120, 180), (40 + index * 80, 100, 180)).save(cover_path)
                covers.append(cover_path)

            query = RankingQuery(keyword="全彩", period="week", category="hanman", page=2)
            result = RankingResult(
                query,
                2,
                3,
                12,
                (
                    SearchItem("111111", "Rank One", ("tag-a",)),
                    SearchItem("222222", "Rank Two", ()),
                ),
            )
            output = root / "ranking.png"

            built = build_ranking_result_card(
                result,
                covers,
                output,
                title="韩漫 · 周榜 · 关键词：全彩",
                command_prefix="/jm",
            )

            self.assertEqual(built, output)
            with Image.open(output) as image:
                self.assertEqual(image.format, "PNG")
                self.assertGreaterEqual(image.width, 900)
                self.assertGreaterEqual(image.height, 420)


if __name__ == "__main__":
    unittest.main()
