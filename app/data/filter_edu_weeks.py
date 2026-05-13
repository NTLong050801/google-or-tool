import csv
from pathlib import Path

SRC = Path("c:/projects/google-or-tool/app/data/edu_weeks.csv")
OUT = Path("c:/projects/google-or-tool/app/data/cleans/weeks.csv")

# Cơ sở chính thức (theo xác nhận của phòng ĐT)
UNIT_ID = 1001


def strip_quotes(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        return s[1:-1]
    return s


def main() -> None:
    rows_out: list[tuple[str, int, int, int, int, str, str, str, int]] = []
    with SRC.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                unit_id = int(strip_quotes(row["unit_id"]))
                schoolyear = int(strip_quotes(row["schoolyear"]))
                semester = int(strip_quotes(row["semester"]))
                order_id = int(strip_quotes(row["order_id"]))
                status = int(strip_quotes(row["status"]))
            except (KeyError, ValueError):
                continue
            if unit_id != UNIT_ID:
                continue
            edu_week_id = strip_quotes(row["id"])
            from_date = strip_quotes(row["from_date"])
            to_date = strip_quotes(row["to_date"])
            title = strip_quotes(row.get("title", "") or "")
            rows_out.append(
                (
                    edu_week_id,
                    unit_id,
                    schoolyear,
                    semester,
                    order_id,
                    from_date,
                    to_date,
                    title,
                    1 if status == 1 else 0,
                )
            )

    rows_out.sort(key=lambda r: (r[2], r[3], r[4]))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8-sig") as wf:
        w = csv.writer(wf)
        w.writerow(
            [
                "edu_week_id",
                "unit_id",
                "school_year",
                "semester",
                "week_order",
                "from_date",
                "to_date",
                "title",
                "is_active",
            ]
        )
        for r in rows_out:
            w.writerow(list(r))

    print(f"unit_id={UNIT_ID} rows={len(rows_out)}")
    print(OUT)


if __name__ == "__main__":
    main()
