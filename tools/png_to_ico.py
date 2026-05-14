import argparse
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="Source PNG path")
    ap.add_argument("--dst", required=True, help="Destination ICO path")
    args = ap.parse_args()

    src = Path(args.src)
    dst = Path(args.dst)
    if not src.exists():
        raise SystemExit(f"Missing source file: {src}")

    try:
        from PIL import Image
    except Exception as e:  # pragma: no cover
        raise SystemExit(
            "Pillow is required to convert PNG->ICO. Install with: pip install pillow"
        ) from e

    img = Image.open(src)
    if img.mode not in ("RGBA", "RGB"):
        img = img.convert("RGBA")

    dst.parent.mkdir(parents=True, exist_ok=True)
    # Common Windows icon sizes (PyInstaller prefers a multi-size ICO).
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    img.save(dst, format="ICO", sizes=sizes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

