"""Bulk-archive router: classification, OOXML office extraction, member fan-out.

Pure (no-DB) coverage of engine/extract/archive.py + office.py. The resolver's
DB half (engine/archive_resolve.py) is exercised live against the real
zip_dossier queue, as the drain's is (see test_drain.py)."""

import zipfile

from engine.extract import archive, office

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
CT = "[Content_Types].xml"


def _zip(path, members: dict[str, str | bytes]):
    with zipfile.ZipFile(path, "w") as z:
        for name, data in members.items():
            z.writestr(name, data)
    return path


def _docx(path, paras):
    body = "".join(
        f'<w:p><w:r><w:t>{t}</w:t></w:r></w:p>' for t in paras
    )
    doc = f'<w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>'
    return _zip(path, {CT: "<x/>", "word/document.xml": doc})


def _xlsx(path, rows):
    shared = sorted({c for r in rows for c in r})
    sst = "".join(f"<si><t>{s}</t></si>" for s in shared)
    idx = {s: i for i, s in enumerate(shared)}
    sheet_rows = "".join(
        "<row>" + "".join(
            f'<c t="s"><v>{idx[c]}</v></c>' for c in r
        ) + "</row>"
        for r in rows
    )
    return _zip(path, {
        CT: "<x/>",
        "xl/sharedStrings.xml": f'<sst xmlns="{S}">{sst}</sst>',
        "xl/worksheets/sheet1.xml": f'<worksheet xmlns="{S}"><sheetData>{sheet_rows}</sheetData></worksheet>',
    })


def _pptx(path, slides):
    files = {CT: "<x/>"}
    for i, lines in enumerate(slides, 1):
        runs = "".join(f'<a:p><a:r><a:t>{t}</a:t></a:r></a:p>' for t in lines)
        P = "http://schemas.openxmlformats.org/presentationml/2006/main"
        files[f"ppt/slides/slide{i}.xml"] = f'<p:sld xmlns:p="{P}" xmlns:a="{A}"><p:cSld>{runs}</p:cSld></p:sld>'
    return _zip(path, files)


# ---- classification ----

def test_classify_docx(tmp_path):
    assert archive.classify(_docx(tmp_path / "d.zip", ["hi"])) == "docx"


def test_classify_xlsx(tmp_path):
    assert archive.classify(_xlsx(tmp_path / "x.zip", [["a"]])) == "xlsx"


def test_classify_pptx(tmp_path):
    assert archive.classify(_pptx(tmp_path / "p.zip", [["a"]])) == "pptx"


def test_classify_document_archive(tmp_path):
    p = _zip(tmp_path / "a.zip", {"0001_doc.pdf": b"%PDF-1.4", "0002_x.pdf": b"%PDF-1.4"})
    assert archive.classify(p) == "document_archive"


def test_classify_ooxml_marker_beats_pdf_members(tmp_path):
    # A DOCX that happens to embed a PDF must still route as a single document.
    p = _zip(tmp_path / "d.zip", {CT: "<x/>", "word/document.xml": f'<w:document xmlns:w="{W}"><w:body/></w:document>', "word/embeddings/o.pdf": b"%PDF"})
    assert archive.classify(p) == "docx"


def test_classify_empty_and_corrupt(tmp_path):
    empty = _zip(tmp_path / "e.zip", {"__MACOSX/": "", "sub/": ""})
    assert archive.classify(empty) == "empty"
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not a zip at all")
    assert archive.classify(bad) == "corrupt"


# ---- OOXML text extraction ----

def test_docx_text_joins_paragraphs(tmp_path):
    ex = office.extract(_docx(tmp_path / "d.zip", ["Eerste alinea.", "Tweede alinea."]), "docx")
    assert ex.kind == "docx" and ex.confidence == 1.0 and ex.units == 2
    assert "Eerste alinea." in ex.text and "Tweede alinea." in ex.text


def test_xlsx_resolves_shared_strings_by_row(tmp_path):
    ex = office.extract(_xlsx(tmp_path / "x.zip", [["Naam", "Bedrag"], ["Alice", "100"]]), "xlsx")
    assert ex.kind == "xlsx" and ex.units == 2
    assert "Naam\tBedrag" in ex.text and "Alice" in ex.text


def test_pptx_text_per_slide(tmp_path):
    ex = office.extract(_pptx(tmp_path / "p.zip", [["Titel"], ["Punt een", "Punt twee"]]), "pptx")
    assert ex.kind == "pptx" and ex.units == 2
    assert "Titel" in ex.text and "Punt twee" in ex.text


def test_docx_empty_body_zero_confidence(tmp_path):
    p = _zip(tmp_path / "d.zip", {CT: "<x/>", "word/document.xml": f'<w:document xmlns:w="{W}"><w:body/></w:document>'})
    ex = office.extract(p, "docx")
    assert ex.text == "" and ex.confidence == 0.0


# ---- member enumeration ----

def test_members_index_routable_only_and_skip_junk(tmp_path):
    p = _zip(tmp_path / "a.zip", {
        "__MACOSX/._x": "junk", "sub/": "", ".DS_Store": "x",
        "0001_a.pdf": b"%PDF", "notes.txt": "x", "0002_b.pdf": b"%PDF",
    })
    mems = archive.members(p)
    routable = [m for m in mems if m.routable]
    assert [m.name for m in routable] == ["0001_a.pdf", "0002_b.pdf"]
    assert [m.index for m in routable] == [0, 1]           # dense over routable only
    assert any(m.ext == "txt" and not m.routable for m in mems)
    assert not any(m.name.startswith("__MACOSX") for m in mems)


def test_extract_member_writes_bytes(tmp_path):
    p = _zip(tmp_path / "a.zip", {"0001_a.pdf": b"%PDF-1.4 body"})
    dest = tmp_path / "out.bin"
    n = archive.extract_member(p, "0001_a.pdf", dest)
    assert dest.read_bytes() == b"%PDF-1.4 body" and n == len(b"%PDF-1.4 body")


# ---- member display title cleanup (mirrors the observed woo naming) ----

def test_display_title_strips_prefix_and_doubled_ext():
    from engine.archive_resolve import _display_title
    assert _display_title("0011_LOB C2018005682.pdf.pdf") == "LOB C2018005682.pdf"
    assert _display_title("sub/dir/0003_Rapport.pdf") == "Rapport.pdf"
    assert _display_title("Gewoon.pdf") == "Gewoon.pdf"
