import io

import pytest
from PIL import Image

from shelfsense_api.images import InvalidImageError, downscale, inspect


def _png(width: int, height: int, mode: str = "RGBA") -> bytes:
    out = io.BytesIO()
    Image.new(mode, (width, height), (10, 20, 30, 255) if mode == "RGBA" else (10, 20, 30)).save(
        out, format="PNG"
    )
    return out.getvalue()


def test_inspect_reports_type_and_size() -> None:
    info = inspect(_png(640, 480))
    assert info.media_type == "image/png"
    assert (info.width, info.height) == (640, 480)


def test_inspect_rejects_non_images_and_unsupported_formats() -> None:
    with pytest.raises(InvalidImageError, match="not an image"):
        inspect(b"definitely not an image")
    out = io.BytesIO()
    Image.new("RGB", (4, 4)).save(out, format="BMP")
    with pytest.raises(InvalidImageError, match="unsupported"):
        inspect(out.getvalue())


def test_downscale_caps_longest_edge_and_flattens_alpha() -> None:
    data, media_type = downscale(_png(4000, 2000), max_edge=1000)
    assert media_type == "image/jpeg"
    with Image.open(io.BytesIO(data)) as img:
        assert img.format == "JPEG"
        assert img.size == (1000, 500)
        assert img.mode == "RGB"


def test_downscale_keeps_small_images_at_size() -> None:
    data, _ = downscale(_png(300, 200, mode="RGB"), max_edge=1000)
    with Image.open(io.BytesIO(data)) as img:
        assert img.size == (300, 200)
