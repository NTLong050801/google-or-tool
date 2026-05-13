import csv
from datetime import date
from pathlib import Path

SRC = Path("c:/projects/google-or-tool/app/data/edu_courses.csv")
OUT = Path("c:/projects/google-or-tool/app/data/cleans/classes.csv")

# Tạm thời chỉ lớp cao đẳng (clevel=2). Đặt None để lấy cả trung cấp (clevel=1).
ONLY_CLEVEL: int | None = 2


def strip_quotes(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        return s[1:-1]
    return s


def main() -> None:
    current_year = date.today().year
    out_rows: list[tuple[str, str, int, int]] = []
    with SRC.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                point_id = int(strip_quotes(row["point_id"]))
                year_to = int(strip_quotes(row["year_to"]))
                clevel = int(strip_quotes(row["clevel"]))
            except (KeyError, ValueError):
                continue
            # year_to >= năm hiện tại: lớp còn hoạt động trong năm (vd K14 tốt nghiệp 8/2026 vẫn year_to=2026)
            if point_id != 1 or year_to < current_year:
                continue
            if ONLY_CLEVEL is not None and clevel != ONLY_CLEVEL:
                continue
            edu_id = strip_quotes(row["id"])
            name = strip_quotes(row["name"])
            out_rows.append((edu_id, name, year_to, clevel))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["edu_course_id", "name", "year_to", "clevel"])
        for edu_id, name, year_to, clevel in out_rows:
            w.writerow([edu_id, name, year_to, clevel])

    print(f"current_year={current_year} rows={len(out_rows)}")
    print(OUT)


if __name__ == "__main__":
    main()
