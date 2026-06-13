from __future__ import annotations

import argparse
import json
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
JPEG_SIGNATURE = b"\xff\xd8"

COMFY_KEYS = {"parameters", "prompt", "workflow"}
KNOWN_TEXT_KEYS = {
    "comment",
    "description",
    "imagedescription",
    "parameters",
    "prompt",
    "usercomment",
    "workflow",
}

EXIF_TAG_NAMES = {
    0x010E: "ImageDescription",
    0x010F: "Make",
    0x0110: "Model",
    0x0131: "Software",
    0x0132: "DateTime",
    0x8298: "Copyright",
    0x8769: "ExifIFDPointer",
    0x8825: "GPSInfoIFDPointer",
    0x9003: "DateTimeOriginal",
    0x9286: "UserComment",
}

TIFF_TYPES = {
    1: ("BYTE", 1),
    2: ("ASCII", 1),
    3: ("SHORT", 2),
    4: ("LONG", 4),
    5: ("RATIONAL", 8),
    7: ("UNDEFINED", 1),
    9: ("SLONG", 4),
    10: ("SRATIONAL", 8),
    13: ("IFD", 4),
}


@dataclass(frozen=True)
class MetadataEntry:
    source: str
    key: str
    value: str


@dataclass
class MetadataResult:
    path: Path
    format_name: str
    entries: list[MetadataEntry]
    warnings: list[str]

    def values(self, key: str) -> list[str]:
        key_lower = key.lower()
        return [entry.value for entry in self.entries if entry.key.lower() == key_lower]

    def first_value(self, *keys: str) -> str:
        for key in keys:
            values = self.values(key)
            if values:
                return values[0]
        return ""


@dataclass(frozen=True)
class MetadataSections:
    platform: str
    prompt: str
    negative_prompt: str
    settings: str
    workflow: str
    raw_parameters: str


def read_metadata(path: str | Path) -> MetadataResult:
    image_path = Path(path)
    data = image_path.read_bytes()
    warnings: list[str] = []
    entries: list[MetadataEntry] = []

    if data.startswith(PNG_SIGNATURE):
        format_name = "PNG"
        entries.extend(_read_png_entries(data, warnings))
    elif data.startswith(JPEG_SIGNATURE):
        format_name = "JPEG"
        entries.extend(_read_jpeg_entries(data, warnings))
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        format_name = "WEBP"
        entries.extend(_read_webp_entries(data, warnings))
    else:
        format_name = "UNKNOWN"
        warnings.append("Unsupported or unknown image signature.")

    entries = _with_platform_aliases(entries)
    return MetadataResult(image_path, format_name, entries, warnings)


def _with_platform_aliases(entries: list[MetadataEntry]) -> list[MetadataEntry]:
    result = list(entries)
    seen = {(entry.source, entry.key, entry.value) for entry in result}

    def add(source: str, key: str, value: str) -> None:
        marker = (source, key, value)
        if value and marker not in seen:
            result.append(MetadataEntry(source, key, value))
            seen.add(marker)

    for entry in entries:
        lower_key = entry.key.lower()
        if entry.key == "UserComment":
            add("Generic", "parameters", entry.value)

        prefix = _split_known_prefix(entry.value)
        if prefix is not None:
            key, value = prefix
            add("Comfy", key, value)

        if lower_key == "parameters":
            add("A1111", "parameters", entry.value)
        elif lower_key == "workflow":
            add("Comfy", "workflow", entry.value)
        elif lower_key == "prompt":
            add("Comfy", "prompt", entry.value)
        elif lower_key in {"comment", "usercomment"}:
            add("Generic", "comment", entry.value)

        json_data = _parse_json_object(entry.value)
        if isinstance(json_data, dict):
            if "uc" in json_data and "prompt" in json_data:
                add("NovelAI", "prompt", _stringify_json_field(json_data.get("prompt")))
                add("NovelAI", "negative_prompt", _stringify_json_field(json_data.get("uc")))
                rest = {
                    key: value
                    for key, value in json_data.items()
                    if key not in {"prompt", "uc", "negative_prompt"}
                }
                if rest:
                    add("NovelAI", "settings", json.dumps(rest, indent=2, ensure_ascii=False))
            elif "negative_prompt" in json_data and "prompt" in json_data:
                add("Generic", "prompt", _stringify_json_field(json_data.get("prompt")))
                add(
                    "Generic",
                    "negative_prompt",
                    _stringify_json_field(json_data.get("negative_prompt")),
                )
            elif lower_key in KNOWN_TEXT_KEYS:
                for key in ("prompt", "negative_prompt", "workflow", "parameters"):
                    if key in json_data:
                        add("Generic", key, _stringify_json_field(json_data[key]))

    return result


