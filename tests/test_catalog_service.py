import unittest
from types import SimpleNamespace

from jmbot.core.catalog_service import RankingQuery, get_album_detail, get_ranking_page, search_albums


class FakeClient:
    def __init__(self):
        self.detail_calls = []
        self.ranking_calls = []

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

    def search_site(self, keyword, page=1, **kwargs):
        if kwargs:
            self.ranking_calls.append(("search", keyword, page, kwargs))
            return self._ranking_page(page, "搜索排行")
        self.search_call = ("site", keyword, page)
        return SimpleNamespace(
            content=[
                ("111111", {"name": "普通搜索结果", "tags": ["中文", "NTR"]}),
                ("222222", {"name": "无标签结果"}),
            ],
            total=22,
            page_count=3,
        )

    def categories_filter(self, *, page, time, category, order_by):
        self.ranking_calls.append(("category", page, time, category, order_by))
        return self._ranking_page(page, "排行")

    def search_author(self, keyword, page=1):
        self.search_call = ("author", keyword, page)
        return SimpleNamespace(
            content=[
                ("333333", {"name": "作者作品", "tags": ["全彩"]}),
            ],
            total=1,
            page_count=1,
        )

    @staticmethod
    def _ranking_page(page, title_prefix):
        start = (page - 1) * 4 + 1
        return SimpleNamespace(
            content=[
                (f"{index:06d}", {"name": f"{title_prefix} {index}", "tags": ["热门"]})
                for index in range(start, start + 4)
            ],
            total=12,
            page_size=4,
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

    def test_ranking_selects_category_endpoint_and_repages_to_five_items(self):
        client = FakeClient()

        result = get_ranking_page(
            RankingQuery(period="week", category="hanman", page=2),
            client=client,
        )

        self.assertEqual([item.album_id for item in result.items], [
            "000006", "000007", "000008", "000009", "000010",
        ])
        self.assertEqual((result.page, result.page_count, result.total), (2, 3, 12))
        self.assertEqual(client.ranking_calls, [
            ("category", 1, "w", "hanman", "mv"),
            ("category", 2, "w", "hanman", "mv"),
            ("category", 3, "w", "hanman", "mv"),
        ])

    def test_keyword_ranking_uses_ranked_site_search(self):
        client = FakeClient()

        result = get_ranking_page(
            RankingQuery(keyword="全彩", period="month", category="doujin"),
            client=client,
        )

        self.assertEqual(len(result.items), 5)
        self.assertEqual(client.ranking_calls[0], (
            "search",
            "全彩",
            1,
            {"order_by": "mv", "time": "m", "category": "doujin"},
        ))

    def test_ranking_out_of_range_does_not_fetch_nonexistent_source_pages(self):
        client = FakeClient()

        result = get_ranking_page(RankingQuery(page=4), client=client)

        self.assertEqual(result.items, ())
        self.assertEqual(result.page_count, 3)
        self.assertEqual(client.ranking_calls, [("category", 1, "a", "0", "mv")])

    def test_ranking_rejects_invalid_domain_values(self):
        for query in [
            RankingQuery(period="year"),
            RankingQuery(category="bad"),
            RankingQuery(page=0),
        ]:
            with self.subTest(query=query), self.assertRaises(ValueError):
                get_ranking_page(query, client=FakeClient())


if __name__ == "__main__":
    unittest.main()
