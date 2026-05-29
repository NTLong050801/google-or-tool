"""Parse CTĐT toàn trường → dao_tao_*.xlsx theo từng khoa.

Input:
  app/data/terms/<term>/raw/ctdt_trungcap.xls   - CTĐT trung cấp toàn trường (export CTOOL)
  app/data/terms/<term>/raw/ctdt_caodang.xls    - CTĐT cao đẳng toàn trường (export CTOOL)
  app/data/shared/subjects.xls                  - Danh sách môn học (tdhocphan, export CTOOL)

Output (mỗi khoa):
  app/data/terms/<term>/departments/<dept>/cleans/dao_tao_trung_cap.xlsx
  app/data/terms/<term>/departments/<dept>/cleans/dao_tao_cao_dang.xlsx

Columns: subject_code | subject_name | total_hours | class_id | room_type

Filter: cột "MH xếp thời khóa biểu" = 'x'

Usage:
  python scripts/parse_ctdt.py --term 2025_2026_HK2
  python scripts/parse_ctdt.py --term 2025_2026_HK2 --level trungcap
  python scripts/parse_ctdt.py --term 2025_2026_HK2 --dept cntt
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Dict, Optional

import pandas as pd
from bs4 import BeautifulSoup


# ---------- Config ----------

TERM_CODE = "2025_2026_HK2"

# Map prefix lớp → mã khoa (khớp với tên thư mục trong departments/)
CLASS_PREFIX_MAPPING: Dict[str, str] = {
    # CNTT
    "LT": "cntt",
    "TK": "cntt",
    "TT": "cntt",
    # CKCN
    "ĐĐ": "ckcn",
    "ĐT": "ckcn",
    "OT": "ckcn",
    "TĐ": "ckcn",
    # DDSK
    "ĐD": "ddsk",
    "SĐ": "ddsk",
    "HH": "ddsk",
    # DL
    "CB": "dl",
    "DL": "dl",
    "MK": "dl",
    # NN
    "TH": "nn",
    "TN": "nn",
    "TR": "nn",
}

# Override room_type thủ công cho 1 môn cụ thể của 1 lớp
# key: (subject_code, class_id), value: room_type string
CUSTOM_ROOM_TYPE: Dict[tuple, str] = {
    # ("K16MH04", "LT501-K16"): "LT-Lý thuyết",
}

LEVELS = {
    "trungcap": ("ctdt_trungcap.xls", "subjects_trungcap.xls", "dao_tao_trung_cap.xlsx"),
    "caodang":  ("ctdt_caodang.xls",  "subjects_caodang.xls",  "dao_tao_cao_dang.xlsx"),
}


# ---------- Helpers ----------

def get_dept(class_id: str) -> str:
    cid = str(class_id).strip().upper()
    for prefix, dept in CLASS_PREFIX_MAPPING.items():
        if cid.startswith(prefix.upper()):
            return dept
    return "unknown"


def clean_col(name: str) -> str:
    return (
        str(name)
        .replace("\n", " ")
        .replace("\r", " ")
        .replace("\xa0", " ")
        .strip()
    )


# ---------- Read subjects (tdhocphan) ----------

def load_room_type_map(subjects_file: Path) -> Dict[str, str]:
    """Đọc subjects.xls → dict: subject_code → room_type.

    Dùng BeautifulSoup để lấy đúng header tên cột.
    Fallback: nếu không match code → match theo tên môn (lowercase).
    """
    if not subjects_file.is_file():
        print(f"  [WARN] Không tìm thấy: {subjects_file}")
        return {}

    with open(subjects_file, encoding="utf-8", errors="ignore") as f:
        soup = BeautifulSoup(f, "html.parser")

    table = soup.find("table", {"id": "tb_main_body"})
    if table is None:
        # fallback: lấy table đầu tiên
        table = soup.find("table")
    if table is None:
        print(f"  [WARN] Không tìm thấy table trong {subjects_file}")
        return {}

    thead = table.find("thead")
    headers = []
    if thead:
        header_row = thead.find("tr")
        headers = [th.get_text(strip=True) for th in header_row.find_all("th")]
    headers = [clean_col(h) for h in headers]

    rows_data = []
    for row in table.find_all("tr", {"class": "body"}):
        cols = [td.get_text(strip=True) for td in row.find_all("td")]
        while len(cols) < len(headers):
            cols.append("")
        rows_data.append(cols[:len(headers)])

    if not rows_data:
        print(f"  [WARN] Không có dữ liệu trong {subjects_file}")
        return {}

    df = pd.DataFrame(rows_data, columns=headers)

    code_col = next((c for c in df.columns if "mã môn" in c.lower()), None)
    name_col = next((c for c in df.columns if "tên môn" in c.lower()), None)
    rt_col   = next((c for c in df.columns if "tính chất" in c.lower()), None)

    if not code_col or not rt_col:
        print(f"  [WARN] Không tìm thấy cột 'Mã môn học' hoặc 'Mã tính chất phòng' trong {subjects_file}")
        return {}

    by_code: Dict[str, str] = {}
    by_name: Dict[str, str] = {}

    for _, r in df.iterrows():
        code = str(r.get(code_col, "")).strip()
        rt   = str(r.get(rt_col, "")).strip()
        name = str(r.get(name_col, "")).strip().lower() if name_col else ""
        if not rt or rt.lower() == "nan":
            continue
        if code and code.lower() != "nan":
            by_code.setdefault(code, rt)
        if name:
            by_name.setdefault(name, rt)

    # merge: code lookup ưu tiên, name lookup làm fallback
    result = {**by_name, **by_code}
    print(f"  Loaded room_type map: {len(by_code)} theo mã, {len(by_name)} theo tên")
    return result


# ---------- Read CTĐT ----------

def read_ctdt(file_path: Path) -> pd.DataFrame:
    """Parse file CTĐT toàn trường (HTML disguised .xls).

    Dùng BeautifulSoup tìm cặp table title/body theo id 'tb_main*title*' / 'tb_main*body*'.
    Trả về DataFrame với cột _class_id bổ sung.
    """
    with open(file_path, encoding="utf-8", errors="ignore") as f:
        soup = BeautifulSoup(f, "html.parser")

    title_tables = soup.find_all(
        "table",
        {"id": lambda x: x and "tb_main" in x and "title" in x},
    )
    body_tables = soup.find_all(
        "table",
        {"id": lambda x: x and "tb_main" in x and "body" in x},
    )

    all_rows = []

    for title_tbl, body_tbl in zip(title_tables, body_tables):
        # Lấy class_id từ title table
        class_id = ""
        for line in title_tbl.get_text().split("\n"):
            if "Lớp" in line:
                parts = line.split("Lớp")
                if len(parts) > 1:
                    class_id = parts[1].split(".")[0].strip()
                    if class_id:
                        break

        if not class_id:
            continue

        thead = body_tbl.find("thead")
        if not thead:
            continue
        header_row = thead.find("tr")
        headers = [clean_col(th.get_text(strip=True)) for th in header_row.find_all("th")]

        for row in body_tbl.find_all("tr", {"class": "body"}):
            cols = [td.get_text(strip=True) for td in row.find_all("td")]
            while len(cols) < len(headers):
                cols.append("")
            row_dict = dict(zip(headers, cols[:len(headers)]))
            row_dict["_class_id"] = class_id
            all_rows.append(row_dict)

    if not all_rows:
        return pd.DataFrame()

    df = pd.DataFrame(all_rows)
    df.columns = [clean_col(c) for c in df.columns]
    return df


# ---------- Process ----------

def process(
    ctdt_file: Path,
    subjects_file: Path,
    term_dir: Path,
    out_filename: str,
    rt_map: Dict[str, str],
    dept_filter: Optional[str] = None,
) -> Dict[str, int]:
    """Parse 1 file CTĐT → tách theo khoa → ghi cleans/dao_tao_*.xlsx.

    Trả về dict {dept_code: số dòng}.
    """
    if not ctdt_file.is_file():
        print(f"  [SKIP] Không tìm thấy: {ctdt_file}")
        return {}

    print(f"  Đọc {ctdt_file.name} ...", end=" ")
    df = read_ctdt(ctdt_file)
    if df.empty:
        print("0 dòng")
        return {}
    print(f"{len(df)} dòng raw")

    # Tìm tên cột linh hoạt
    code_col  = next((c for c in df.columns if re.search(r"mã\s*môn", c, re.I)), None)
    name_col  = next((c for c in df.columns if re.search(r"tên\s*môn", c, re.I)), None)
    hours_col = next((c for c in df.columns if re.search(r"tổng.*giờ", c, re.I)), None)
    sched_col = next((c for c in df.columns if re.search(r"xếp\s*thời\s*khóa\s*biểu", c, re.I)), None)

    missing = [n for n, c in [("Mã môn học", code_col), ("Tên môn học", name_col),
                               ("Tổng số giờ", hours_col), ("MH xếp TKB", sched_col)] if not c]
    if missing:
        print(f"  [WARN] Thiếu cột: {missing}")

    rows = []
    skipped_no_x = 0
    missing_rt: set[str] = set()

    for _, row in df.iterrows():
        if sched_col:
            need = str(row.get(sched_col, "")).strip().lower()
            if need != "x":
                skipped_no_x += 1
                continue

        sub_code = str(row.get(code_col, "") if code_col else "").strip()
        sub_name = str(row.get(name_col, "") if name_col else "").strip()
        class_id = str(row.get("_class_id", "")).strip()

        try:
            total_hours = int(str(row.get(hours_col, 0) if hours_col else 0).strip())
        except (ValueError, TypeError):
            total_hours = 0

        if not sub_code or not sub_name or total_hours <= 0:
            continue

        # room_type: custom → code → name
        rt = (
            CUSTOM_ROOM_TYPE.get((sub_code, class_id))
            or rt_map.get(sub_code)
            or rt_map.get(sub_name.lower())
            or ""
        )
        if not rt:
            missing_rt.add(f"{sub_code} - {sub_name}")

        rows.append({
            "subject_code": sub_code,
            "subject_name": sub_name,
            "total_hours":  total_hours,
            "class_id":     class_id,
            "room_type":    rt,
        })

    print(f"  Bỏ qua {skipped_no_x} dòng không tick 'x'")
    if missing_rt:
        print(f"  CẢNH BÁO: {len(missing_rt)} môn không có room_type:")
        for m in sorted(missing_rt):
            print(f"    - {m}")

    df_all = pd.DataFrame(rows, columns=[
        "subject_code", "subject_name", "total_hours", "class_id", "room_type"
    ])
    df_all = df_all.drop_duplicates(subset=["subject_code", "class_id"]).reset_index(drop=True)

    # Tách theo khoa
    df_all["_dept"] = df_all["class_id"].apply(get_dept)
    result: Dict[str, int] = {}

    for dept_code, df_dept in df_all.groupby("_dept"):
        if dept_filter and dept_code != dept_filter:
            continue

        df_export = df_dept.drop(columns=["_dept"]).sort_values(
            ["class_id", "subject_code"]
        ).reset_index(drop=True)

        out_dir = term_dir / "departments" / dept_code / "cleans"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / out_filename
        df_export.to_excel(out_path, index=False, engine="openpyxl")

        print(f"  [{dept_code}] {len(df_export)} dòng → {out_path}")
        result[dept_code] = len(df_export)

    return result


# ---------- CLI ----------

def main() -> None:
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="Parse CTĐT toàn trường → dao_tao_*.xlsx theo khoa")
    parser.add_argument("--term",  default=TERM_CODE, help=f"Mã học kỳ (default: {TERM_CODE})")
    parser.add_argument("--level", choices=["trungcap", "caodang"], default=None,
                        help="Bậc đào tạo. Mặc định: chạy cả 2")
    parser.add_argument("--dept",  default=None,
                        help="Chỉ xuất 1 khoa (vd: cntt). Mặc định: tất cả")
    args = parser.parse_args()

    base      = Path("app/data")
    term_dir  = base / "terms" / args.term
    raw_dir   = term_dir / "raw"
    shared    = base / "shared"

    print(f"Term: {args.term}\n")

    levels = [args.level] if args.level else list(LEVELS.keys())

    for level in levels:
        src_name, subjects_name, out_name = LEVELS[level]
        subjects_file = shared / subjects_name
        print(f"=== {level.upper()} ===")
        rt_map = load_room_type_map(subjects_file)
        print()
        result = process(
            ctdt_file=raw_dir / src_name,
            subjects_file=subjects_file,
            term_dir=term_dir,
            out_filename=out_name,
            rt_map=rt_map,
            dept_filter=args.dept,
        )
        total = sum(result.values())
        print(f"  Tổng: {total} dòng / {len(result)} khoa\n")


if __name__ == "__main__":
    main()
