"""Đọc availability + holidays trực tiếp từ DB cdata (thay cho file CSV)."""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Dict, Optional, Set, Tuple

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
