"""Export the selected helper artwork to Windows ICO sizes without redesigning it."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "icarus_helper" / "assets"
SIZES = (16, 32, 48, 64, 128, 256)


def main():
    source = ASSETS / "app-master.png"
    metadata_path = ASSETS / "app.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    with Image.open(source) as image:
        image = image.convert("RGBA")
        if image.width != image.height or image.getchannel("A").getextrema() != (0, 255):
            raise ValueError("The selected square artwork must have real transparency")
        image.save(ASSETS / "app.ico", format="ICO", sizes=[(n, n) for n in SIZES])
        image.resize((256, 256), Image.Resampling.LANCZOS).save(ASSETS / "app.png")
    with Image.open(ASSETS / "app.ico") as icon:
        if icon.ico.sizes() != {(n, n) for n in SIZES}:
            raise ValueError("ICO is missing a required Windows size")
        for n in SIZES:
            frame = icon.ico.getimage((n, n)).convert("RGBA")
            if frame.getchannel("A").getextrema() != (0, 255):
                raise ValueError(f"ICO transparency failed at {n}px")
    metadata.update(exported_at=datetime.now(timezone.utc).isoformat(),
                    master_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                    icon_sha256=hashlib.sha256((ASSETS / "app.ico").read_bytes()).hexdigest(),
                    sizes=list(SIZES), export="ICO size conversion only; artwork created by built-in image_gen")
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(ASSETS / "app.ico"), "sizes": list(SIZES)}))


if __name__ == "__main__":
    main()
