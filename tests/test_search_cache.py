import unittest

from jmbot.core.catalog_service import AlbumDetail, AlbumSummary, SearchResultPage
from jmbot.core.search_cache import (
    DetailCache,
    SearchCache,
    SearchCacheMissError,
    SearchIndexError,
    SearchScope,
)


class FakeClock:
    def __init__(self):
        self.value = 100.0

    def __call__(self):
        return self.value


class SearchCacheTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.cache = SearchCache(ttl_seconds=10, max_entries=2, clock=self.clock)
        self.group_scope = SearchScope("group", user_id=42, group_id=10001)
        self.other_group_scope = SearchScope("group", user_id=42, group_id=10002)
        self.result = SearchResultPage(
            keyword="关键词",
            page=1,
            total=2,
            items=(
                AlbumSummary("111111", "结果一"),
                AlbumSummary("222222", "结果二"),
            ),
        )

    def test_search_results_are_isolated_by_context(self):
        self.cache.put(self.group_scope, self.result)

        self.assertEqual(self.cache.resolve_album_id(self.group_scope, 2), "222222")
        with self.assertRaises(SearchCacheMissError):
            self.cache.resolve_album_id(self.other_group_scope, 1)

    def test_search_result_expires(self):
        self.cache.put(self.group_scope, self.result)
        self.clock.value += 11

        with self.assertRaises(SearchCacheMissError):
            self.cache.resolve_album_id(self.group_scope, 1)

    def test_search_index_must_be_in_displayed_results(self):
        self.cache.put(self.group_scope, self.result)

        with self.assertRaises(SearchIndexError):
            self.cache.resolve_album_id(self.group_scope, 3)

    def test_search_cache_evicts_oldest_context(self):
        private_scope = SearchScope("private", user_id=42)
        third_scope = SearchScope("private", user_id=43)
        self.cache.put(self.group_scope, self.result)
        self.clock.value += 1
        self.cache.put(private_scope, self.result)
        self.clock.value += 1
        self.cache.put(third_scope, self.result)

        with self.assertRaises(SearchCacheMissError):
            self.cache.resolve_album_id(self.group_scope, 1)


class DetailCacheTest(unittest.TestCase):
    def test_detail_cache_returns_value_until_expired(self):
        clock = FakeClock()
        cache = DetailCache(ttl_seconds=10, max_entries=2, clock=clock)
        detail = AlbumDetail("123456", "标题", (), None, (), (), None)
        cache.put(detail)

        self.assertEqual(cache.get("123456"), detail)
        clock.value += 11
        self.assertIsNone(cache.get("123456"))


if __name__ == "__main__":
    unittest.main()
