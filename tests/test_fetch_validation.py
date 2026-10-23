"""Fetch validation: truncated PDFs, masquerading payloads, size caps, placeholders."""

from contextlib import contextmanager

import pytest

from engine.fetch import fetch_file, sniff_type


class FakeResponse:
    def __init__(self, body: bytes, status: int = 200, headers: dict | None = None):
        self.body = body
        self.status_code = status
        self.headers = headers or {}

    def iter_content(self, chunk_size):
        for i in range(0, len(self.body), chunk_size):
            yield self.body[i : i + chunk_size]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeSession:
    def __init__(self, response: FakeResponse):
        self.response = response

    def get(self, url, stream=True, timeout=None):
        return self.response


def make_pdf(size: int = 4096, truncated: bool = False) -> bytes:
    body = b"%PDF-1.7\n" + b"x" * size
    if not truncated:
        body += b"\n%%EOF\n"
    return body


def fetch(tmp_path, body: bytes, **kw):
    session = FakeSession(FakeResponse(body, headers=kw.pop("headers", {})))
    return fetch_file(
        session, "http://example/file", tmp_path / "blob.bin",
        max_bytes=kw.pop("max_bytes", 10_000_000),
        min_size_bytes=kw.pop("min_size_bytes", 1024),
        retry_attempts=1,
    )


def test_valid_pdf_passes_all_validations(tmp_path):
    r = fetch(tmp_path, make_pdf())
    assert r.ok and r.sniffed_type == "pdf"
    assert r.validated_magic and r.validated_eof and r.validated_min_size
    assert r.content_hash.startswith("sha256:")
    assert (tmp_path / "blob.bin").exists()


def test_truncated_pdf_is_rejected_retryable_and_deleted(tmp_path):
    r = fetch(tmp_path, make_pdf(truncated=True))
    assert not r.ok and r.retryable
    assert "truncated" in r.error
    assert not (tmp_path / "blob.bin").exists()  # deleted so a re-run re-fetches


def test_html_masquerading_as_pdf_detected_by_magic_not_extension(tmp_path):
    r = fetch(tmp_path, b"<!DOCTYPE html><html>error page</html>" + b" " * 2000)
    assert r.ok  # download itself succeeded...
    assert r.sniffed_type == "html"  # ...but it is NOT a pdf (magic, not URL)


def test_zip_detected(tmp_path):
    r = fetch(tmp_path, b"PK\x03\x04" + b"\x00" * 2000)
    assert r.ok and r.sniffed_type == "zip"


def test_placeholder_below_min_size_never_passes_as_done(tmp_path):
    r = fetch(tmp_path, make_pdf(size=10))
    assert not r.ok and not r.validated_min_size and r.retryable
    assert not (tmp_path / "blob.bin").exists()


def test_content_length_precheck_stops_before_download(tmp_path):
    r = fetch(tmp_path, make_pdf(), headers={"Content-Length": "99999999999"})
    assert not r.ok and "oversize" in r.error
    assert not (tmp_path / "blob.bin").exists()


def test_mid_stream_cap_aborts_and_cleans_up(tmp_path):
    r = fetch(tmp_path, make_pdf(size=5000), max_bytes=2000)
    assert not r.ok and "mid-download" in r.error
    assert not (tmp_path / "blob.bin").exists()


def test_sniff_type_table():
    assert sniff_type(b"%PDF-1.4") == "pdf"
    assert sniff_type(b"PK\x03\x04ab") == "zip"
    assert sniff_type(b'  {"a": 1}') == "json"
    assert sniff_type(b"<html>") == "html"
    assert sniff_type(b"\x00\x01\x02\x03") == "other"
