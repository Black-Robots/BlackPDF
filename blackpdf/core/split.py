"""Dividir un PDF en varios y extraer páginas."""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from pathlib import Path

from .document import PdfSource, open_pdf, save_pdf, source_stem
from .errors import EmptyInputError, InvalidOptionError
from .files import safe_filename, unique_path
from .pages import extract_document
from .progress import ProgressCallback, report
from .ranges import chunk_indices, format_page_ranges, parse_page_ranges, parse_page_selection


def _group_label(group: Sequence[int]) -> str:
    if len(group) == 1:
        return str(group[0] + 1)
    first, last = group[0] + 1, group[-1] + 1
    if list(group) == list(range(group[0], group[-1] + 1)):
        return f"{first}-{last}"
    if list(group) == list(range(group[0], group[-1] - 1, -1)):
        return f"{first}-{last}"
    return format_page_ranges(group).replace(",", "_")


def _write_groups(
    source: PdfSource,
    groups_for: Callable[[int], list[list[int]]],
    out_dir: str | os.PathLike,
    base_name: str | None,
    password: str | None,
    progress: ProgressCallback | None,
) -> list[Path]:
    doc = open_pdf(source, password)
    try:
        groups = groups_for(doc.page_count)
        if not groups:
            raise EmptyInputError("no hay páginas que dividir")
        folder = Path(out_dir)
        base = safe_filename(base_name or source_stem(source))
        outputs: list[Path] = []
        for position, group in enumerate(groups):
            report(progress, position, len(groups))
            part = extract_document(doc, group)
            try:
                target = unique_path(folder / f"{base}_{_group_label(group)}.pdf")
                outputs.append(save_pdf(part, target, garbage=4))
            finally:
                part.close()
        report(progress, len(groups), len(groups))
        return outputs
    finally:
        doc.close()


def split_by_ranges(
    source: PdfSource,
    spec: str,
    out_dir: str | os.PathLike,
    *,
    base_name: str | None = None,
    password: str | None = None,
    progress: ProgressCallback | None = None,
) -> list[Path]:
    """Un archivo por cada rango: "1-3,5,8-10" -> 3 archivos."""
    return _write_groups(
        source, lambda count: parse_page_ranges(spec, count), out_dir, base_name, password, progress
    )


def split_every(
    source: PdfSource,
    size: int,
    out_dir: str | os.PathLike,
    *,
    base_name: str | None = None,
    password: str | None = None,
    progress: ProgressCallback | None = None,
) -> list[Path]:
    """Un archivo cada `size` páginas."""
    if size < 1:
        raise InvalidOptionError("el tamaño debe ser al menos 1", option="size")
    return _write_groups(
        source, lambda count: chunk_indices(count, size), out_dir, base_name, password, progress
    )


def split_each_page(
    source: PdfSource,
    out_dir: str | os.PathLike,
    *,
    base_name: str | None = None,
    password: str | None = None,
    progress: ProgressCallback | None = None,
) -> list[Path]:
    """Un archivo por página."""
    return _write_groups(
        source, lambda count: chunk_indices(count, 1), out_dir, base_name, password, progress
    )


def extract_pages(
    source: PdfSource,
    spec: str,
    output: str | os.PathLike,
    *,
    password: str | None = None,
    progress: ProgressCallback | None = None,
) -> Path:
    """Crea un PDF solo con las páginas indicadas, en el orden escrito."""
    doc = open_pdf(source, password)
    try:
        report(progress, 0, 2)
        indices = parse_page_selection(spec, doc.page_count)
        part = extract_document(doc, indices)
        try:
            report(progress, 1, 2)
            path = save_pdf(part, output, garbage=4)
        finally:
            part.close()
        report(progress, 2, 2)
        return path
    finally:
        doc.close()


EXTRACT_EACH = ("page", "range")


def extract_pages_separately(
    source: PdfSource,
    spec: str,
    out_dir: str | os.PathLike,
    *,
    each: str = "page",
    base_name: str | None = None,
    password: str | None = None,
    progress: ProgressCallback | None = None,
) -> list[Path]:
    """Extrae las páginas indicadas en varios PDF: uno por página o uno por rango.

    Con ``each="page"``, "1-3,5" da cuatro archivos (y vacío, uno por cada página del
    documento); con ``each="range"``, dos: uno de la 1 a la 3 y otro con la 5.
    """
    if each not in EXTRACT_EACH:
        raise InvalidOptionError(each, option="each")
    if each == "range":
        groups_for = lambda count: parse_page_ranges(spec, count)  # noqa: E731
    else:
        groups_for = lambda count: [[i] for i in parse_page_selection(spec, count, allow_empty_all=True)]  # noqa: E731
    return _write_groups(source, groups_for, out_dir, base_name, password, progress)