def _split_known_prefix(value: str) -> tuple[str, str] | None:
    head, sep, tail = value.partition(":")
    if not sep:
        return None
    key = head.strip().lower()
    if key in COMFY_KEYS:
        return key, tail.strip()
    return None


def _parse_json_object(value: str) -> Any:
    stripped = value.strip()
    if not stripped or stripped[0] not in "{[":
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return None


def _stringify_json_field(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, indent=2, ensure_ascii=False)


def _read_png_entries(data: bytes, warnings: list[str]) -> list[MetadataEntry]:
    entries: list[MetadataEntry] = []
    offset = len(PNG_SIGNATURE)

    while offset + 8 <= len(data):
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        chunk_type = data[offset + 4 : offset + 8]
        chunk_start = offset + 8
        chunk_end = chunk_start + length
        if chunk_end + 4 > len(data):
            warnings.append("PNG chunk length exceeds file size.")
            break

        chunk = data[chunk_start:chunk_end]
        if chunk_type == b"tEXt":
            parsed = _parse_png_text_chunk(chunk)
            if parsed:
                key, value = parsed
                entries.append(MetadataEntry("PNG tEXt", key, value))
        elif chunk_type == b"zTXt":
            parsed = _parse_png_ztxt_chunk(chunk, warnings)
            if parsed:
                key, value = parsed
                entries.append(MetadataEntry("PNG zTXt", key, value))
        elif chunk_type == b"iTXt":
            parsed = _parse_png_itxt_chunk(chunk, warnings)
            if parsed:
                key, value = parsed
                entries.append(MetadataEntry("PNG iTXt", key, value))

        offset = chunk_end + 4
        if chunk_type == b"IEND":
            break

    return entries


def _parse_png_text_chunk(chunk: bytes) -> tuple[str, str] | None:
    if b"\x00" not in chunk:
        return None
    key_raw, value_raw = chunk.split(b"\x00", 1)
    return _decode_png_key(key_raw), _decode_text(value_raw)


def _parse_png_ztxt_chunk(
    chunk: bytes, warnings: list[str]
) -> tuple[str, str] | None:
    try:
        key_raw, rest = chunk.split(b"\x00", 1)
        if not rest:
            return None
        compression_method = rest[0]
        if compression_method != 0:
            warnings.append(f"Unsupported PNG zTXt compression method: {compression_method}")
            return None
        return _decode_png_key(key_raw), _decode_text(zlib.decompress(rest[1:]))
    except Exception as exc:
        warnings.append(f"Failed to read PNG zTXt chunk: {exc}")
        return None


def _parse_png_itxt_chunk(
    chunk: bytes, warnings: list[str]
) -> tuple[str, str] | None:
    try:
        key_raw, rest = chunk.split(b"\x00", 1)
        compression_flag = rest[0]
        compression_method = rest[1]
        rest = rest[2:]
        language_tag, rest = rest.split(b"\x00", 1)
        translated_keyword, text_raw = rest.split(b"\x00", 1)
        _ = language_tag, translated_keyword
        if compression_flag:
            if compression_method != 0:
                warnings.append(
                    f"Unsupported PNG iTXt compression method: {compression_method}"
                )
                return None
            text_raw = zlib.decompress(text_raw)
        return _decode_png_key(key_raw), _decode_text(text_raw)
    except Exception as exc:
        warnings.append(f"Failed to read PNG iTXt chunk: {exc}")
        return None


def _read_webp_entries(data: bytes, warnings: list[str]) -> list[MetadataEntry]:
    entries: list[MetadataEntry] = []
    offset = 12

    while offset + 8 <= len(data):
        fourcc_raw = data[offset : offset + 4]
        try:
            fourcc = fourcc_raw.decode("ascii")
        except UnicodeDecodeError:
            fourcc = repr(fourcc_raw)
        chunk_size = struct.unpack("<I", data[offset + 4 : offset + 8])[0]
        chunk_start = offset + 8
        chunk_end = chunk_start + chunk_size
        if chunk_end > len(data):
            warnings.append(f"WEBP chunk {fourcc} length exceeds file size.")
            break

        chunk = data[chunk_start:chunk_end]
        if fourcc == "EXIF":
            entries.extend(_read_exif_entries(chunk, "WEBP EXIF", warnings))
        elif fourcc == "XMP ":
            entries.append(MetadataEntry("WEBP XMP", "XMP", _decode_text(chunk)))

        offset = chunk_end + (chunk_size & 1)

    return entries


