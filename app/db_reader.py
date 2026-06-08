"""Đọc availability + holidays + subject_registrations từ DB cdata."""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Dict, List, Optional, Set, Tuple

import pymysql


def _config() -> dict:
    return {
        "host":     os.getenv("CDATA_DB_HOST", "127.0.0.1"),
        "port":     int(os.getenv("CDATA_DB_PORT", "3306")),
        "user":     os.getenv("CDATA_DB_USER", "root"),
        "password": os.getenv("CDATA_DB_PASSWORD", ""),
        "database": os.getenv("CDATA_DB_NAME", "cdata"),
        "charset":  "utf8mb4",
    }


def _ctool_db() -> str:
    return os.getenv("CTOOL_DB_NAME", "cbase_v2")


@contextmanager
def _connect():
    conn = pymysql.connect(**_config())
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def _ctool_connect():
    cfg = {**_config(), "database": _ctool_db()}
    conn = pymysql.connect(**cfg)
    try:
        yield conn
    finally:
        conn.close()


def load_availability_from_db(
    schoolyear: str,
    semester: int,
    only_submitted: bool = True,
) -> Dict[str, Set[Tuple[int, int, int]]]:
    """Trả về dict[teacher_username → set[(week_order, day_of_week, session_id)]].

    Lưu ý: bảng edu_teacher_availabilities lưu teacher_id = users.id (BIGINT).
    Pipeline OR-Tools dùng username (vd "CT176") làm teacher_id key
    nên cần JOIN sang users để lấy username.

    only_submitted=True → chỉ lấy slot status='submitted'.
    """
    sql = """
        SELECT u.username, a.week_order, a.day_of_week, a.session_id
        FROM cd_edu_teacher_availabilities a
        JOIN {ctool}.users u ON u.id = a.teacher_id
        WHERE a.schoolyear = %s AND a.semester = %s
    """.format(ctool=_ctool_db())
    params = [schoolyear, semester]
    if only_submitted:
        sql += " AND a.status = 'submitted'"

    result: Dict[str, Set[Tuple[int, int, int]]] = {}
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            for username, w, d, s in cur.fetchall():
                tid = (username or "").strip()
                if not tid:
                    continue
                result.setdefault(tid, set()).add((int(w), int(d), int(s)))
    return result


def load_holidays_from_db(
    schoolyear: str,
    semester: int,
    unit_id: Optional[int] = None,
) -> Tuple[Set[int], Dict[int, str]]:
    """Trả về (set[week_order], dict[week_order → reason]).

    unit_id optional: nếu None, lấy hết (đa-đơn vị).
    """
    sql = """
        SELECT week_order, reason FROM cd_edu_week_holidays
        WHERE schoolyear = %s AND semester = %s
    """
    params = [schoolyear, semester]
    if unit_id is not None:
        sql += " AND unit_id = %s"
        params.append(unit_id)

    weeks: Set[int] = set()
    reasons: Dict[int, str] = {}
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            for w, r in cur.fetchall():
                weeks.add(int(w))
                reasons[int(w)] = (r or "Nghỉ").strip()
    return weeks, reasons


def load_class_week_starts_from_db(
    schoolyear: str,
    semester: int,
) -> Dict[str, int]:
    """Trả về {class_id: week_start}.

    Truy vấn bảng cd_edu_class_week_starts (prefix cd_ là tên thật trong DB).
    """
    sql = """
        SELECT class_id, week_start
        FROM cd_edu_class_week_starts
        WHERE schoolyear = %s AND semester = %s
    """
    result: Dict[str, int] = {}
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, [schoolyear, semester])
            for class_id, week_start in cur.fetchall():
                cid = (class_id or "").strip()
                if cid:
                    result[cid] = int(week_start)
    return result


def load_class_excluded_weeks_from_db(
    schoolyear: str,
    semester: int,
) -> Dict[str, Dict[int, str]]:
    """Trả về {class_id: {week_order: reason}}.

    Truy vấn bảng cd_edu_class_excluded_weeks (prefix cd_ là tên thật trong DB).
    Ví dụ: {"TK603-K14": {15: "thi", 16: "du_phong"}, "LT21CNTT1": {18: "thi"}}
    """
    sql = """
        SELECT class_id, week_order, reason
        FROM cd_edu_class_excluded_weeks
        WHERE schoolyear = %s AND semester = %s
    """
    result: Dict[str, Dict[int, str]] = {}
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, [schoolyear, semester])
            for class_id, week_order, reason in cur.fetchall():
                cid = (class_id or "").strip()
                if cid:
                    result.setdefault(cid, {})[int(week_order)] = (reason or "").strip()
    return result


def load_week_dates_from_db(
    schoolyear: str,
    semester: int,
    unit_id: Optional[int] = None,
) -> Dict[int, Tuple[str, str]]:
    """Trả về {order_id: (from_date, to_date)} dạng 'YYYY-MM-DD'.

    Đọc từ edu_weeks (CTool DB). Nếu có nhiều unit, dedup — mỗi order_id lấy 1 lần.
    """
    sql = """
        SELECT order_id, from_date, to_date
        FROM edu_weeks
        WHERE schoolyear = %s AND semester = %s
    """
    params = [schoolyear, semester]
    if unit_id is not None:
        sql += " AND unit_id = %s"
        params.append(unit_id)
    sql += " ORDER BY order_id"

    result: Dict[int, Tuple[str, str]] = {}
    with _ctool_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            for order_id, from_date, to_date in cur.fetchall():
                oid = int(order_id)
                if oid not in result:
                    fd = from_date.strftime("%Y-%m-%d") if hasattr(from_date, "strftime") else str(from_date)
                    td = to_date.strftime("%Y-%m-%d") if hasattr(to_date, "strftime") else str(to_date)
                    result[oid] = (fd, td)
    return result



    """Check kết nối DB - dùng cho health check."""
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        return True
    except Exception:
        return False


def db_available() -> bool:
    """Check kết nối DB - dùng cho health check."""
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        return True
    except Exception:
        return False


def load_subject_registrations_from_db(
    schoolyear: str,
    semester: int,
    only_submitted: bool = True,
) -> List[Dict]:
    """Đọc đăng ký môn dạy của GV từ DB → thay thế teacher_subjects.xlsx.

    Trả về list[dict] với các key:
      teacher_id   : username GV (vd "CT176")
      subject_code : mã môn
      priority     : 1/2/3
      teacher_type : "cơ hữu" / "thỉnh giảng"

    JOIN sang cbase_v2.users để lấy username từ numeric teacher_id.
    """
    sql = """
        SELECT u.username, r.subject_code, r.priority, r.teacher_type
        FROM cd_edu_subject_registrations r
        JOIN {ctool}.users u ON u.id = r.teacher_id
        WHERE r.schoolyear = %s AND r.semester = %s
    """.format(ctool=_ctool_db())
    params = [schoolyear, semester]
    if only_submitted:
        sql += " AND r.status = 'submitted'"

    rows: List[Dict] = []
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            for username, subject_code, priority, teacher_type in cur.fetchall():
                username = (username or "").strip()
                if not username:
                    continue
                rows.append({
                    "teacher_id":   username,
                    "subject_code": (subject_code or "").strip(),
                    "priority":     int(priority or 2),
                    "teacher_type": (teacher_type or "cơ hữu").strip(),
                })
    return rows
