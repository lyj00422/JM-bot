import unittest
from types import SimpleNamespace

from jmbot.core.catalog_service import get_album_detail, search_albums


class FakeClient:
    def __init__(self):
        self.detail_calls = []

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
        self.search_call = ("site", keyword, page)
        return SimpleNamespace(
            content=[
                ("111111", {"name": "普通搜索结果", "tags": ["中文", "NTR"]}),
                ("222222", {"name": "无标签结果"}),
            ],
            total=22,
            page_count=3,
        )

    def search_author(self, keyword, page=1):
        self.search_call = ("author", keyword, page)
        return SimpleNamespace(
            content=[
                ("333333", {"name": "作者作品", "tags": ["全彩"]}),
            ],
            total=1,
            page_count=1,
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

    def test_get_album_detail_treats_zero_metadata_as_unknown(self):
        client = SimpleNamespace(
            get_album_detail=lambda album_id: SimpleNamespace(
                album_id=album_id,
                name="标题",
                page_count=0,
                update_date="0",
            )
        )

        detail = get_album_detail("123456", client=client)

        self.assertIsNone(detail.page_count)
        self.assertIsNone(detail.update_time)

    def test_search_albums_uses_site_search_and_normalizes_items(self):
        client = FakeClient()

        result = search_albums("测试", page=2, kind="site", client=client)

        self.assertEqual(client.search_call, ("site", "测试", 2))
        self.assertEqual(result.keyword, "测试")
        self.assertEqual(result.kind, "site")
        self.assertEqual(result.page, 2)
        self.assertEqual(result.total, 22)
        self.assertEqual(result.page_count, 3)
        self.assertEqual(result.items[0].album_id, "111111")
        self.assertEqual(result.items[0].title, "普通搜索结果")
        self.assertEqual(result.items[0].tags, ("中文", "NTR"))
        self.assertEqual(result.items[1].tags, ())

    def test_search_albums_uses_author_search(self):
        client = FakeClient()

        result = search_albums("柊ぽぷら", page=1, kind="author", client=client)

        self.assertEqual(client.search_call, ("author", "柊ぽぷら", 1))
        self.assertEqual(result.kind, "author")
        self.assertEqual(result.items[0].title, "作者作品")

    def test_search_albums_rejects_invalid_page_and_kind(self):
        with self.assertRaises(ValueError):
            search_albums("测试", page=0, client=FakeClient())

        with self.assertRaises(ValueError):
            search_albums("测试", kind="tag", client=FakeClient())


if __name__ == "__main__":
    unittest.main()