def _read_jpeg_entries(data: bytes, warnings: list[str]) -> list[MetadataEntry]:
    entries: list[MetadataEntry] = []
    offset = 2

    while offset + 4 <= len(data):
        if data[offset] != 0xFF:
            next_marker = data.find(b"\xff", offset)
            if next_marker < 0:
                break
            offset = next_marker

        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset >= len(data):
            break

        marker = data[offset]
        offset += 1
        if marker in {0xD8, 0xD9}:
            continue
        if marker == 0xDA:
            break
        if marker in set(range(0xD0, 0xD8)) | {0x01}:
            continue
        if offset + 2 > len(data):
            break

        segment_length = struct.unpack(">H", data[offset : offset + 2])[0]
        segment_start = offset + 2
        segment_end = offset + segment_length
        if segment_end > len(data):
            warnings.append("JPEG segment length exceeds file size.")
            break
        segment = data[segment_start:segment_end]

        if marker == 0xE1 and segment.startswith(b"Exif\x00\x00"):
            entries.extend(_read_exif_entries(segment[6:], "JPEG EXIF", warnings))
        elif marker == 0xE1 and segment.startswith(
            b"http://ns.adobe.com/xap/1.0/\x00"
        ):
            xmp = segment.split(b"\x00", 1)[1]
            entries.append(MetadataEntry("JPEG XMP", "XMP", _decode_text(xmp)))
        elif marker == 0xFE:
            entries.append(MetadataEntry("JPEG Comment", "Comment", _decode_text(segment)))

        offset = segment_end

    return entries


def _read_exif_entries(
    exif_data: bytes, source: str, warnings: list[str]
) -> list[MetadataEntry]:
    if exif_data.startswith(b"Exif\x00\x00"):
        exif_data = exif_data[6:]
    if len(exif_data) < 8:
        warnings.append(f"{source} data is too short.")
        return []

    byte_order = exif_data[:2]
    if byte_order == b"II":
        endian = "<"
    elif byte_order == b"MM":
        endian = ">"
    else:
        warnings.append(f"{source} does not start with a TIFF byte order marker.")
        return []

    magic = struct.unpack(endian + "H", exif_data[2:4])[0]
    if magic != 42:
        warnings.append(f"{source} has unsupported TIFF magic: {magic}")
        return []

    entries: list[MetadataEntry] = []
    first_ifd_offset = struct.unpack(endian + "I", exif_data[4:8])[0]
    ifd0_values = _read_tiff_ifd(exif_data, first_ifd_offset, endian, f"{source} IFD0", warnings)
    entries.extend(ifd0_values["entries"])

    exif_pointer = ifd0_values["pointers"].get(0x8769)
    if isinstance(exif_pointer, int):
        exif_values = _read_tiff_ifd(
            exif_data, exif_pointer, endian, f"{source} ExifIFD", warnings
        )
        entries.extend(exif_values["entries"])

    return entries


def _read_tiff_ifd(
    data: bytes,
    offset: int,
    endian: str,
    source: str,
    warnings: list[str],
) -> dict[str, Any]:
    if offset < 0 or offset + 2 > len(data):
        warnings.append(f"{source} offset is outside EXIF data.")
        return {"entries": [], "pointers": {}}

    entry_count = struct.unpack(endian + "H", data[offset : offset + 2])[0]
    cursor = offset + 2
    entries: list[MetadataEntry] = []
    pointers: dict[int, int] = {}

    for _ in range(entry_count):
        if cursor + 12 > len(data):
            warnings.append(f"{source} entry exceeds EXIF data.")
            break

        tag, type_id, count = struct.unpack(endian + "HHI", data[cursor : cursor + 8])
        value_or_offset = data[cursor + 8 : cursor + 12]
        cursor += 12

        value, pointer = _decode_tiff_value(
            data, endian, tag, type_id, count, value_or_offset, warnings, source
        )
        if pointer is not None:
            pointers[tag] = pointer

        tag_name = EXIF_TAG_NAMES.get(tag, f"Tag0x{tag:04X}")
        entries.append(MetadataEntry(source, tag_name, _stringify_value(value)))

    return {"entries": entries, "pointers": pointers}


