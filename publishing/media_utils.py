from __future__ import annotations

import io
import ipaddress
import os
import socket
import ssl
from http.client import HTTPConnection, HTTPSConnection
from urllib.parse import urljoin, urlparse

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from core.exceptions import MetaPermanentError, MetaTransientError

try:
    from PIL import Image, ImageOps
except ImportError:  # pragma: no cover
    Image = None
    ImageOps = None


VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".m4v", ".avi"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
INSTAGRAM_IMAGE_MAX_SIZE = (1080, 1350)
INSTAGRAM_IMAGE_MIN_WIDTH = 320
INSTAGRAM_ASPECT_RATIO_MIN = 0.8    # 4:5 portrait (most portrait allowed)
INSTAGRAM_ASPECT_RATIO_MAX = 1.91   # 1.91:1 landscape (most landscape allowed)
INSTAGRAM_IMAGE_TARGET_BYTES = 4_000_000  # IG allows 8MB; 4MB balances quality vs URL-fetch speed
INSTAGRAM_VIDEO_MAX_BYTES = 100 * 1024 * 1024
MEDIA_FETCH_TIMEOUT_SECONDS = 12
ALLOWED_PUBLIC_MEDIA_SCHEMES = {"http", "https"}
DISALLOWED_MEDIA_HOSTNAMES = {
    "localhost",
    "localhost.localdomain",
}


def media_extension(media_url: str) -> str:
    path = urlparse(media_url).path.lower()
    if "." not in path:
        return ""
    return path[path.rfind(".") :]


def build_public_media_url(relative_url: str) -> str:
    if settings.PUBLIC_BASE_URL:
        return urljoin(settings.PUBLIC_BASE_URL.rstrip("/") + "/", relative_url.lstrip("/"))
    return relative_url


def resolve_local_media_storage_path(media_url: str) -> str | None:
    parsed = urlparse(media_url)
    media_prefix = settings.MEDIA_URL.rstrip("/")
    if not parsed.path.startswith(media_prefix + "/"):
        return None
    return parsed.path[len(media_prefix) + 1 :]


def _save_public_media_file(storage_path: str, payload: bytes) -> str:
    if default_storage.exists(storage_path):
        default_storage.delete(storage_path)
    saved = default_storage.save(storage_path, ContentFile(payload))
    return build_public_media_url(default_storage.url(saved))


def _optimize_local_image_for_instagram(media_url: str) -> str:
    storage_path = resolve_local_media_storage_path(media_url)
    if not storage_path or Image is None or ImageOps is None:
        return media_url
    if not default_storage.exists(storage_path):
        return media_url

    ext = media_extension(media_url)
    with default_storage.open(storage_path, "rb") as source_file:
        source_bytes = source_file.read()

    try:
        with Image.open(io.BytesIO(source_bytes)) as image:
            if getattr(image, "is_animated", False):
                return media_url

            modified = False
            image = ImageOps.exif_transpose(image)

            # --- Mode conversion (IG requires RGB JPEG) ---
            if image.mode in {"RGBA", "LA", "P"}:
                base = Image.new("RGB", image.size, (255, 255, 255))
                alpha = image.convert("RGBA")
                base.paste(alpha, mask=alpha.getchannel("A"))
                image = base
                modified = True
            elif image.mode != "RGB":
                image = image.convert("RGB")
                modified = True

            # --- Aspect ratio enforcement (Meta: 4:5 to 1.91:1) ---
            # Images outside this range get cropped badly by Instagram or
            # rejected entirely.  Center-crop to nearest valid ratio.
            w, h = image.size
            if h > 0:
                ratio = w / h
                if ratio < INSTAGRAM_ASPECT_RATIO_MIN:
                    # Too tall → crop top/bottom to 4:5
                    new_h = int(w / INSTAGRAM_ASPECT_RATIO_MIN)
                    top = (h - new_h) // 2
                    image = image.crop((0, top, w, top + new_h))
                    modified = True
                elif ratio > INSTAGRAM_ASPECT_RATIO_MAX:
                    # Too wide → crop sides to 1.91:1
                    new_w = int(h * INSTAGRAM_ASPECT_RATIO_MAX)
                    left = (w - new_w) // 2
                    image = image.crop((left, 0, left + new_w, h))
                    modified = True

            # --- Minimum dimension (Meta: 320px width minimum) ---
            if image.width < INSTAGRAM_IMAGE_MIN_WIDTH:
                scale = INSTAGRAM_IMAGE_MIN_WIDTH / image.width
                image = image.resize(
                    (INSTAGRAM_IMAGE_MIN_WIDTH, int(image.height * scale)),
                    Image.Resampling.LANCZOS,
                )
                modified = True

            # --- Maximum dimension (1080×1350) ---
            if image.width > INSTAGRAM_IMAGE_MAX_SIZE[0] or image.height > INSTAGRAM_IMAGE_MAX_SIZE[1]:
                image.thumbnail(INSTAGRAM_IMAGE_MAX_SIZE, Image.Resampling.LANCZOS)
                modified = True

            # Early return only if nothing was modified AND file is already
            # a small-enough JPEG.  Previously this returned the original
            # URL even after in-memory crop/resize, losing those changes.
            original_size = len(source_bytes)
            if not modified and ext in {".jpg", ".jpeg"} and original_size <= INSTAGRAM_IMAGE_TARGET_BYTES:
                return media_url

            # --- JPEG compression — start high quality, step down gently ---
            output = io.BytesIO()
            quality = 90
            while True:
                output.seek(0)
                output.truncate(0)
                image.save(output, format="JPEG", quality=quality, optimize=True, progressive=True)
                if output.tell() <= INSTAGRAM_IMAGE_TARGET_BYTES or quality <= 78:
                    break
                quality -= 4

            derived_path = f"{os.path.splitext(storage_path)[0]}_ig.jpg"
            return _save_public_media_file(derived_path, output.getvalue())
    except OSError:
        return media_url


