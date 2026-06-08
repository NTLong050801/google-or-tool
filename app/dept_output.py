"""Helpers ghi output theo từng khoa: assignment_log.xlsx, warnings.txt."""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import Dict, List


def split_warnings_by_dept(
    warnings: List[str],
    dept_codes: List[str],
) -> Dict[str, List[str]]:
    """Chia warnings ra theo khoa.

    Cảnh báo có gắn mã khoa (vd "[assign] cntt/cao_dang ..." hay "[cntt] ...")
    → đẩy vào khoa đó. Cảnh báo chung (không gắn khoa) → thêm vào tất cả khoa.
    """
    if not dept_codes:
        return {}
    by_dept: Dict[str, List[str]] = {d: [] for d in dept_codes}
    general: List[str] = []

    # Sort dept codes theo độ dài giảm dần để match prefix dài trước
    sorted_depts = sorted(dept_codes, key=len, reverse=True)

    for w in warnings:
        matched_dept = None
        wl = w.lower()
        for d in sorted_depts:
            dl = d.lower()
            # Các pattern hay gặp:
            #   "[assign] cntt/..." | "] cntt/" | " cntt/"
            #   "[cntt]" | "(cntt)"
            #   "cntt:" ở đầu hoặc sau khoảng trắng
            patterns = [
                f"] {dl}/", f" {dl}/", f"[{dl}]", f"({dl})",
                f"]{dl}/",
            ]
            if any(p in wl for p in patterns):
                matched_dept = d
                break
            # Bắt đầu bằng "cntt:" hoặc " cntt "
            if re.search(rf"(^|\s){re.escape(dl)}(:|\s)", wl):
                matched_dept = d
                break
        if matched_dept:
            by_dept[matched_dept].append(w)
        else:
            general.append(w)

    # General → đưa vào TẤT CẢ khoa (mỗi khoa cần biết bối cảnh chung)
    for d in dept_codes:
        by_dept[d] = general + by_dept[d]
    return by_dept


def write_per_dept_assignment_log(
    by_dept_dir: Path,
    rows: List[Dict],
    dept_codes: List[str],
) -> Dict[str, int]:
    """Ghi assignment_log.xlsx vào từng thư mục khoa, chỉ chứa rows của khoa đó.

    Trả về {dept_code: số rows ghi}.
    """
    import pandas as pd

    by_dept_rows: Dict[str, List[Dict]] = defaultdict(list)
    for r in rows:
        d = str(r.get("Khoa", "")).strip() or "_unknown"
        by_dept_rows[d].append(r)

    counts: Dict[str, int] = {}
    for dept_code in dept_codes:
        dept_rows = by_dept_rows.get(dept_code, [])
        if not dept_rows:
            continue
        dept_dir = by_dept_dir / dept_code
        dept_dir.mkdir(parents=True, exist_ok=True)
        path = dept_dir / "assignment_log.xlsx"
        df = pd.DataFrame(dept_rows)
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            df.to_excel(writer, sheet_name=(dept_code or "khoa")[:31], index=False)
        counts[dept_code] = len(dept_rows)
    return counts


def write_per_dept_warnings(
    by_dept_dir: Path,
    warnings: List[str],
    dept_codes: List[str],
) -> Dict[str, int]:
    """Ghi warnings.txt vào từng thư mục khoa.

    Trả về {dept_code: số dòng ghi}.
    """
    if not warnings or not dept_codes:
        return {}

    by_dept = split_warnings_by_dept(warnings, dept_codes)
    counts: Dict[str, int] = {}
    for dept_code, dept_warnings in by_dept.items():
        if not dept_warnings:
            continue
        dept_dir = by_dept_dir / dept_code
        dept_dir.mkdir(parents=True, exist_ok=True)
        path = dept_dir / "warnings.txt"
        path.write_text("\n".join(dept_warnings), encoding="utf-8")
        counts[dept_code] = len(dept_warnings)
    return counts
