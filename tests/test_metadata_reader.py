from __future__ import annotations

import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from comfy_metadata_reader import read_metadata


def png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def make_png_with_text(key: str, value: str) -> bytes:
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00\x00\x00\x00")
    text = key.encode("latin-1") + b"\x00" + value.encode("utf-8")
    return (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", ihdr)
        + png_chunk(b"tEXt", text)
        + png_chunk(b"IDAT", idat)
        + png_chunk(b"IEND", b"")
    )


def make_tiff_with_user_comment(comment: str) -> bytes:
    payload = comment.encode("utf-16-be")
    user_comment = b"UNICODE\x00" + payload
    workflow = b'workflow:{"nodes":[]}\x00'
    prompt = b'prompt:{"1":{"inputs":{}}}\x00'

    header = b"II*\x00\x08\x00\x00\x00"
    ifd0_offset = 8
    ifd0_entry_count = 3
    ifd0_size = 2 + ifd0_entry_count * 12 + 4
    data_offset = ifd0_offset + ifd0_size

    workflow_offset = data_offset
    prompt_offset = workflow_offset + len(workflow)
    exif_ifd_offset = prompt_offset + len(prompt)
    exif_ifd_size = 2 + 12 + 4
    user_comment_offset = exif_ifd_offset + exif_ifd_size

    ifd0 = bytearray()
    ifd0 += struct.pack("<H", ifd0_entry_count)
    ifd0 += struct.pack("<HHI", 0x010F, 2, len(workflow))
    ifd0 += struct.pack("<I", workflow_offset)
    ifd0 += struct.pack("<HHI", 0x0110, 2, len(prompt))
    ifd0 += struct.pack("<I", prompt_offset)
    ifd0 += struct.pack("<HHI", 0x8769, 4, 1)
    ifd0 += struct.pack("<I", exif_ifd_offset)
    ifd0 += struct.pack("<I", 0)

    exif_ifd = bytearray()
    exif_ifd += struct.pack("<H", 1)
    exif_ifd += struct.pack("<HHI", 0x9286, 7, len(user_comment))
    exif_ifd += struct.pack("<I", user_comment_offset)
    exif_ifd += struct.pack("<I", 0)

    return header + bytes(ifd0) + workflow + prompt + bytes(exif_ifd) + user_comment


def make_webp_with_exif(exif: bytes) -> bytes:
    chunk = b"EXIF" + struct.pack("<I", len(exif)) + exif
    if len(exif) & 1:
        chunk += b"\x00"
    riff_size = 4 + len(chunk)
    return b"RIFF" + struct.pack("<I", riff_size) + b"WEBP" + chunk


class MetadataReaderTests(unittest.TestCase):
    def test_reads_png_parameters_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sample.png"
            path.write_bytes(make_png_with_text("parameters", "cat\nSteps: 20"))

            result = read_metadata(path)

        self.assertEqual(result.format_name, "PNG")
        self.assertEqual(result.first_value("parameters"), "cat\nSteps: 20")

    def test_reads_webp_exif_user_comment_and_comfy_aliases(self) -> None:
        comment = "cat\nNegative prompt: dog\nSteps: 20, Seed: 123"
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sample.webp"
            path.write_bytes(make_webp_with_exif(make_tiff_with_user_comment(comment)))

            result = read_metadata(path)

        self.assertEqual(result.format_name, "WEBP")
        self.assertEqual(result.first_value("parameters"), comment)
        self.assertEqual(result.first_value("workflow"), '{"nodes":[]}')
        self.assertEqual(result.first_value("prompt"), '{"1":{"inputs":{}}}')


if __name__ == "__main__":
    unittest.main()
