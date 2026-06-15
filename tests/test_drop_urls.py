from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from comfy_exif_viewer import (
    MAX_RECENT_IMAGES,
    _app_cache_dir,
    _clear_app_cache_files,
    _format_bytes,
    _is_allowed_drop_url,
    _is_supported_drop_item,
    _load_recent_paths,
    _save_recent_paths,
    _suffix_from_url_or_content_type,
)


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

    def test_supports_local_files_and_allowed_urls_for_drop_queue(self) -> None:
        self.assertTrue(_is_supported_drop_item(r"D:\images\sample.png"))
        self.assertTrue(_is_supported_drop_item(r"D:\images\sample.webp"))
        self.assertTrue(
            _is_supported_drop_item(
                "https://ac-o.namu.la/20260615sac/sample.png?type=orig"
            )
        )
        self.assertFalse(_is_supported_drop_item(r"D:\images\sample.txt"))
        self.assertFalse(_is_supported_drop_item("https://example.com/sample.png"))

    def test_uses_content_type_when_url_has_no_suffix(self) -> None:
        self.assertEqual(
            _suffix_from_url_or_content_type(
                "https://ac-o.namu.la/20260615sac/image?type=orig",
                "image/webp",
            ),
            ".webp",
        )

    def test_formats_download_byte_counts(self) -> None:
        self.assertEqual(_format_bytes(100), "100 B")
        self.assertEqual(_format_bytes(1536), "1.5 KB")
        self.assertEqual(_format_bytes(2 * 1024 * 1024), "2.0 MB")

    def test_persists_existing_recent_paths_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with _temporary_local_app_data(temp_dir):
                existing = Path(temp_dir) / "image.png"
                existing.write_bytes(b"png")
                missing = Path(temp_dir) / "missing.png"

                _save_recent_paths([existing, missing])

                self.assertEqual(_load_recent_paths(), [existing])

    def test_recent_cache_is_capped(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with _temporary_local_app_data(temp_dir):
                paths = []
                for index in range(MAX_RECENT_IMAGES + 2):
                    path = Path(temp_dir) / f"image-{index}.png"
                    path.write_bytes(b"png")
                    paths.append(path)

                _save_recent_paths(paths)

                self.assertEqual(len(_load_recent_paths()), MAX_RECENT_IMAGES)

    def test_clear_app_cache_deletes_only_app_cache_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with _temporary_local_app_data(temp_dir):
                cache_dir = _app_cache_dir()
                cache_dir.mkdir(parents=True)
                cached = cache_dir / "chrome-drop-test.png"
                clipboard = cache_dir / "clipboard-image.png"
                unrelated = cache_dir / "user-image.png"
                cached.write_bytes(b"png")
                clipboard.write_bytes(b"png")
                unrelated.write_bytes(b"png")

                deleted_count = _clear_app_cache_files(include_legacy=False)

                self.assertEqual(deleted_count, 2)
                self.assertFalse(cached.exists())
                self.assertFalse(clipboard.exists())
                self.assertTrue(unrelated.exists())


class _temporary_local_app_data:
    def __init__(self, path: str) -> None:
        self.path = path
        self.previous_local = os.environ.get("LOCALAPPDATA")
        self.previous_app = os.environ.get("APPDATA")

    def __enter__(self) -> None:
        os.environ["LOCALAPPDATA"] = self.path
        os.environ.pop("APPDATA", None)

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        if self.previous_local is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = self.previous_local
        if self.previous_app is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = self.previous_app


if __name__ == "__main__":
    unittest.main()
