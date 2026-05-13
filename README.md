# Xếp thời khóa biểu – OR-Tools CP-SAT

Công cụ **xếp lịch theo mẫu một tuần (weekly pattern)** cho trường cao đẳng/đại học, dùng **Google OR-Tools CP-SAT**.

Đầu vào là các file Excel/CSV đã chuẩn bị trong `app/data/`. Đầu ra là lịch xếp dạng JSON và CSV trong `app/data/output/`.

---

## Yêu cầu

- Python 3.10+
- Cài thư viện:

```bash
pip install -r requirements.txt
```

---

## Cấu trúc dữ liệu đầu vào

```
app/data/
├── classes_project.xls          # Danh sách lớp – môn – giảng viên – tổng giờ
├── projects.xls                 # Catalog môn học (xác định loại phòng: LT / TH)
└── cleans/
    ├── classes.csv              # Lớp -> edu_course_id (class_group_id)
    ├── teachers.csv             # teacher_id, teacher_name
    ├── teacher_aliases.csv      # (tùy chọn) ánh xạ tên không khớp -> teacher_id
    ├── rooms.csv                # Danh sách phòng (loại, sức chứa, in_use)
    └── weeks.csv                # Tuần học (week_order, from_date, to_date)
```

### classes_project.xls

Cột bắt buộc: `Class`, `Mã môn học`, `Tên môn học`, `Tổng số giờ`, `Giảng viên`.

### cleans/rooms.csv

Các cột quan trọng: `room_name` (id), `room_label` (tên), `room_property_code` (`LT`/`TH`/`X`/`K`), `in_use` (1 = đưa vào lịch).

### cleans/weeks.csv

Cột `week_order` xác định dải tuần. Solver lấy `min` và `max` làm `week_start`/`week_end`.

---

## Chạy xếp lịch

Từ thư mục gốc của repo:

```bash
python -m app.local_timetable
```

Tùy chọn:

| Tham số | Mặc định | Ý nghĩa |
|---------|----------|---------|
| `--seconds` | `120` | Giới hạn thời gian solver (giây) |
| `--lessons-cluster` | `5` | Số tiết liên tiếp mỗi buổi |
| `--minutes-per-lesson` | `50` | Quy đổi giờ trong Excel sang tiết |
| `--data-root` | `app/data` | Thư mục chứa file dữ liệu |

Ví dụ:

```bash
python -m app.local_timetable --seconds 180 --lessons-cluster 5
```

---

## Kết quả đầu ra

```
app/data/output/
├── timetable_result.json   # Toàn bộ kết quả (status, sessions, objective)
└── timetable.csv           # Lịch dạng bảng (Thứ, Buổi, Tiết, Lớp, Môn, GV, Phòng, …)
```

**`timetable.csv`** có các cột: `Thứ`, `Buổi`, `Tiết`, `Lớp`, `Mã môn`, `Tên môn`, `Giảng viên`, `Phòng`, `Tuần từ`, `Tuần đến`.

---

## Ràng buộc solver

**Cứng (hard constraints):**

- Mỗi phân công được xếp đúng số buổi/tuần tính từ tổng giờ.
- Một **phòng** không dùng cho hai lớp cùng lúc.
- Một **giáo viên** không dạy hai lớp cùng lúc.
- Cùng **nhóm lớp** (`class_group_id`) không học hai môn cùng lúc.
- Mỗi buổi học không vượt ranh giới sáng/chiều (tiết 1–5 = sáng, tiết 6–10 = chiều).

**Mềm (objective):** ưu tiên xếp tiết bắt đầu sớm hơn trong ngày.

---

## Môn bị bỏ qua

File `app/timetable_skip_subjects.py` liệt kê các môn không đưa vào xếp lịch (ví dụ: *Thực tập tốt nghiệp*, *Giáo dục quốc phòng*). Thêm quy tắc mới trực tiếp vào hàm `is_excluded_subject`.

---

## Cấu trúc code

```
app/
├── local_timetable.py        # Entrypoint CLI: đọc data -> build request -> solver -> xuất output
├── timetable_builder.py      # Ghép cleans/*.csv + XLS -> GenerateRequest
├── timetable_loader.py       # Các hàm đọc file (CSV, XLS, chuẩn hoá tên)
├── timetable_export.py       # Xuất sessions -> timetable.csv
├── timetable_skip_subjects.py# Danh sách môn loại trừ
├── solver.py                 # CP-SAT: biến, ràng buộc, objective
└── schemas.py                # Pydantic models (Assignment, Classroom, ...)
```
