import unittest

from core.parser import parse_jm_command


class ParseJmCommandTest(unittest.TestCase):
    def test_parse_valid_jm_command(self):
        result = parse_jm_command("/jm 123456")

        self.assertIsNotNone(result)
        self.assertEqual(result.album_id, "123456")

    def test_parse_allows_extra_spaces(self):
        result = parse_jm_command("  /jm   350234  ")

        self.assertIsNotNone(result)
        self.assertEqual(result.album_id, "350234")

    def test_parse_uses_custom_prefix(self):
        result = parse_jm_command("!jm 123456", command_prefix="!jm")

        self.assertIsNotNone(result)
        self.assertEqual(result.album_id, "123456")

    def test_parse_rejects_invalid_id(self):
        self.assertIsNone(parse_jm_command("/jm abc"))

    def test_parse_rejects_other_command(self):
        self.assertIsNone(parse_jm_command("/help 123456"))


if __name__ == "__main__":
    unittest.main()
