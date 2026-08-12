from __future__ import annotations

import json
import os
import queue
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from comfy_metadata_reader import MetadataEntry
from comfy_exif_viewer import (
    _AllowedDropRedirectHandler,
    _CivitaiImageInfo,
    _DownloadedImage,
    DEFAULT_ALLOWED_DROP_URL_HOSTS,
    MAX_RECENT_IMAGES,
    MetadataViewer,
    _app_cache_dir,
    _clear_app_cache_files,
    _format_bytes,
    _is_app_cache_path,
    _is_allowed_drop_url,
    _is_supported_drop_item,
    _load_allowed_url_hosts,
    _load_recent_paths,
    _civitai_original_asset_url,
    _parse_civitai_image_page,
    _parse_allowed_url_hosts,
    _resolve_civitai_image_page,
    _save_allowed_url_hosts,
    _save_recent_paths,
    _suffix_from_url_or_content_type,
)


class _FakeResponse:
    def __init__(
        self,
        final_url: str,
        payload: bytes,
        content_type: str = "image/png",
    ) -> None:
        self.final_url = final_url
        self.payload = payload
        self.offset = 0
        self.headers = {
            "Content-Type": content_type,
            "Content-Length": str(len(payload)),
        }

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        return None

    def geturl(self) -> str:
        return self.final_url

    def read(self, size: int) -> bytes:
        chunk = self.payload[self.offset : self.offset + size]
        self.offset += len(chunk)
        return chunk


