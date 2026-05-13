from __future__ import annotations

import csv
import html
import re
import unicodedata
from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd

from .schemas import Classroom


def fold_vi(s: str) -> str:
    s = unicodedata.normalize("NFD", (s or "").strip().lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def fold_teacher_cell(raw: str) -> str:
    """Chuẩn hoá tên GV giống ô Excel (bỏ nội dung trong ngoặc)."""
    s = re.sub(r"\(.*?\)", "", str(raw).strip())
    return fold_vi(s)


def norm_subject_code(code: str) -> str:
    return re.sub(r"\s+", "", str(code).strip().upper().replace("_", ""))


def clean_cell(cell_html: str) -> str:
    text = re.sub(r"<[^>]+>", "", cell_html)
    text = html.unescape(text).replace("\xa0", "").strip()
    return text


def parse_projects_room_type(cell: str) -> int:
    """
    Khoa CNTT: chỉ phân LT vs không-LT.
    LT-* trong catalog → phòng lý thuyết (1).
    Mọi mã khác (TH, PTH, K, ô trống, …) → coi là thực hành, ghép phòng TH trong rooms.csv (2).
    """
    raw = (cell or "").replace("\xa0", "").strip()
    if not raw:
        return 2
    prefix = raw.upper().split("-")[0].strip()
    if prefix.startswith("LT"):
        return 1
    return 2


def parse_projects_catalog(path: Path) -> Dict[str, int]:
    """Đọc projects.xls (HTML). Map mã môn chuẩn hoá -> classroom_type (1=LT, 2=TH cho CNTT)."""
    raw = path.read_text(encoding="utf-8", errors="ignore")
    tr_pat = re.compile(r"<tr\s+class=['\"]body['\"]>(.*?)</tr>", re.S | re.I)
    td_pat = re.compile(r"<td[^>]*>(.*?)</td>", re.S | re.I)
    out: Dict[str, int] = {}
    for tr in tr_pat.findall(raw):
        tds = [clean_cell(td) for td in td_pat.findall(tr)]
        if len(tds) < 13:
            continue
        code = norm_subject_code(tds[1])
        room_cell = tds[12]
        out[code] = parse_projects_room_type(room_cell)
    return out


def load_classrooms_from_csv(path: Path) -> List[Classroom]:
    rooms: List[Classroom] = []
    typ_map = {"LT": 1, "TH": 2, "X": 3, "K": 4}
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if int(row.get("in_use", 0) or 0) != 1:
                continue
            label = (row.get("room_label") or "").strip()
            note = (row.get("note") or "").strip()
            blob = f"{label} {note}".lower()
            if "ảo" in blob or " ao)" in blob:
                continue
            raw_id = (row.get("room_name") or "").strip()
            if raw_id.isdigit():
                rid = int(raw_id)
            else:
                rid = abs(hash(label)) % (10**9)
            code = (row.get("room_property_code") or "LT").strip().upper()
            rtype = typ_map.get(code, 1)
            cap_raw = (row.get("capacity") or "").strip()
            capacity = int(cap_raw) if cap_raw.isdigit() else None
            rooms.append(
                Classroom(
                    id=rid,
                    name=label or raw_id,
                    capacity=capacity,
                    type=rtype,
                    type_code=code,
                    campus_code=(row.get("campus_code") or "").strip() or None,
                )
            )
    return rooms


def load_teacher_aliases(path: Path) -> Dict[str, str]:
    """Optional: cleans/teacher_aliases.csv columns name,teacher_id (ghi đè/ghép sau teachers.csv)."""
    if not path.is_file():
        return {}
    out: Dict[str, str] = {}
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = (row.get("name") or row.get("teacher_name") or "").strip()
            tid = (row.get("teacher_id") or "").strip()
            if not name or not tid:
                continue
            out[fold_teacher_cell(name)] = tid
    return out


def load_teacher_display_map(path: Path) -> Dict[str, str]:
    """teacher_id -> họ tên (để xuất TKB)."""
    out: Dict[str, str] = {}
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            tid = (row.get("teacher_id") or "").strip()
            name = (row.get("teacher_name") or "").strip()
            if tid:
                out[tid] = name or tid
    return out


def load_teacher_lookup(path: Path) -> Dict[str, str]:
    """fold(full_name) -> teacher_id (vd CT112)."""
    out: Dict[str, str] = {}
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            tid = (row.get("teacher_id") or "").strip()
            name = (row.get("teacher_name") or "").strip()
            if not tid or not name:
                continue
            key = fold_teacher_cell(name)
            if key in out and out[key] != tid:
                pass
            out.setdefault(key, tid)
    return out


def load_class_group_ids(path: Path) -> Dict[str, int]:
    """Tên lớp -> edu_course_id (dùng làm class_group_id)."""
    out: Dict[str, int] = {}
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = (row.get("name") or "").strip()
            eid = (row.get("edu_course_id") or "").strip()
            if name and eid.isdigit():
                out[name] = int(eid)
    return out


def week_bounds_from_csv(path: Path) -> Tuple[int, int]:
    orders: List[int] = []
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            o = (row.get("week_order") or "").strip()
            if o.isdigit():
                orders.append(int(o))
    if not orders:
        raise ValueError(f"No week_order in {path}")
    return min(orders), max(orders)


def load_classes_project(path: Path) -> pd.DataFrame:
    return pd.read_excel(path, engine="openpyxl")