def prepare_instagram_media_url(media_url: str) -> str:
    ext = media_extension(media_url)
    if ext in VIDEO_EXTENSIONS:
        storage_path = resolve_local_media_storage_path(media_url)
        if storage_path and default_storage.exists(storage_path):
            size = default_storage.size(storage_path)
            if size > INSTAGRAM_VIDEO_MAX_BYTES:
                raise MetaPermanentError(
                    "Instagram video is too large for reliable publishing from this app. "
                    "Use a smaller MP4/MOV file before scheduling again."
                )
        return media_url

    if ext in IMAGE_EXTENSIONS:
        return _optimize_local_image_for_instagram(media_url)

    return media_url


def _is_public_ip_address(value: str) -> bool:
    try:
        return ipaddress.ip_address(value.split("%", 1)[0]).is_global
    except ValueError:
        return False


def _resolved_public_ips(parsed) -> list[str]:
    try:
        resolved = [
            item[4][0]
            for item in socket.getaddrinfo(parsed.hostname, parsed.port or None, type=socket.SOCK_STREAM)
            if item and item[4]
        ]
    except socket.gaierror as exc:
        raise MetaTransientError(
            "Public media host could not be resolved before publish. Check PUBLIC_BASE_URL/DNS and retry."
        ) from exc

    if not resolved:
        raise MetaTransientError("Public media host could not be resolved before publish.")
    if any(not _is_public_ip_address(address) for address in resolved):
        raise MetaPermanentError("Media URL must resolve only to public IP addresses.")
    return resolved


def _validate_public_media_url(media_url: str) -> None:
    parsed = urlparse(str(media_url or "").strip())
    scheme = str(parsed.scheme or "").lower()
    hostname = str(parsed.hostname or "").strip().lower()

    if scheme not in ALLOWED_PUBLIC_MEDIA_SCHEMES:
        raise MetaPermanentError("Media URL must use a public http/https address.")
    if parsed.username or parsed.password:
        raise MetaPermanentError("Media URL cannot include embedded credentials.")
    if not hostname:
        raise MetaPermanentError("Media URL host is missing.")
    if hostname in DISALLOWED_MEDIA_HOSTNAMES or hostname.endswith(".local"):
        raise MetaPermanentError("Media URL must not target localhost or local-network hostnames.")
    if _is_public_ip_address(hostname):
        return
    try:
        ipaddress.ip_address(hostname.split("%", 1)[0])
    except ValueError:
        pass
    else:
        raise MetaPermanentError("Media URL must not target private or reserved IP addresses.")

    _resolved_public_ips(parsed)


class _PinnedHTTPConnection(HTTPConnection):
    def __init__(self, resolved_ip: str, host: str, port: int, timeout: int):
        super().__init__(host=host, port=port, timeout=timeout)
        self._resolved_ip = resolved_ip

    def connect(self):
        self.sock = socket.create_connection((self._resolved_ip, self.port), self.timeout, self.source_address)


class _PinnedHTTPSConnection(HTTPSConnection):
    def __init__(self, resolved_ip: str, host: str, port: int, timeout: int):
        super().__init__(host=host, port=port, timeout=timeout, context=ssl.create_default_context())
        self._resolved_ip = resolved_ip

    def connect(self):
        raw_sock = socket.create_connection((self._resolved_ip, self.port), self.timeout, self.source_address)
        self.sock = self._context.wrap_socket(raw_sock, server_hostname=self.host)


def ensure_public_media_fetchable(media_url: str) -> None:
    _validate_public_media_url(media_url)
    parsed = urlparse(str(media_url or "").strip())
    resolved_ip = _resolved_public_ips(parsed)[0]
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    connection_cls = _PinnedHTTPSConnection if parsed.scheme.lower() == "https" else _PinnedHTTPConnection
    connection = connection_cls(resolved_ip, parsed.hostname or "", port, MEDIA_FETCH_TIMEOUT_SECONDS)
    try:
        connection.request(
            "GET",
            path,
            headers={
                "Host": parsed.hostname or "",
                "User-Agent": "SocialMediaAutomationMediaCheck/1.0",
                "Connection": "close",
            },
        )
        response = connection.getresponse()
    except OSError as exc:
        raise MetaTransientError(
            "Public media URL could not be reached before publish. "
            "Check PUBLIC_BASE_URL/ngrok and retry."
        ) from exc

    try:
        if response.status >= 500:
            raise MetaTransientError(
                f"Public media URL returned {response.status}. "
                "The tunnel or media server is temporarily unavailable."
            )
        if response.status >= 400:
            raise MetaPermanentError(
                f"Public media URL returned {response.status}. "
                "Reconnect the tunnel or upload the media again."
            )
        try:
            response.read(65536)
        except OSError as exc:
            raise MetaTransientError(
                "Public media URL stream timed out while validating media. "
                "Auto-retry should recover if tunnel/network stabilizes."
            ) from exc
    finally:
        connection.close()
