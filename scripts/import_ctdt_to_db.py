"""Import dao_tao_*.xlsx vào bảng edu_ctdt_subjects (DB cdata).

Usage:
  python scripts/import_ctdt_to_db.py --term 2025_2026_HK2 --schoolyear 2025-2026 --semester 2
  python scripts/import_ctdt_to_db.py --term 2025_2026_HK2 --schoolyear 2025-2026 --semester 2 --dept cntt
  python scripts/import_ctdt_to_db.py --term 2025_2026_HK2 --schoolyear 2025-2026 --semester 2 --clear

Options:
  --clear   Xóa dữ liệu cũ của kỳ này trước khi import (re-import)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
import pymysql
from dotenv import load_dotenv
import os

load_dotenv()


def get_conn():
    return pymysql.connect(
        host=os.getenv("CDATA_DB_HOST", "127.0.0.1"),
        port=int(os.getenv("CDATA_DB_PORT", 3306)),
        user=os.getenv("CDATA_DB_USER", "root"),
        password=os.getenv("CDATA_DB_PASSWORD", ""),
        database=os.getenv("CDATA_DB_NAME", "cdata"),
        charset="utf8mb4",
    )


LEVELS = {
    "trungcap": "dao_tao_trung_cap.xlsx",
    "caodang":  "dao_tao_cao_dang.xlsx",
}


def import_file(
    conn,
    xlsx_path: Path,
    schoolyear: str,
    semester: int,
    level: str,
    dept_code: str,
) -> int:
    if not xlsx_path.is_file():
        print(f"  [SKIP] Không tìm thấy: {xlsx_path}")
        return 0

    df = pd.read_excel(xlsx_path, engine="openpyxl")
    if df.empty:
        return 0

    rows = []
    for _, r in df.iterrows():
        subject_code = str(r.get("subject_code", "")).strip()
        subject_name = str(r.get("subject_name", "")).strip()
        class_id     = str(r.get("class_id", "")).strip()
        room_type    = str(r.get("room_type", "")).strip()
        try:
            total_hours = int(r.get("total_hours", 0))
        except (ValueError, TypeError):
            total_hours = 0

        if not subject_code or not subject_name or total_hours <= 0:
            continue

        rows.append((
            schoolyear, semester, level, dept_code,
            subject_code, subject_name, total_hours, class_id, room_type,
        ))

    if not rows:
        return 0

    sql = """
        INSERT INTO cd_edu_ctdt_subjects
            (schoolyear, semester, level, department_code,
             subject_code, subject_name, total_hours, class_id, room_type,
             created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
        ON DUPLICATE KEY UPDATE
            subject_name = VALUES(subject_name),
            total_hours  = VALUES(total_hours),
            room_type    = VALUES(room_type),
            level        = VALUES(level),
            department_code = VALUES(department_code),
            updated_at   = NOW()
    """

    with conn.cursor() as cur:
        cur.executemany(sql, rows)
    conn.commit()
    return len(rows)


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="Import dao_tao_*.xlsx → edu_ctdt_subjects")
    parser.add_argument("--term",       required=True, help="Mã học kỳ, vd: 2025_2026_HK2")
    parser.add_argument("--schoolyear", required=True, help="Năm học, vd: 2025-2026")
    parser.add_argument("--semester",   type=int, required=True, help="Học kỳ: 1 hoặc 2")
    parser.add_argument("--dept",       default=None, help="Chỉ import 1 khoa")
    parser.add_argument("--clear",      action="store_true", help="Xóa dữ liệu cũ trước khi import")
    args = parser.parse_args()

    term_dir = Path("app/data/terms") / args.term / "departments"
    if not term_dir.is_dir():
        print(f"Không tìm thấy thư mục: {term_dir}")
        sys.exit(1)

    conn = get_conn()
    print(f"Kết nối DB thành công → {os.getenv('CDATA_DB_NAME')}")

    if args.clear:
        with conn.cursor() as cur:
            if args.dept:
                cur.execute(
                    "DELETE FROM cd_edu_ctdt_subjects WHERE schoolyear=%s AND semester=%s AND department_code=%s",
                    (args.schoolyear, args.semester, args.dept),
                )
                print(f"Đã xóa dữ liệu cũ: {args.schoolyear} HK{args.semester} / {args.dept}")
            else:
                cur.execute(
                    "DELETE FROM cd_edu_ctdt_subjects WHERE schoolyear=%s AND semester=%s",
                    (args.schoolyear, args.semester),
                )
                print(f"Đã xóa dữ liệu cũ: {args.schoolyear} HK{args.semester}")
        conn.commit()

    depts = [args.dept] if args.dept else sorted(p.name for p in term_dir.iterdir() if p.is_dir())
    total = 0

    for dept_code in depts:
        cleans = term_dir / dept_code / "cleans"
        print(f"\n[{dept_code}]")
        for level, filename in LEVELS.items():
            n = import_file(conn, cleans / filename, args.schoolyear, args.semester, level, dept_code)
            if n:
                print(f"  {level}: {n} dòng imported")
            total += n

    conn.close()
    print(f"\nTổng: {total} dòng → edu_ctdt_subjects")


if __name__ == "__main__":
    main()
