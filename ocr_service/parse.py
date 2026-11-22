"""Translate PP-StructureV3 output into bounded, unconfirmed report fields."""

import re
from datetime import date
from html.parser import HTMLParser
from statistics import median

MAX_ROWS = 200
VALUE = re.compile(
    r"(?:[<>≤≥]=?\s*)?-?\d+(?:\.\d+)?|"
    r"(?:[1-4]\+|\+{1,4}|±)(?: \d+(?:\.\d+)?)?|"
    r"[-−—]|阴性|阳性|正常|normal|neg|pos|浅黄色|清亮", re.I,
)
DATE = r"(\d{4})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})日?"
DATE_FIELD = re.compile(
    r"(采样|采集|采血|报告|审核)(?:日期|时间)\s*[:：]?\s*" + DATE
    + r"(?:\s+(\d{1,2})\s*:\s*(\d{2}))?"
)
HEADINGS = {
    "项目名称": "name", "检验项目": "name", "项目": "name",
    "项目缩写": "code", "缩写": "code",
    "结果": "value", "检验结果": "value",
    "单位": "unit", "参考区间": "reference", "参考范围": "reference",
}
REQUIRED = {"name", "value", "unit", "reference"}


def compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


class Cells(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self.row: list[str] | None = None
        self.cell: list[str] | None = None
        self.merged = False
        self.spanned_table = False

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.row, self.cell, self.merged = [], None, False
        if tag in ("td", "th") and self.row is not None:
            self.cell = []
            self.merged |= any(
                key in ("rowspan", "colspan") and val != "1" for key, val in attrs
            )
            self.spanned_table |= self.merged

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.row is not None and self.cell is not None:
            self.row.append("".join(self.cell).strip())
            self.cell = None
        if tag == "tr" and self.row is not None:
            if self.row and not self.merged:
                self.rows.append(self.row)
            self.row, self.cell = None, None


def candidates(fields: dict[str, str], needs_review: bool) -> list[dict]:
    name = re.sub(r"^\s*\d+\s*", "", fields["name"])
    name = re.sub(r"^[\s*＊★]+|[√✓✔\s]+$", "", name)
    code = fields.get("code", "").strip()
    raw_result = fields["value"]
    raw_unit = fields["unit"]
    flag = re.search("[↑↓]", raw_result + raw_unit)
    value = " ".join(re.sub("[↑↓]", "", raw_result).split()).translate(
        str.maketrans("＜＞＋", "<>+")
    )
    value = re.sub(r"\s*\.\s*", ".", value)
    value = re.sub(r"(?<=\d)\s+\+", "+", value)
    unit = re.sub(r"[↑↓\s]", "", raw_unit)
    ref = fields["reference"].strip()
    if (not name or re.search(r"采样|采集|收样|报告时间|打印|声明|条码", name) or
            any(len(s) > limit for s, limit in [
                (name, 100), (code, 40), (value, 100), (unit, 40), (ref, 160),
            ])):
        return []
    fields_out = {
        "name": name, "code": code, "result": value, "unit": unit,
        "reference": ref, "flag": flag[0] if flag else "",
        "requires_review": needs_review,
    }
    pair = re.fullmatch(r"(\d{1,3})\s*[/／]\s*(\d{1,3})", value)
    if (pair and compact(name).lower() in {"血压", "动脉血压", "bp"}
            and unit.lower() == "mmhg"):
        high, low = map(int, pair.groups())
        return [
            fields_out | {
                "name": label, "code": alias, "result": number, "reference": "",
                "requires_review": needs_review or not 1 <= low < high <= 400,
            }
            for label, alias, number in [
                ("收缩压", "SBP", pair[1]), ("舒张压", "DBP", pair[2]),
            ]
        ]
    return [fields_out] if VALUE.fullmatch(value) else []


def table_candidates(tables: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for table in tables:
        html = table.get("pred_html")
        if not isinstance(html, str) or len(html) > 150_000:
            continue
        parser = Cells()
        parser.feed(html)
        # Row/column spans can shift cell indices across rows.
        if parser.spanned_table:
            continue
        fields, width = {}, 0
        for cells in parser.rows:
            headings = {
                HEADINGS[compact(cell)]: i
                for i, cell in enumerate(cells) if compact(cell) in HEADINGS
            }
            if REQUIRED <= headings.keys():
                fields, width = headings, len(cells)
            elif fields and len(cells) == width:
                rows.extend(candidates({key: cells[i] for key, i in fields.items()}, False))
                if len(rows) >= MAX_ROWS:
                    return rows[:MAX_ROWS]
    return rows


def spatial_candidates(ocr: dict) -> list[dict]:
    """Conservative fallback for borderless forms: require matching headers."""
    words = []
    for raw, box in zip(ocr.get("rec_texts", []), ocr.get("rec_boxes", [])):
        if isinstance(raw, str) and raw.strip() and len(box) == 4:
            left, top, right, bottom = map(float, box)
            if right > left and bottom > top:
                words.append((raw, (left + right) / 2, (top + bottom) / 2, bottom - top))
    if not words:
        return []
    bands: list[list[tuple]] = []
    tolerance = max(3, median(w[3] for w in words) * .57)
    for word in sorted(words, key=lambda w: w[2]):
        if not bands or word[2] - median(w[2] for w in bands[-1]) > tolerance:
            bands.append([])
        bands[-1].append(word)
    columns: list[tuple[str, float]] = []
    rows: list[dict] = []
    for band in bands:
        band.sort(key=lambda w: w[1])
        headers = [(HEADINGS[compact(t)], x) for t, x, _, _ in band if compact(t) in HEADINGS]
        if REQUIRED <= {n for n, _ in headers}:
            order = [n for n, _ in headers]
            if order in [
                ["name", "value", "unit", "reference"],
                ["name", "code", "value", "unit", "reference"],
            ]:
                columns = headers
            continue
        if not columns:
            continue
        buckets = {key: [] for key, _ in columns}
        boundaries = [(left[1] + right[1]) / 2 for left, right in zip(columns, columns[1:])]
        for word, x, _, _ in band:
            index = sum(x >= limit for limit in boundaries)
            buckets[columns[index][0]].append(word)
        rows.extend(candidates({key: " ".join(parts) for key, parts in buckets.items()}, True))
        if len(rows) >= MAX_ROWS:
            break
    return rows[:MAX_ROWS]


def report_payload(result: dict) -> dict:
    overall = result.get("overall_ocr_res") or {}
    lines = "\n".join(s for s in overall.get("rec_texts", []) if isinstance(s, str))
    specimen = "unknown"
    found = re.search(r"(?:标本|样本)(?:类型)?\s*[:：]?\s*(尿液|尿|血液|全血|血清|血浆)", lines)
    if found:
        specimen = "urine" if found[1].startswith("尿") else "blood"

    day, source, sample_time = None, "manual", None
    matches = list(DATE_FIELD.finditer(lines))
    matches.sort(key=lambda m: m[1] in ("报告", "审核"))
    for matched in matches:
        try:
            day = date(*(int(matched[i]) for i in (2, 3, 4))).isoformat()
        except ValueError:
            continue
        source = "report" if matched[1] in ("报告", "审核") else "sample"
        if source == "sample" and matched[5] is not None:
            hour, minute = int(matched[5]), int(matched[6])
            if hour < 24 and minute < 60:
                sample_time = f"{hour:02}:{minute:02}"
        break

    rows = table_candidates(result.get("table_res_list") or [])
    strategy = "table"
    if not rows:
        rows = spatial_candidates(overall)
        strategy = "spatial" if rows else "none"
    return {
        "schema_version": 1, "engine": "PP-StructureV3",
        "strategy": strategy, "specimen": specimen, "date": day,
        "date_source": source, "sample_time": sample_time, "rows": rows,
    }
