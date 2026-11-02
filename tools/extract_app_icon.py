"""Copy an installed game's native multi-resolution icon without changing pixels.

Only PE resource metadata and individual RT_ICON payloads are read. The original
executable is never written, launched, or bundled with ICARUS Helper.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import struct

import pefile
from PIL import Image


DEFAULT_SOURCE = Path(r"C:\Program Files (x86)\Steam\steamapps\common\Icarus\Icarus\Binaries\Win64\Icarus-Win64-Shipping.exe")
DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "icarus_helper" / "assets" / "game-native.ico"


def extract_icon(source: Path, group_id: int = 123) -> tuple[bytes, list[dict], int]:
    """Reconstruct the ICO directory around byte-identical RT_ICON payloads."""
    pe = pefile.PE(str(source), fast_load=True)
    try:
        pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"]])
        resource_types = {entry.id: entry for entry in pe.DIRECTORY_ENTRY_RESOURCE.entries}
        icon_groups = resource_types[14].directory.entries
        selected = next((entry for entry in icon_groups if entry.id == group_id), None)
        if selected is None:
            raise ValueError(f"Icon group {group_id} not found")
        language = selected.directory.entries[0]
        descriptor = language.data.struct
        group = pe.get_data(descriptor.OffsetToData, descriptor.Size)
        reserved, image_type, count = struct.unpack_from("<HHH", group)
        if reserved != 0 or image_type != 1 or not count or len(group) != 6 + 14 * count:
            raise ValueError("Malformed RT_GROUP_ICON directory")
        raw_icons = {entry.id: entry for entry in resource_types[3].directory.entries}
        payloads, directory, sizes = [], [], []
        offset = 6 + count * 16
        for index in range(count):
            width, height, palette, zero, planes, bits, length, resource_id = struct.unpack_from("<BBBBHHIH", group, 6 + index * 14)
            candidates = raw_icons[resource_id].directory.entries
            icon_language = next((entry for entry in candidates if entry.id == language.id), candidates[0])
            icon_descriptor = icon_language.data.struct
            payload = pe.get_data(icon_descriptor.OffsetToData, icon_descriptor.Size)
            if len(payload) != length:
                raise ValueError("RT_ICON length disagrees with group metadata")
            directory.append(struct.pack("<BBBBHHII", width, height, palette, zero, planes, bits, length, offset))
            payloads.append(payload)
            sizes.append({"width": width or 256, "height": height or 256, "bits_per_pixel": bits,
                          "resource_id": resource_id, "payload_sha256": hashlib.sha256(payload).hexdigest()})
            offset += length
        return struct.pack("<HHH", 0, 1, count) + b"".join(directory) + b"".join(payloads), sizes, language.id
    finally:
        pe.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--group", type=int, default=123)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    source = args.source.resolve(strict=True)
    output = args.output.resolve()
    if source == output:
        raise ValueError("Output must not replace the source executable")
    icon_bytes, images, language = extract_icon(source, args.group)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(icon_bytes)
    preview = output.with_suffix(".png")
    with Image.open(output) as decoded:
        expected_sizes = {(item["width"], item["height"]) for item in images}
        if decoded.ico.sizes() != expected_sizes:
            raise ValueError("Decoded ICO sizes do not match the original directory")
        decoded.ico.getimage(max(expected_sizes)).save(preview)
    with source.open("rb") as source_stream:
        source_hash = hashlib.file_digest(source_stream, "sha256").hexdigest()
    metadata = {
        "source": str(source), "source_sha256": source_hash,
        "group_id": args.group, "language_id": language,
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "icon_sha256": hashlib.sha256(icon_bytes).hexdigest(),
        "images": images,
        "usage": "Native ICARUS application icon copied for the user's local helper; original icon payloads unchanged.",
        "preview": "PNG decoded from the largest original image; no visual changes.",
    }
    output.with_suffix(".json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "preview": str(preview), "sizes": sorted(expected_sizes)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
