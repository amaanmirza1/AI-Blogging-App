from __future__ import annotations

import secrets
from html.parser import HTMLParser
from pathlib import Path
from uuid import uuid4

import bleach
from fastapi import HTTPException, Request, UploadFile, status

from app.config import settings


ALLOWED_TAGS = [
    "p",
    "br",
    "strong",
    "em",
    "u",
    "blockquote",
    "ul",
    "ol",
    "li",
    "h2",
    "h3",
    "a",
    "code",
    "pre",
    "img",
]
ALLOWED_ATTRIBUTES = {
    "a": ["href", "target", "rel"],
    "img": ["src", "alt"],
}
ALLOWED_PROTOCOLS = ["http", "https", "mailto"]
ALLOWED_IMAGE_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif"}


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        value = data.strip()
        if value:
            self.parts.append(value)

    def get_text(self) -> str:
        return " ".join(self.parts)


def ensure_csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(24)
        request.session["csrf_token"] = token
    return token


def validate_csrf(request: Request, csrf_token: str | None) -> None:
    expected = ensure_csrf_token(request)
    if not csrf_token or csrf_token != expected:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token")


def sanitize_rich_text(content: str) -> str:
    cleaned = bleach.clean(
        content,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        protocols=ALLOWED_PROTOCOLS,
        strip=True,
    )
    return bleach.linkify(cleaned)


def html_to_text(content: str) -> str:
    parser = TextExtractor()
    parser.feed(content)
    return parser.get_text()


async def save_image(upload: UploadFile | None) -> str | None:
    if upload is None or not upload.filename:
        return None
    if upload.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(status_code=400, detail="Unsupported image type")
    data = await upload.read()
    if len(data) > settings.max_upload_size_mb * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Image exceeds upload size limit")
    suffix = ALLOWED_IMAGE_TYPES[upload.content_type]
    filename = f"{uuid4().hex}{suffix}"
    destination = settings.upload_dir / filename
    destination.write_bytes(data)
    return f"/uploads/{filename}"


def delete_uploaded_file(path: str | None) -> None:
    if not path:
        return
    filename = Path(path).name
    target = settings.upload_dir / filename
    if target.exists():
        target.unlink()
