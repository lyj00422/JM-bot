import unittest

from jmbot.core.catalog_service import AlbumDetail
from jmbot.core.detail_cache import DetailCache


class FakeClock:
    def __init__(self):
        self.value = 100.0

    def __call__(self):
        return self.value


class DetailCacheTest(unittest.TestCase):
    def test_detail_cache_returns_value_until_expired(self):
        clock = FakeClock()
        cache = DetailCache(ttl_seconds=10, max_entries=2, clock=clock)
        detail = AlbumDetail("123456", "标题", (), None, (), (), None)
        cache.put(detail)

        self.assertEqual(cache.get("123456"), detail)
        clock.value += 11
        self.assertIsNone(cache.get("123456"))

    def test_detail_cache_evicts_oldest_entry(self):
        clock = FakeClock()
        cache = DetailCache(ttl_seconds=10, max_entries=1, clock=clock)
        cache.put(AlbumDetail("111111", "一", (), None, (), (), None))
        cache.put(AlbumDetail("222222", "二", (), None, (), (), None))

        self.assertIsNone(cache.get("111111"))
        self.assertIsNotNone(cache.get("222222"))


if __name__ == "__main__":
    unittest.main()
