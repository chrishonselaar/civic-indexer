"""Streaming fetch with validation.

Ported from wob-search-mcp/scripts/run_backfill_from_ledger.py (ensure_pdf:
Content-Length pre-check, 64KB streaming chunks, mid-stream byte-cap abort,
partial cleanup) and scripts/recover_truncated.py (%%EOF-in-last-1KB truncation
check), de-asynced to requests. Type detection is by magic bytes, never URL
extension (decision-inventory.md §5).
"""

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path

import requests

CHUNK = 1 << 16  # 64 KB

MAGIC_TYPES = [
    (b"%PDF", "pdf"),
    (b"PK\x03\x04", "zip"),
    (b"{", "json"),
    (b"<?xml", "xml"),  # must precede the generic "<" html rule
    (b"<", "html"),
]


@dataclass
class FetchResult:
    ok: bool
    path: Path | None
    sniffed_type: str | None
    bytes: int
    content_hash: str | None
    validated_magic: bool
    validated_eof: bool
    validated_min_size: bool
    error: str | None = None
    retryable: bool = False


def sniff_type(head: bytes) -> str:
    stripped = head.lstrip()
    for magic, kind in MAGIC_TYPES:
        if head.startswith(magic) or stripped.startswith(magic):
            return kind
    return "other"


def fetch_file(
    session: requests.Session,
    url: str,
    dest: Path,
    *,
    max_bytes: int,
    min_size_bytes: int = 1024,
    timeout: int = 300,
    retry_attempts: int = 3,
    retry_backoff: float = 1.5,
) -> FetchResult:
    last_error: str = ""
    for attempt in range(retry_attempts):
        try:
            return _fetch_once(
                session, url, dest,
                max_bytes=max_bytes, min_size_bytes=min_size_bytes, timeout=timeout,
            )
        except requests.RequestException as e:
            last_error = str(e)
            dest.unlink(missing_ok=True)
            if attempt < retry_attempts - 1:
                time.sleep(retry_backoff * (attempt + 1))
    return FetchResult(
        ok=False, path=None, sniffed_type=None, bytes=0, content_hash=None,
        validated_magic=False, validated_eof=False, validated_min_size=False,
        error=f"download failed after {retry_attempts} attempts: {last_error}",
        retryable=True,
    )


def _fetch_once(
    session: requests.Session,
    url: str,
    dest: Path,
    *,
    max_bytes: int,
    min_size_bytes: int,
    timeout: int,
) -> FetchResult:
    with session.get(url, stream=True, timeout=timeout) as resp:
        if resp.status_code != 200:
            return FetchResult(
                ok=False, path=None, sniffed_type=None, bytes=0, content_hash=None,
                validated_magic=False, validated_eof=False, validated_min_size=False,
                error=f"HTTP {resp.status_code}",
                retryable=resp.status_code in (429, 500, 502, 503, 504),
            )
        content_length = resp.headers.get("Content-Length")
        if content_length and int(content_length) > max_bytes:
            # Refused before downloading: bytes=0 so accounting charges nothing.
            return FetchResult(
                ok=False, path=None, sniffed_type=None,
                bytes=0, content_hash=None,
                validated_magic=False, validated_eof=False, validated_min_size=False,
                error=f"oversize: Content-Length {int(content_length)} > cap {max_bytes}",
            )

        sha = hashlib.sha256()
        size = 0
        head = b""
        tail = b""
        try:
            with open(dest, "wb") as f:
                for chunk in resp.iter_content(CHUNK):
                    size += len(chunk)
                    if size > max_bytes:
                        raise _Oversize()
                    sha.update(chunk)
                    if len(head) < 8:
                        head += chunk[: 8 - len(head)]
                    tail = (tail + chunk)[-1024:]
                    f.write(chunk)
        except _Oversize:
            dest.unlink(missing_ok=True)
            return FetchResult(
                ok=False, path=None, sniffed_type=None, bytes=size, content_hash=None,
                validated_magic=False, validated_eof=False, validated_min_size=False,
                error=f"oversize: stream exceeded cap {max_bytes} mid-download",
            )

    sniffed = sniff_type(head)
    magic_ok = sniffed != "other"
    eof_ok = sniffed != "pdf" or b"%%EOF" in tail
    size_ok = size >= min_size_bytes

    if sniffed == "pdf" and not eof_ok:
        dest.unlink(missing_ok=True)  # truncated download: delete so a re-run re-fetches
        return FetchResult(
            ok=False, path=None, sniffed_type=sniffed, bytes=size, content_hash=None,
            validated_magic=magic_ok, validated_eof=False, validated_min_size=size_ok,
            error="truncated PDF (no %%EOF in final 1KB)", retryable=True,
        )
    if not size_ok:
        dest.unlink(missing_ok=True)  # placeholders must never pass as done
        return FetchResult(
            ok=False, path=None, sniffed_type=sniffed, bytes=size, content_hash=None,
            validated_magic=magic_ok, validated_eof=eof_ok, validated_min_size=False,
            error=f"below min size ({size} < {min_size_bytes} bytes)", retryable=True,
        )

    return FetchResult(
        ok=True, path=dest, sniffed_type=sniffed, bytes=size,
        content_hash=f"sha256:{sha.hexdigest()}",
        validated_magic=magic_ok, validated_eof=eof_ok, validated_min_size=True,
    )


class _Oversize(Exception):
    pass