def _decode_tiff_value(
    data: bytes,
    endian: str,
    tag: int,
    type_id: int,
    count: int,
    value_or_offset: bytes,
    warnings: list[str],
    source: str,
) -> tuple[Any, int | None]:
    type_info = TIFF_TYPES.get(type_id)
    if not type_info:
        return f"<unsupported TIFF type {type_id}>", None

    type_name, type_size = type_info
    total_size = type_size * count
    pointer = None
    if total_size <= 4:
        raw_value = value_or_offset[:total_size]
        value_offset = struct.unpack(endian + "I", value_or_offset)[0]
    else:
        value_offset = struct.unpack(endian + "I", value_or_offset)[0]
        if value_offset + total_size > len(data):
            warnings.append(f"{source} tag 0x{tag:04X} points outside EXIF data.")
            return "<invalid offset>", None
        raw_value = data[value_offset : value_offset + total_size]

    if tag in {0x8769, 0x8825} and type_id in {4, 13} and count == 1:
        pointer = value_offset
        return value_offset, pointer

    if tag == 0x9286:
        return _decode_user_comment(raw_value), pointer
    if type_name == "ASCII":
        return _decode_text(raw_value.split(b"\x00", 1)[0]), pointer
    if type_name in {"BYTE", "UNDEFINED"}:
        return _decode_binary_or_hex(raw_value), pointer
    if type_name == "SHORT":
        fmt = endian + ("H" * count)
        values = struct.unpack(fmt, raw_value)
        return values[0] if count == 1 else list(values), pointer
    if type_name in {"LONG", "IFD"}:
        fmt = endian + ("I" * count)
        values = struct.unpack(fmt, raw_value)
        return values[0] if count == 1 else list(values), pointer
    if type_name == "SLONG":
        fmt = endian + ("i" * count)
        values = struct.unpack(fmt, raw_value)
        return values[0] if count == 1 else list(values), pointer
    if type_name in {"RATIONAL", "SRATIONAL"}:
        signed = type_name == "SRATIONAL"
        unit = "ii" if signed else "II"
        values = []
        for index in range(count):
            num, den = struct.unpack(
                endian + unit, raw_value[index * 8 : index * 8 + 8]
            )
            values.append(f"{num}/{den}" if den else f"{num}/0")
        return values[0] if count == 1 else values, pointer

    return _decode_binary_or_hex(raw_value), pointer


def _decode_user_comment(raw_value: bytes) -> str:
    header = raw_value[:8]
    payload = raw_value[8:]
    if header.startswith(b"ASCII"):
        return _decode_text(payload).rstrip("\x00")
    if header.startswith(b"UNICODE"):
        return _decode_utf16(payload).rstrip("\x00")
    if header.startswith(b"JIS"):
        return payload.rstrip(b"\x00").decode("shift_jis", errors="replace")
    return _decode_text(raw_value).rstrip("\x00")


def _decode_utf16(payload: bytes) -> str:
    if payload.startswith((b"\xff\xfe", b"\xfe\xff")):
        return payload.decode("utf-16", errors="replace")

    candidates = []
    for encoding in ("utf-16-be", "utf-16-le", "utf-8"):
        text = payload.decode(encoding, errors="replace")
        score = sum(ch.isprintable() or ch in "\r\n\t" for ch in text)
        score -= text.count("\ufffd") * 10
        candidates.append((score, text))
    return max(candidates, key=lambda item: item[0])[1]


def _decode_binary_or_hex(raw_value: bytes) -> str:
    text = _decode_text(raw_value).rstrip("\x00")
    if text and sum(ch.isprintable() or ch in "\r\n\t" for ch in text) >= len(text) * 0.8:
        return text
    return raw_value.hex()


def _decode_png_key(raw_value: bytes) -> str:
    return raw_value.decode("latin-1", errors="replace")


def _decode_text(raw_value: bytes) -> str:
    raw_value = raw_value.rstrip(b"\x00")
    for encoding in ("utf-8", "utf-16", "latin-1"):
        try:
            return raw_value.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw_value.decode("utf-8", errors="replace")


def _stringify_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value)
    return str(value)


