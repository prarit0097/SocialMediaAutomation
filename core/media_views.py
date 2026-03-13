import mimetypes
import os
import re
from pathlib import Path

from django.conf import settings
from django.http import FileResponse, Http404, HttpResponse, HttpResponseNotAllowed
from django.utils._os import safe_join


RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)$")


def serve_media(request, path: str):
    if request.method not in {"GET", "HEAD"}:
        return HttpResponseNotAllowed(["GET", "HEAD"])

    try:
        absolute_path = safe_join(str(settings.MEDIA_ROOT), path)
    except ValueError as exc:
        raise Http404("Invalid media path") from exc

    if not os.path.exists(absolute_path) or not os.path.isfile(absolute_path):
        raise Http404("Media file not found")

    file_size = os.path.getsize(absolute_path)
    content_type, _ = mimetypes.guess_type(absolute_path)
    content_type = content_type or "application/octet-stream"
    file_name = Path(absolute_path).name
    range_header = request.headers.get("Range", "").strip()

    if not range_header:
        if request.method == "HEAD":
            response = HttpResponse(status=200, content_type=content_type)
        else:
            response = FileResponse(open(absolute_path, "rb"), content_type=content_type)
        response["Content-Length"] = str(file_size)
        response["Accept-Ranges"] = "bytes"
        response["Cache-Control"] = "public, max-age=86400"
        response["Content-Disposition"] = f'inline; filename="{file_name}"'
        return response

    match = RANGE_RE.match(range_header)
    if not match:
        response = HttpResponse(status=416)
        response["Content-Range"] = f"bytes */{file_size}"
        return response

    start_str, end_str = match.groups()
    if start_str == "" and end_str == "":
        response = HttpResponse(status=416)
        response["Content-Range"] = f"bytes */{file_size}"
        return response

    if start_str == "":
        suffix_length = int(end_str)
        if suffix_length <= 0:
            response = HttpResponse(status=416)
            response["Content-Range"] = f"bytes */{file_size}"
            return response
        start = max(file_size - suffix_length, 0)
        end = file_size - 1
    else:
        start = int(start_str)
        end = int(end_str) if end_str else file_size - 1

    if start >= file_size or end < start:
        response = HttpResponse(status=416)
        response["Content-Range"] = f"bytes */{file_size}"
        return response

    end = min(end, file_size - 1)
    chunk_size = end - start + 1

    with open(absolute_path, "rb") as media_file:
        media_file.seek(start)
        chunk = media_file.read(chunk_size)

    response = HttpResponse(chunk if request.method == "GET" else b"", status=206, content_type=content_type)
    response["Content-Range"] = f"bytes {start}-{end}/{file_size}"
    response["Content-Length"] = str(chunk_size)
    response["Accept-Ranges"] = "bytes"
    response["Cache-Control"] = "public, max-age=86400"
    response["Content-Disposition"] = f'inline; filename="{file_name}"'
    return response
