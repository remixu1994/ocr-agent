"""Bounded, plain-data output for non-medical document workloads."""

from itertools import islice

from .parse import Cells

MAX_TEXT = 400
MAX_TEXT_LENGTH = 400
MAX_TABLES = 8
MAX_ROWS_PER_TABLE = 40
MAX_COLUMNS = 16
MAX_CELL_LENGTH = 120


def bounded_box(raw, width: int, height: int) -> list[int] | None:
    try:
        x1, y1, x2, y2 = map(int, raw)
    except (TypeError, ValueError, OverflowError):
        return None
    if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
        return None
    return [x1, y1, x2, y2]


def document_payload(page: dict, width: int, height: int) -> dict:
    overall = page.get("overall_ocr_res") or {}
    words = overall.get("rec_texts") or []
    boxes = overall.get("rec_boxes") or []
    tables = page.get("table_res_list") or []
    partial = len(words) > MAX_TEXT or len(tables) > MAX_TABLES
    text_blocks = []
    for index, word in enumerate(islice(words, MAX_TEXT)):
        if not isinstance(word, str) or not word.strip():
            continue
        word = word.strip()
        partial |= len(word) > MAX_TEXT_LENGTH
        raw_box = boxes[index] if index < len(boxes) else None
        text_blocks.append({"text": word[:MAX_TEXT_LENGTH],
                            "box": bounded_box(raw_box, width, height)})
    table_blocks = []
    for table in islice(tables, MAX_TABLES):
        html = table.get("pred_html")
        if not isinstance(html, str) or len(html) > 150_000:
            partial = True
            continue
        cells = Cells()
        cells.feed(html)
        cells.close()
        if cells.spanned_table:
            # Don't pretend a table with merged cells is a rectangular grid.
            partial = True
            continue
        rows = []
        partial |= len(cells.rows) > MAX_ROWS_PER_TABLE
        for row in cells.rows[:MAX_ROWS_PER_TABLE]:
            partial |= len(row) > MAX_COLUMNS
            partial |= any(len(cell) > MAX_CELL_LENGTH for cell in row[:MAX_COLUMNS])
            rows.append([cell[:MAX_CELL_LENGTH] for cell in row[:MAX_COLUMNS]])
        if rows:
            table_blocks.append({"rows": rows})
    return {
        "schema_version": 1,
        "engine": "PP-StructureV3",
        "image": {"width": width, "height": height},
        "text_blocks": text_blocks,
        "tables": table_blocks,
        "partial": partial,
    }