class _DownloadOwner:
    def _queue_download_progress(
        self,
        _received: int,
        _total: int | None,
        _generation: int | None = None,
    ) -> None:
        return None


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

    def test_allows_civitai_page_and_cdn_hosts_by_default(self) -> None:
        self.assertTrue(
            _is_allowed_drop_url("https://civitai.red/images/135875665")
        )
        self.assertTrue(
            _is_allowed_drop_url("https://civitai.com/images/135875665")
        )
        self.assertTrue(
            _is_allowed_drop_url(
                "https://image.civitai.com/path/original=true/135875665.jpeg"
            )
        )
        self.assertTrue(
            _is_allowed_drop_url(
                "https://image-b2.civitai.com/file/civitai-media-cache/id/original"
            )
        )

    def test_allows_configured_exact_host(self) -> None:
        allowed_hosts = frozenset({"images.example.com"})

        self.assertTrue(
            _is_allowed_drop_url(
                "https://images.example.com/sample.png", allowed_hosts
            )
        )
        self.assertFalse(
            _is_allowed_drop_url(
                "https://ac-o.namu.la/sample.png", allowed_hosts
            )
        )

    def test_wildcard_host_allows_subdomains_but_not_parent(self) -> None:
        allowed_hosts = frozenset({"*.example.com"})

        self.assertTrue(
            _is_allowed_drop_url("https://cdn.example.com/sample.png", allowed_hosts)
        )
        self.assertTrue(
            _is_allowed_drop_url("https://a.b.example.com/sample.png", allowed_hosts)
        )
        self.assertFalse(
            _is_allowed_drop_url("https://example.com/sample.png", allowed_hosts)
        )

    def test_empty_configured_host_list_disables_url_downloads(self) -> None:
        self.assertFalse(
            _is_allowed_drop_url(
                "https://ac-o.namu.la/sample.png", frozenset()
            )
        )

    def test_queue_continues_when_settings_no_longer_allow_queued_url(self) -> None:
        owner = MetadataViewer.__new__(MetadataViewer)
        owner.allowed_url_hosts = frozenset()
        owner.processing_drop_queue = True
        owner.after = mock.Mock()
        owner._process_next_drop_queue_item = mock.Mock()

        with mock.patch("comfy_exif_viewer.messagebox.showerror"):
            MetadataViewer.open_url(
                owner,
                "https://ac-o.namu.la/sample.png",
                from_queue=True,
            )

        owner.after.assert_called_once_with(0, owner._process_next_drop_queue_item)

    def test_parses_and_normalizes_allowed_hosts(self) -> None:
        hosts = _parse_allowed_url_hosts(
            "HTTPS://Images.Example.com/gallery/image.png\n*.CDN.Example.com\n"
        )

        self.assertEqual(
            hosts,
            frozenset({"images.example.com", "*.cdn.example.com"}),
        )

    def test_rejects_invalid_allowed_host(self) -> None:
        with self.assertRaisesRegex(ValueError, "Invalid hostname"):
            _parse_allowed_url_hosts("example.com/path")

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

    def test_supports_only_civitai_image_pages_and_direct_cdn_images(self) -> None:
        self.assertTrue(
            _is_supported_drop_item("https://civitai.red/images/135875665")
        )
        self.assertTrue(
            _is_supported_drop_item("https://civitai.com/images/135875665/")
        )
        self.assertTrue(
            _is_supported_drop_item(
                "https://image.civitai.com/path/anim=false,width=450/image.jpeg"
            )
        )
        self.assertFalse(
            _is_supported_drop_item("https://civitai.com/models/123/example")
        )

    def test_civitai_cdn_variant_is_rewritten_to_original_asset(self) -> None:
        url = (
            "https://image.civitai.com/token/"
            "dc7a1cd5-d51b-45be-9bcc-6998c2165d35/"
            "anim=false,width=450,optimized=true/image.jpeg"
        )

        self.assertEqual(
            _civitai_original_asset_url(url),
            "https://image.civitai.com/token/"
            "dc7a1cd5-d51b-45be-9bcc-6998c2165d35/"
            "original=true/image.jpeg",
        )

    def test_direct_civitai_download_requests_original_asset(self) -> None:
        url = (
            "https://image.civitai.com/token/"
            "dc7a1cd5-d51b-45be-9bcc-6998c2165d35/"
            "anim=false,width=450,optimized=true/image.jpeg"
        )
        original_url = _civitai_original_asset_url(url)
        response = _FakeResponse(original_url, b"original-image")
        with tempfile.TemporaryDirectory() as temp_dir:
            with _temporary_local_app_data(temp_dir):
                with mock.patch(
                    "comfy_exif_viewer._open_allowed_drop_url",
                    return_value=response,
                ) as open_url:
                    downloaded = MetadataViewer._download_image_url(
                        _DownloadOwner(), url
                    )

                downloaded_bytes = downloaded.path.read_bytes()

        request = open_url.call_args.args[0]
        self.assertEqual(request.full_url, original_url)
        self.assertEqual(downloaded_bytes, b"original-image")

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

    def test_rejects_redirect_outside_allowed_host(self) -> None:
        response = _FakeResponse("https://example.com/image.png", b"png")
        with tempfile.TemporaryDirectory() as temp_dir:
            with _temporary_local_app_data(temp_dir):
                with mock.patch(
                    "comfy_exif_viewer._open_allowed_drop_url",
                    return_value=response,
                ):
                    with self.assertRaisesRegex(ValueError, "redirected outside"):
                        MetadataViewer._download_image_url(
                            _DownloadOwner(),
                            "https://ac-o.namu.la/image.png",
                        )

    def test_streams_allowed_download_to_app_cache(self) -> None:
        payload = b"png-payload"
        response = _FakeResponse("https://ac-o.namu.la/image.png", payload)
        with tempfile.TemporaryDirectory() as temp_dir:
            with _temporary_local_app_data(temp_dir):
                with mock.patch(
                    "comfy_exif_viewer._open_allowed_drop_url",
                    return_value=response,
                ):
                    downloaded = MetadataViewer._download_image_url(
                        _DownloadOwner(),
                        "https://ac-o.namu.la/image.png",
                    )

                self.assertEqual(downloaded.path.read_bytes(), payload)
                self.assertFalse(
                    downloaded.path.with_name(downloaded.path.name + ".part").exists()
                )

    def test_streams_download_from_configured_custom_host(self) -> None:
        payload = b"custom-host-image"
        url = "https://images.example.com/image.png"
        response = _FakeResponse(url, payload)
        allowed_hosts = frozenset({"images.example.com"})
        with tempfile.TemporaryDirectory() as temp_dir:
            with _temporary_local_app_data(temp_dir):
                with mock.patch(
                    "comfy_exif_viewer._open_allowed_drop_url",
                    return_value=response,
                ) as open_url:
                    downloaded = MetadataViewer._download_image_url(
                        _DownloadOwner(),
                        url,
                        allowed_hosts=allowed_hosts,
                    )

                self.assertEqual(downloaded.path.read_bytes(), payload)
                self.assertEqual(open_url.call_args.args[1], allowed_hosts)

    def test_resolves_civitai_page_before_downloading_image(self) -> None:
        page_url = "https://civitai.red/images/135875665"
        image_url = "https://image.civitai.com/path/original=true/135875665.jpeg"
        entries = ()
        response = _FakeResponse(image_url, b"jpeg-image")
        with tempfile.TemporaryDirectory() as temp_dir:
            with _temporary_local_app_data(temp_dir):
                with mock.patch(
                    "comfy_exif_viewer._resolve_civitai_image_page",
                    return_value=_CivitaiImageInfo(image_url, entries),
                ) as resolve_page:
                    with mock.patch(
                        "comfy_exif_viewer._open_allowed_drop_url",
                        return_value=response,
                    ):
                        downloaded = MetadataViewer._download_image_url(
                            _DownloadOwner(), page_url
                        )

                downloaded_bytes = downloaded.path.read_bytes()

        resolve_page.assert_called_once_with(
            page_url, frozenset(DEFAULT_ALLOWED_DROP_URL_HOSTS)
        )
        self.assertEqual(downloaded_bytes, b"jpeg-image")

    def test_numeric_cdn_link_uses_matching_page_metadata(self) -> None:
        asset_id = "847bf52a-1fc1-406c-8f67-749302a7f268"
        direct_url = (
            f"https://image.civitai.com/root/{asset_id}/original/135875665.jpeg"
        )
        page_url = f"https://image.civitai.com/root/{asset_id}/original/image.jpeg"
        entries = (MetadataEntry("Civitai", "prompt", "cat"),)
        response = _FakeResponse(direct_url, b"image")
        with tempfile.TemporaryDirectory() as temp_dir:
            with _temporary_local_app_data(temp_dir):
                with mock.patch(
                    "comfy_exif_viewer._resolve_civitai_image_page",
                    return_value=_CivitaiImageInfo(page_url, entries),
                ):
                    with mock.patch(
                        "comfy_exif_viewer._open_allowed_drop_url",
                        return_value=response,
                    ):
                        downloaded = MetadataViewer._download_image_url(
                            _DownloadOwner(), direct_url
                        )

        self.assertEqual(downloaded.supplemental_entries, entries)

    def test_numeric_cdn_link_ignores_metadata_for_different_asset(self) -> None:
        direct_url = (
            "https://image.civitai.com/root/"
            "847bf52a-1fc1-406c-8f67-749302a7f268/original/135875665.jpeg"
        )
        other_url = (
            "https://image.civitai.com/root/"
            "dc7a1cd5-d51b-45be-9bcc-6998c2165d35/original/image.jpeg"
        )
        entries = (MetadataEntry("Civitai", "prompt", "wrong image"),)
        response = _FakeResponse(direct_url, b"image")
        with tempfile.TemporaryDirectory() as temp_dir:
            with _temporary_local_app_data(temp_dir):
                with mock.patch(
                    "comfy_exif_viewer._resolve_civitai_image_page",
                    return_value=_CivitaiImageInfo(other_url, entries),
                ):
                    with mock.patch(
                        "comfy_exif_viewer._open_allowed_drop_url",
                        return_value=response,
                    ):
                        downloaded = MetadataViewer._download_image_url(
                            _DownloadOwner(), direct_url
                        )

        self.assertEqual(downloaded.supplemental_entries, ())

    def test_parses_civitai_original_url_and_generation_metadata(self) -> None:
        source = """
        <html><head>
        <script type="application/ld+json">
        {"@type":"ImageObject","contentUrl":"https://image.civitai.com/path/original=true/135875665.jpeg"}
        </script></head><body>
        <script id="__NEXT_DATA__" type="application/json">
        {"props":{"pageProps":{"trpcState":{"json":{"queries":[{
          "queryKey":[["image","get"],{"input":{"id":135875665}}],
          "state":{"data":{"type":"image","meta":{
            "prompt":"cat","negativePrompt":"dog","steps":30,
            "sampler":"Euler","cfgScale":5,"seed":123,
            "Model":"Anima","width":1024,"height":1536
          }}}
        }]}}}}}
        </script></body></html>
        """

        info = _parse_civitai_image_page(source, 135875665)

        self.assertEqual(
            info.image_url,
            "https://image.civitai.com/path/original=true/135875665.jpeg",
        )
        values = {(entry.key, entry.value) for entry in info.metadata_entries}
        self.assertIn(("prompt", "cat"), values)
        self.assertIn(("negative_prompt", "dog"), values)
        self.assertTrue(
            any(key == "settings" and "Steps: 30" in value for key, value in values)
        )

    def test_red_civitai_page_is_resolved_through_matching_com_page(self) -> None:
        source = b"""
        <script type="application/ld+json">
        {"@type":"ImageObject","contentUrl":"https://image.civitai.com/image.jpeg"}
        </script>
        """
        response = _FakeResponse(
            "https://civitai.com/images/135875665",
            source,
            "text/html; charset=utf-8",
        )
        allowed_hosts = frozenset(DEFAULT_ALLOWED_DROP_URL_HOSTS)
        with mock.patch(
            "comfy_exif_viewer._open_allowed_drop_url", return_value=response
        ) as open_url:
            info = _resolve_civitai_image_page(
                "https://civitai.red/images/135875665", allowed_hosts
            )

        request = open_url.call_args.args[0]
        self.assertEqual(
            request.full_url, "https://civitai.com/images/135875665"
        )
        self.assertEqual(info.image_url, "https://image.civitai.com/image.jpeg")

    def test_rejects_civitai_page_with_untrusted_image_asset(self) -> None:
        source = """
        <script type="application/ld+json">
        {"@type":"ImageObject","contentUrl":"https://example.com/image.jpeg"}
        </script>
        """

        with self.assertRaisesRegex(ValueError, "original image URL"):
            _parse_civitai_image_page(source, 135875665)

    def test_rejects_each_redirect_hop_outside_allowed_host(self) -> None:
        handler = _AllowedDropRedirectHandler()

        with self.assertRaisesRegex(ValueError, "redirected outside"):
            handler.redirect_request(
                mock.Mock(),
                mock.Mock(),
                302,
                "Found",
                {},
                "https://example.com/intermediate.png",
            )

    def test_persists_existing_recent_paths_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with _temporary_local_app_data(temp_dir):
                existing = Path(temp_dir) / "image.png"
                existing.write_bytes(b"png")
                missing = Path(temp_dir) / "missing.png"

                _save_recent_paths([existing, missing])

                self.assertEqual(_load_recent_paths(), [existing])

    def test_persists_allowed_image_hosts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with _temporary_local_app_data(temp_dir):
                expected = frozenset({"ac-o.namu.la", "*.example.com"})

                _save_allowed_url_hosts(expected)

                self.assertEqual(_load_allowed_url_hosts(), expected)

    def test_missing_or_invalid_settings_keep_default_sites(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with _temporary_local_app_data(temp_dir):
                self.assertEqual(
                    _load_allowed_url_hosts(), frozenset(DEFAULT_ALLOWED_DROP_URL_HOSTS)
                )
                settings_path = Path(temp_dir) / "ComfyUI-EXIF-viewer" / "settings.json"
                settings_path.parent.mkdir(parents=True)
                settings_path.write_text("[]", encoding="utf-8")

                self.assertEqual(
                    _load_allowed_url_hosts(), frozenset(DEFAULT_ALLOWED_DROP_URL_HOSTS)
                )
                settings_path.write_text("not-json", encoding="utf-8")

                self.assertEqual(
                    _load_allowed_url_hosts(), frozenset(DEFAULT_ALLOWED_DROP_URL_HOSTS)
                )

    def test_migrates_legacy_arcalive_default_to_new_default_sites(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with _temporary_local_app_data(temp_dir):
                settings_path = Path(temp_dir) / "ComfyUI-EXIF-viewer" / "settings.json"
                settings_path.parent.mkdir(parents=True)
                settings_path.write_text(
                    json.dumps({"allowed_image_hosts": ["ac-o.namu.la"]}),
                    encoding="utf-8",
                )

                self.assertEqual(
                    _load_allowed_url_hosts(), frozenset(DEFAULT_ALLOWED_DROP_URL_HOSTS)
                )

    def test_preserves_legacy_empty_or_custom_allowed_sites(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with _temporary_local_app_data(temp_dir):
                settings_path = Path(temp_dir) / "ComfyUI-EXIF-viewer" / "settings.json"
                settings_path.parent.mkdir(parents=True)
                settings_path.write_text(
                    json.dumps({"allowed_image_hosts": []}), encoding="utf-8"
                )
                self.assertEqual(_load_allowed_url_hosts(), frozenset())

                settings_path.write_text(
                    json.dumps({"allowed_image_hosts": ["images.example.com"]}),
                    encoding="utf-8",
                )
                self.assertEqual(
                    _load_allowed_url_hosts(), frozenset({"images.example.com"})
                )

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
                _save_allowed_url_hosts(frozenset({"images.example.com"}))

                deleted_count = _clear_app_cache_files(include_legacy=False)

                self.assertEqual(deleted_count, 2)
                self.assertFalse(cached.exists())
                self.assertFalse(clipboard.exists())
                self.assertTrue(unrelated.exists())
                self.assertEqual(
                    _load_allowed_url_hosts(), frozenset({"images.example.com"})
                )

    def test_identifies_only_files_in_managed_cache_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with _temporary_local_app_data(temp_dir):
                cached = _app_cache_dir() / "chrome-drop-test.png"
                unrelated = Path(temp_dir) / "image.png"

                self.assertTrue(_is_app_cache_path(cached))
                self.assertFalse(_is_app_cache_path(unrelated))

    def test_recent_history_write_failure_does_not_abort_open_flow(self) -> None:
        owner = MetadataViewer.__new__(MetadataViewer)
        owner.recent_paths = []
        owner.status_var = mock.Mock()
        owner._refresh_recent_images = mock.Mock()
        image_path = Path("image.png")

        with mock.patch(
            "comfy_exif_viewer._save_recent_paths",
            side_effect=OSError("read-only app data"),
        ):
            MetadataViewer._add_recent_path(owner, image_path)

        self.assertEqual(owner.recent_paths, [image_path])
        owner._refresh_recent_images.assert_called_once_with()
        owner.status_var.set.assert_called_once()

    def test_stale_download_completion_does_not_replace_current_image(self) -> None:
        owner = MetadataViewer.__new__(MetadataViewer)
        owner.download_thread = mock.Mock()
        owner.drop_frame = mock.Mock()
        owner.drop_label = mock.Mock()
        owner._hide_download_progress = mock.Mock()
        owner.navigation_generation = 2
        owner.drop_queue = []
        owner.processing_drop_queue = True
        owner.open_path = mock.Mock()
        owner.after = mock.Mock()

        MetadataViewer._finish_url_download(owner, Path("stale.png"), "", 1)

        owner.open_path.assert_not_called()
        owner.after.assert_not_called()
        self.assertFalse(owner.processing_drop_queue)
        self.assertIsNone(owner.download_thread)

    def test_download_completion_passes_civitai_metadata_to_open_path(self) -> None:
        owner = MetadataViewer.__new__(MetadataViewer)
        owner.download_thread = mock.Mock()
        owner.drop_frame = mock.Mock()
        owner.drop_label = mock.Mock()
        owner._hide_download_progress = mock.Mock()
        owner.navigation_generation = 3
        owner.drop_queue = []
        owner.processing_drop_queue = False
        owner.open_path = mock.Mock()
        entries = (MetadataEntry("Civitai", "prompt", "cat"),)
        downloaded = _DownloadedImage(Path("civitai.jpeg"), entries)

        MetadataViewer._finish_url_download(owner, downloaded, "", 3)

        owner.open_path.assert_called_once_with(
            Path("civitai.jpeg"),
            from_queue=True,
            supplemental_entries=entries,
        )

    def test_dead_worker_remains_owned_until_completion_event_is_consumed(self) -> None:
        owner = MetadataViewer.__new__(MetadataViewer)
        owner.drop_queue = []
        owner.processing_drop_queue = False
        owner.download_thread = mock.Mock()
        owner.download_thread.is_alive.return_value = False
        owner.status_var = mock.Mock()
        owner._supported_drop_items = mock.Mock(return_value=["new.png"])
        owner._process_next_drop_queue_item = mock.Mock()

        accepted = MetadataViewer._open_drop_items(owner, ["new.png"])

        self.assertTrue(accepted)
        self.assertEqual(owner.drop_queue, ["new.png"])
        owner._process_next_drop_queue_item.assert_not_called()

    def test_scheduled_preview_ignores_an_invalidated_current_path(self) -> None:
        owner = MetadataViewer.__new__(MetadataViewer)
        owner.preview_after_id = "pending"
        owner.current_image_path = None
        owner._render_preview = mock.Mock()

        MetadataViewer._refresh_scheduled_preview(owner)

        self.assertIsNone(owner.preview_after_id)
        owner._render_preview.assert_not_called()

    def test_download_worker_hands_result_off_without_calling_tk(self) -> None:
        owner = MetadataViewer.__new__(MetadataViewer)
        owner.download_events = queue.Queue()
        owner._download_image_url = mock.Mock(return_value=Path("downloaded.png"))
        owner.after = mock.Mock()

        MetadataViewer._download_image_url_worker(owner, "https://example", 4)

        self.assertEqual(
            owner.download_events.get_nowait(),
            ("finished", 4, Path("downloaded.png"), ""),
        )
        owner.after.assert_not_called()


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