def pretty_value(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        return ""
    if stripped[0] in "[{":
        try:
            return json.dumps(json.loads(stripped), indent=2, ensure_ascii=False)
        except json.JSONDecodeError:
            return value
    return value


def split_a1111_parameters(parameters: str) -> dict[str, str]:
    lines = parameters.splitlines()
    if not lines:
        return {"prompt": "", "negative_prompt": "", "settings": ""}

    settings_index = None
    for index, line in enumerate(lines):
        if line.strip().startswith("Steps:"):
            settings_index = index
            break

    negative_index = None
    for index, line in enumerate(lines):
        if line.strip().startswith("Negative prompt:"):
            negative_index = index
            break

    if negative_index is not None:
        prompt_lines = lines[:negative_index]
        negative_end = settings_index if settings_index is not None else len(lines)
        negative_lines = lines[negative_index:negative_end]
        if negative_lines:
            negative_lines[0] = negative_lines[0].split("Negative prompt:", 1)[1].strip()
        settings_lines = lines[settings_index:] if settings_index is not None else []
    else:
        prompt_end = settings_index if settings_index is not None else len(lines)
        prompt_lines = lines[:prompt_end]
        negative_lines = []
        settings_lines = lines[settings_index:] if settings_index is not None else []

    return {
        "prompt": "\n".join(prompt_lines).strip(),
        "negative_prompt": "\n".join(negative_lines).strip(),
        "settings": "\n".join(settings_lines).strip(),
    }


def extract_sections(result: MetadataResult) -> MetadataSections:
    parameters = result.first_value("parameters")
    workflow = result.first_value("workflow")
    prompt = ""
    negative_prompt = ""
    settings = ""
    platform = "Unknown"

    if workflow or result.first_value("prompt"):
        platform = "ComfyUI"

    if parameters:
        split_params = split_a1111_parameters(parameters)
        prompt = split_params["prompt"]
        negative_prompt = split_params["negative_prompt"]
        settings = split_params["settings"]
        if settings or negative_prompt:
            platform = "A1111/WebUI compatible"

    novel_prompt = _first_value_from_source(result, "NovelAI", "prompt")
    novel_negative = _first_value_from_source(result, "NovelAI", "negative_prompt")
    novel_settings = _first_value_from_source(result, "NovelAI", "settings")
    if novel_prompt or novel_negative:
        platform = "NovelAI"
        prompt = novel_prompt or prompt
        negative_prompt = novel_negative or negative_prompt
        settings = novel_settings or settings

    generic_prompt = _first_value_from_source(result, "Generic", "prompt")
    generic_negative = _first_value_from_source(result, "Generic", "negative_prompt")
    if not prompt and generic_prompt:
        prompt = generic_prompt
    if not negative_prompt and generic_negative:
        negative_prompt = generic_negative

    if not prompt:
        prompt = result.first_value("prompt")
    if not settings:
        settings = result.first_value("settings")

    return MetadataSections(
        platform=platform,
        prompt=pretty_value(prompt),
        negative_prompt=pretty_value(negative_prompt),
        settings=pretty_value(settings),
        workflow=pretty_value(workflow),
        raw_parameters=parameters,
    )


def _first_value_from_source(result: MetadataResult, source: str, key: str) -> str:
    source_lower = source.lower()
    key_lower = key.lower()
    for entry in result.entries:
        if entry.source.lower() == source_lower and entry.key.lower() == key_lower:
            return entry.value
    return ""


def format_report(result: MetadataResult) -> str:
    sections = extract_sections(result)
    lines = [
        f"File: {result.path}",
        f"Format: {result.format_name}",
        f"Platform: {sections.platform}",
        f"Metadata entries: {len(result.entries)}",
        "",
    ]

    for key, value in (
        ("prompt", sections.prompt),
        ("negative_prompt", sections.negative_prompt),
        ("settings", sections.settings),
        ("workflow", sections.workflow),
        ("parameters", sections.raw_parameters),
    ):
        if value:
            lines.extend([f"[{key}]", pretty_value(value), ""])

    lines.append("[raw]")
    for entry in result.entries:
        lines.append(f"{entry.source} / {entry.key}")
        lines.append(pretty_value(entry.value))
        lines.append("")

    if result.warnings:
        lines.append("[warnings]")
        lines.extend(result.warnings)

    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Read ComfyUI image metadata.")
    parser.add_argument("image", help="PNG, WEBP, or JPEG image path")
    args = parser.parse_args()
    print(format_report(read_metadata(args.image)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
