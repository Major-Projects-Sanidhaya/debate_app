"""In-memory frame encoding for video moderation.

PRIVACY: frames exist only as bytes in memory for the duration of one
classification call. Nothing here writes to disk, Redis, or logs — keep it
that way. `encode_rgb_to_jpeg` is deliberately LiveKit-free so it stays
unit-testable; the caller does the rtc frame conversion.
"""

import io

from PIL import Image

MAX_DIMENSION = 512
JPEG_QUALITY = 80


def encode_rgb_to_jpeg(
    width: int,
    height: int,
    rgb_bytes: bytes,
    *,
    max_dimension: int = MAX_DIMENSION,
    quality: int = JPEG_QUALITY,
) -> bytes:
    """Downscale a raw RGB24 frame so its long edge is <= max_dimension and
    return JPEG bytes. Never touches the filesystem."""
    image = Image.frombytes("RGB", (width, height), rgb_bytes)
    longest = max(image.width, image.height)
    if longest > max_dimension:
        scale = max_dimension / longest
        image = image.resize(
            (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
            Image.LANCZOS,
        )
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality)
    return buffer.getvalue()


def frame_to_jpeg(frame, **kwargs) -> bytes:
    """Convert an rtc.VideoFrame to a downscaled in-memory JPEG."""
    from livekit import rtc  # imported lazily to keep this module test-friendly

    rgb = frame.convert(rtc.VideoBufferType.RGB24)
    return encode_rgb_to_jpeg(rgb.width, rgb.height, bytes(rgb.data), **kwargs)
