"""Validate uploads and shrink them to what the vision model needs."""

import io
from dataclasses import dataclass
from typing import Literal

from PIL import Image, UnidentifiedImageError

MediaType = Literal["image/jpeg", "image/png", "image/webp"]

ALLOWED: dict[str, MediaType] = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
}


class InvalidImageError(ValueError):
    """The bytes are not an image we accept."""


@dataclass(frozen=True)
class ImageInfo:
    """What we learned from decoding an upload."""

    media_type: MediaType
    width: int
    height: int


def inspect(data: bytes) -> ImageInfo:
    """Decode the header, reject unsupported formats. Does not load pixel data."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            fmt = img.format or ""
            if fmt not in ALLOWED:
                raise InvalidImageError(f"unsupported image format {fmt or 'unknown'!r}")
            return ImageInfo(media_type=ALLOWED[fmt], width=img.width, height=img.height)
    except UnidentifiedImageError as exc:
        raise InvalidImageError("not an image") from exc


def downscale(data: bytes, max_edge: int) -> tuple[bytes, MediaType]:
    """Resize so the longest edge is ``max_edge`` and re-encode as JPEG.

    Larger images cost more tokens without improving shelf-level extraction; the model's
    own ceiling is 1568px on the long edge. PNGs with transparency are flattened.
    """
    with Image.open(io.BytesIO(data)) as img:
        img.load()
        work: Image.Image = img
        longest = max(work.width, work.height)
        if longest > max_edge:
            scale = max_edge / longest
            work = work.resize((round(work.width * scale), round(work.height * scale)))
        if work.mode not in ("RGB", "L"):
            work = work.convert("RGB")
        out = io.BytesIO()
        work.save(out, format="JPEG", quality=88, optimize=True)
        return out.getvalue(), "image/jpeg"
