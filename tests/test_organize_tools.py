"""Unir, dividir, alternar y extraer."""

import pymupdf
import pytest

from blackpdf.core.alternate import alternate_pdfs, interleave_order
from blackpdf.core.errors import EmptyInputError, InvalidRangeError
from blackpdf.core.merge import merge_pdfs
from blackpdf.core.split import extract_pages, split_by_ranges, split_each_page, split_every

from .conftest import page_labels


def test_merge_keeps_order_and_adds_bookmarks(make_pdf, tmp_path):
    a = make_pdf(pages=2, prefix="A", name="primero.pdf")
    b = make_pdf(pages=1, prefix="B", name="segundo.pdf")
    output = tmp_path / "unido.pdf"
    steps = []
    merge_pdfs([b, a], output, progress=lambda d, t: steps.append((d, t)))
    doc = pymupdf.open(output)
    assert [p.get_text().split()[0] for p in doc] == ["B1", "A1", "A2"]
    assert doc.get_toc() == [[1, "segundo", 1], [1, "primero", 2]]
    assert steps and steps[-1][0] == steps[-1][1]


def test_merge_nests_existing_bookmarks(make_pdf, tmp_path):
    a = make_pdf(pages=2, name="libro.pdf")
    doc = pymupdf.open(a)
    doc.set_toc([[1, "Capítulo", 2]])
    doc.saveIncr()
    doc.close()
    output = tmp_path / "unido.pdf"
    merge_pdfs([make_pdf(pages=1, name="portada.pdf"), a], output)
    assert pymupdf.open(output).get_toc() == [[1, "portada", 1], [1, "libro", 2], [2, "Capítulo", 3]]


def test_merge_without_bookmarks(make_pdf, tmp_path):
    output = tmp_path / "unido.pdf"
    merge_pdfs([make_pdf(), make_pdf()], output, add_bookmarks=False)
    assert pymupdf.open(output).get_toc() == []


def test_merge_requires_files(tmp_path):
    with pytest.raises(EmptyInputError):
        merge_pdfs([], tmp_path / "x.pdf")


def test_split_by_ranges(make_pdf, tmp_path):
    source = make_pdf(pages=10, name="informe.pdf")
    outputs = split_by_ranges(source, "1-3,5,8-10", tmp_path / "partes")
    assert [p.name for p in outputs] == ["informe_1-3.pdf", "informe_5.pdf", "informe_8-10.pdf"]
    assert page_labels(outputs[0]) == ["P1", "P2", "P3"]
    assert page_labels(outputs[2]) == ["P8", "P9", "P10"]


def test_split_invalid_range(make_pdf, tmp_path):
    with pytest.raises(InvalidRangeError):
        split_by_ranges(make_pdf(pages=3), "2-9", tmp_path)


def test_split_every(make_pdf, tmp_path):
    outputs = split_every(make_pdf(pages=7, name="a.pdf"), 3, tmp_path / "out")
    assert [len(pymupdf.open(p)) for p in outputs] == [3, 3, 1]
    assert outputs[-1].name == "a_7.pdf"


def test_split_each_page_does_not_overwrite(make_pdf, tmp_path):
    source = make_pdf(pages=2, name="b.pdf")
    first = split_each_page(source, tmp_path / "out")
    second = split_each_page(source, tmp_path / "out")
    assert [p.name for p in first] == ["b_1.pdf", "b_2.pdf"]
    assert [p.name for p in second] == ["b_1 (2).pdf", "b_2 (2).pdf"]


def test_extract_pages(make_pdf, tmp_path):
    output = tmp_path / "extraido.pdf"
    extract_pages(make_pdf(pages=5), "5,1-2", output)
    assert page_labels(output) == ["P5", "P1", "P2"]


def test_interleave_order():
    assert interleave_order(3, 3) == [0, 3, 1, 4, 2, 5]
    assert interleave_order(3, 3, reverse_b=True) == [0, 5, 1, 4, 2, 3]
    assert interleave_order(3, 1) == [0, 3, 1, 2]
    assert interleave_order(1, 3) == [0, 1, 2, 3]


def test_alternate_pdfs_for_double_sided_scan(make_pdf, tmp_path):
    fronts = make_pdf(pages=3, prefix="F")
    backs = make_pdf(pages=3, prefix="B")
    output = tmp_path / "alternado.pdf"
    alternate_pdfs(fronts, backs, output, reverse_b=True)
    texts = [p.get_text().split()[0] for p in pymupdf.open(output)]
    assert texts == ["F1", "B3", "F2", "B2", "F3", "B1"]


def test_extract_pages_separately(make_pdf, tmp_path):
    from blackpdf.core.split import extract_pages_separately

    source = make_pdf(pages=12, name="informe.pdf")
    every = extract_pages_separately(source, "", tmp_path / "todas")
    assert len(every) == 12 and every[0].name == "informe_1.pdf" and every[-1].name == "informe_12.pdf"
    assert all(len(pymupdf.open(p)) == 1 for p in every) and page_labels(every[4]) == ["P5"]

    chosen = extract_pages_separately(source, "5, 1-2", tmp_path / "algunas")
    assert [p.name for p in chosen] == ["informe_5.pdf", "informe_1.pdf", "informe_2.pdf"]

    ranges = extract_pages_separately(source, "1-3, 5-6", tmp_path / "rangos", each="range")
    assert [p.name for p in ranges] == ["informe_1-3.pdf", "informe_5-6.pdf"]
    assert page_labels(ranges[0]) == ["P1", "P2", "P3"] and page_labels(ranges[1]) == ["P5", "P6"]

    with pytest.raises(InvalidRangeError):
        extract_pages_separately(source, "", tmp_path / "nada", each="range")
    with pytest.raises(InvalidRangeError):
        extract_pages_separately(source, "13", tmp_path / "fuera")
