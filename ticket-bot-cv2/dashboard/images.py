"""Validated banner/image uploads for the dashboard."""

from __future__ import annotations

import re
import uuid
from pathlib import Path

MAX_IMAGE_BYTES = 4 * 1024 * 1024
ALLOWED_TYPES = {
    "image/png": (".png", b"\x89PNG\r\n\x1a\n"),
    "image/jpeg": (".jpg", b"\xff\xd8\xff"),
    "image/webp": (".webp", b"RIFF"),
    "image/gif": (".gif", b"GIF8"),
}

IMAGE_SLOTS = {
    "panel_banner": "panel_banner_url",
    "panel_footer": "panel_footer_url",
    "ticket_banner": "ticket_banner_url",
    "ticket_footer": "ticket_footer_url",
    "welcome": "welcome_image_url",
}

SLOT_LABELS = {
    "panel_banner": "Assistance panel banner",
    "panel_footer": "Assistance panel footer",
    "ticket_banner": "Ticket banner",
    "ticket_footer": "Ticket footer",
    "welcome": "Welcome picture",
}

UPLOAD_URL_PREFIX = "/media/uploads/"


class UploadError(ValueError):
    """The uploaded file is not an acceptable image."""


def validate(content_type: str | None, data: bytes) -> str:
    """Return the file extension for a validated image payload."""

    if not data:
        raise UploadError("The uploaded file was empty.")
    if len(data) > MAX_IMAGE_BYTES:
        raise UploadError("Images must be 4 MB or smaller.")
    content_type = (content_type or "").split(";")[0].strip().lower()
    if content_type not in ALLOWED_TYPES:
        raise UploadError("Use a PNG, JPEG, WebP or GIF image.")
    extension, signature = ALLOWED_TYPES[content_type]
    if not data.startswith(signature):
        raise UploadError("The file contents do not match its image type.")
    return extension


def safe_filename(slot: str, extension: str) -> str:
    slot = re.sub(r"[^a-z_]", "", slot.lower()) or "image"
    return f"{slot}-{uuid.uuid4().hex[:12]}{extension}"


def store(uploads_dir: Path, slot: str, content_type: str | None, data: bytes) -> str:
    """Write a validated image and return the path the dashboard serves it from."""

    if slot not in IMAGE_SLOTS:
        raise UploadError("Unknown image slot.")
    extension = validate(content_type, data)
    uploads_dir.mkdir(parents=True, exist_ok=True)
    filename = safe_filename(slot, extension)
    (uploads_dir / filename).write_bytes(data)
    return f"{UPLOAD_URL_PREFIX}{filename}"


def remove(uploads_dir: Path, public_path: str | None) -> None:
    """Delete a previously uploaded file, ignoring files outside the uploads dir."""

    if not public_path or not public_path.startswith(UPLOAD_URL_PREFIX):
        return
    name = public_path[len(UPLOAD_URL_PREFIX) :]
    if "/" in name or name in {"", ".", ".."}:
        return
    target = uploads_dir / name
    if target.is_file():
        target.unlink()
