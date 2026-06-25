import unittest
from types import SimpleNamespace

from jmbot.core.catalog_service import get_album_detail, search_albums


class FakeClient:
    def __init__(self):
        self.detail_calls = []
        self.search_calls = []

    def get_album_detail(self, album_id):
        self.detail_calls.append(album_id)
        return SimpleNamespace(
            album_id=album_id,
            name="测试漫画",
            authors=["作者甲", "作者乙"],
            page_count=123,
            tags=["标签一", "标签二"],
            episode_list=[
                ("1001", "1", "第一话", "2026-01-01"),
                ("1002", "2", "第二话"),
            ],
            update_date="2026-06-25",
        )

    def search_site(self, keyword, page=1):
        self.search_calls.append((keyword, page))
        return SimpleNamespace(
            total=8,
            content=[
                ("111111", {"name": "结果一", "tags": ["A", "B"]}),
                ("222222", {"name": "结果二"}),
                ("333333", {"name": "结果三", "tags": []}),
                ("444444", {"name": "结果四", "tags": ["D"]}),
                ("555555", {"name": "结果五", "tags": ["E"]}),
                ("666666", {"name": "不应展示", "tags": ["F"]}),
            ],
        )


class CatalogServiceTest(unittest.TestCase):
    def test_get_album_detail_converts_jmcomic_entity(self):
        client = FakeClient()

        detail = get_album_detail("123456", client=client)

        self.assertEqual(client.detail_calls, ["123456"])
        self.assertEqual(detail.album_id, "123456")
        self.assertEqual(detail.title, "测试漫画")
        self.assertEqual(detail.authors, ("作者甲", "作者乙"))
        self.assertEqual(detail.page_count, 123)
        self.assertEqual(detail.tags, ("标签一", "标签二"))
        self.assertEqual(detail.chapters, ("第一话", "第二话"))
        self.assertEqual(detail.update_time, "2026-06-25")

    def test_get_album_detail_tolerates_missing_optional_fields(self):
        client = SimpleNamespace(
            get_album_detail=lambda album_id: SimpleNamespace(album_id=album_id, name="")
        )

        detail = get_album_detail("123456", client=client)

        self.assertEqual(detail.title, "未知")
        self.assertEqual(detail.authors, ())
        self.assertIsNone(detail.page_count)
        self.assertEqual(detail.tags, ())
        self.assertEqual(detail.chapters, ())
        self.assertIsNone(detail.update_time)

    def test_search_albums_returns_only_first_five_items(self):
        client = FakeClient()

        result = search_albums("关键词", page=2, client=client)

        self.assertEqual(client.search_calls, [("关键词", 2)])
        self.assertEqual(result.keyword, "关键词")
        self.assertEqual(result.page, 2)
        self.assertEqual(result.total, 8)
        self.assertEqual(len(result.items), 5)
        self.assertEqual(result.items[0].album_id, "111111")
        self.assertEqual(result.items[1].tags, ())
        self.assertNotIn("666666", [item.album_id for item in result.items])


if __name__ == "__main__":
    unittest.main()
