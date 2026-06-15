from __future__ import annotations

import unittest

from comfy_exif_viewer import _is_allowed_drop_url, _suffix_from_url_or_content_type


class DropUrlTests(unittest.TestCase):
    def test_allows_ac_o_namu_la_https_urls(self) -> None:
        self.assertTrue(
            _is_allowed_drop_url(
                "https://ac-o.namu.la/20260615sac/sample.png?type=orig"
            )
        )

    def test_blocks_other_hosts(self) -> None:
        self.assertFalse(_is_allowed_drop_url("https://example.com/sample.png"))
        self.assertFalse(_is_allowed_drop_url("https://namu.la/sample.png"))

    def test_blocks_non_https_urls(self) -> None:
        self.assertFalse(_is_allowed_drop_url("http://ac-o.namu.la/sample.png"))

    def test_uses_content_type_when_url_has_no_suffix(self) -> None:
        self.assertEqual(
            _suffix_from_url_or_content_type(
                "https://ac-o.namu.la/20260615sac/image?type=orig",
                "image/webp",
            ),
            ".webp",
        )


if __name__ == "__main__":
    unittest.main()
