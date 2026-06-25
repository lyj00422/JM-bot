import unittest
from types import SimpleNamespace

from jmbot.core.catalog_service import get_album_detail


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


if __name__ == "__main__":
    unittest.main()
