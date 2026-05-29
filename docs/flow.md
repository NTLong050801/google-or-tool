# Luồng xử lý nội bộ

Chi tiết kỹ thuật từng bước trong pipeline xếp TKB. Xem [README.md](../README.md) để biết cách chạy.

---

## Tổng quan

```
[Data files]
     │
     ▼
timetable_builder.py   ← Đọc cleans/, gán GV, tính sessions_per_week
     │
     ▼
solver.py              ← CP-SAT: tạo biến, thêm ràng buộc, giải
     │
     ▼
timetable_export*.py   ← Xuất CSV + XLSX
```

---

## Bước 1 – Build Request (`timetable_builder.py`)

### 1.1 Dữ liệu dùng chung

```
shared/rooms.csv                → load_classrooms_from_csv()  → List[Classroom]
shared/teachers.xlsx            → load_teachers_xlsx()        → Dict[teacher_id → Teacher]
terms/<term>/weeks.csv          → week_bounds_from_csv()      → (week_lo, week_hi)
terms/<term>/holidays.csv       → (hoặc DB)                  → Set[week_order]
```

- `rooms.csv`: lọc `in_use=1`, map `room_property_code` → type (LT=1, TH=2, PTH=3, K=4).
- `weeks.csv`: lấy min/max `week_order` → `num_weeks = week_hi - week_lo + 1`.

### 1.2 Dữ liệu từng khoa

Với mỗi khoa (vd `cntt`), đọc từ `departments/cntt/cleans/`:

```
classes.csv                  → Dict[class_id → class_group_id]
dao_tao_trung_cap.xlsx       → List[{subject_code, subject_name, total_hours, class_id, room_type}]
dao_tao_cao_dang.xlsx        → (tương tự)
teacher_subjects.xlsx        → List[{teacher_id, subject_code, class_id, priority, teacher_type}]
availability.csv             → Dict[teacher_id → Set[(week, day, session)]]  (nếu không dùng DB)
```

> `dao_tao_*.xlsx` được sinh tự động bởi `scripts/parse_ctdt.py` từ file export CTOOL trong `terms/<term>/raw/`.

### 1.3 Gán GV (`teacher_assigner.py`)

Với mỗi dòng CTĐT (môn × lớp), tìm GV phù hợp nhất theo điểm:

```
score = priority_bonus + type_bonus + avail_bonus + load_balance_bonus

priority_bonus : {1: 20, 2: 15, 3: 10}
type_bonus     : thỉnh giảng = 200, cơ hữu = 0
avail_bonus    : số slot khả dụng của GV trong kỳ
load_balance   : ưu tiên GV ít tiết hơn
```

GV `thỉnh giảng` được ưu tiên cao hơn để tận dụng trước khi dùng GV cơ hữu.

Nếu không có GV nào đủ slot → bỏ qua môn đó + ghi cảnh báo.

### 1.4 Tính `sessions_per_week`

```python
total_periods    = round(total_hours)
periods_per_week = ceil(total_periods / num_weeks)
sessions_per_week = ceil(periods_per_week / lessons_cluster)  # lessons_cluster = 5
```

Giới hạn: min 1, max theo `max_spw_trung_cap` / `max_spw_cao_dang` trong config.

### 1.5 Xác định `classroom_type`

Lấy từ cột `room_type` trong `dao_tao_*.xlsx` (đã parse từ CTOOL qua `parse_ctdt.py`):

| room_type | classroom_type |
|-----------|---------------|
| `LT-*` | 1 (lý thuyết) |
| `TH-*` | 2 (thực hành tin học) |
| `PTH-*` | 3 (phòng thực hành) |
| `K-*` | 4 (khác: sân, xưởng) |
| trống | 1 (default) |

---

## Bước 2 – Solver CP-SAT (`solver.py`)

### 2.1 Biến quyết định

Với mỗi assignment `a`:
- `candidate_rooms` = phòng có type khớp `a.classroom_type`.
- `starts` = vị trí tiết bắt đầu hợp lệ (sáng: [1], chiều: [6] — cluster=5).
- Biến boolean: `x[a_id, day_idx, p_start, room_idx]`

Số biến ≈ N_assignments × 7 ngày × 2 buổi × N_phòng_cùng_loại.

### 2.2 Ràng buộc cứng

| Ký hiệu | Mô tả |
|---------|-------|
| HC-001 | Mỗi assignment xếp đúng `sessions_per_week` buổi |
| HC-002 | Không xung đột phòng: 1 phòng ≤ 1 lớp/slot |
| HC-003 | Không xung đột GV: 1 GV ≤ 1 lớp/slot |
| HC-004 | Không xung đột lớp: 1 lớp ≤ 1 môn/slot |
| HC-005 | Trung cấp chỉ học buổi sáng (`trung_cap_morning_only`) |
| HC-006 | Môn match room_type → phòng đúng loại |
| HC-007 | Phòng đủ sức chứa cho lớp |
| HC-008 | Không xếp GV vào slot GV đăng ký bận |
| HC-009 | Không xếp vào tuần nghỉ lễ |
| HC-010 | Môn match keyword → chỉ dùng phòng chỉ định |
| HC-011 | Giới hạn buổi/tuần theo bậc đào tạo |

### 2.3 Objective (mềm)

```
Maximize Σ x[a, d, p_start, r] × (periods_per_day + 1 - p_start)
```

Ưu tiên xếp tiết sớm hơn trong ngày (tiết 1 > tiết 6).

### 2.4 Kết quả

- `OPTIMAL` / `FEASIBLE` → có lịch, xuất file.
- `INFEASIBLE` → không tìm được lịch thỏa mãn tất cả ràng buộc cứng.
- `UNKNOWN` → hết thời gian, chưa tìm được.

---

## Bước 3 – Export

### `timetable_export.py` → `timetable.csv`

Cột: `Thứ`, `Buổi`, `Tiết`, `Lớp`, `Mã môn`, `Tên môn`, `Giảng viên`, `Phòng`, `Tuần từ`, `Tuần đến`.

### `timetable_export_class.py` → `timetable_by_class.xlsx`

Grid layout: mỗi sheet = 1 lớp, hàng = Tuần, cột = Thứ × Buổi.
Ô hiển thị: `Tên môn / GV / Phòng`. Tuần nghỉ lễ tô màu + ghi lý do.

---

## Nguồn availability GV

| Chế độ | Nguồn | Cấu trúc |
|--------|-------|----------|
| File CSV | `cleans/availability.csv` | `teacher_id, week, day, session` |
| DB (API mode) | Bảng `edu_teacher_availabilities` (DB `cdata`) | Query qua `db_reader.py` |

Khi `use_db=true` (API mode), `db_reader.load_availability_from_db()` trả về cùng cấu trúc dict như file CSV, nên builder không cần biết nguồn.

---

## Sơ đồ file

```
local_timetable.py / main.py   ← Entrypoint (CLI hoặc API)
    │
    ├── paths.py               ← Resolve đường dẫn (term, dept)
    ├── scheduling_config.py   ← Load scheduling_rules.json
    ├── db_reader.py           ← Đọc availability + holidays từ DB cdata
    │
    ├── timetable_builder.py   ← Orchestrate: load → assign → build request
    │       ├── timetable_loader.py        ← Đọc file, chuẩn hóa
    │       ├── teacher_assigner.py        ← Greedy GV assignment
    │       └── timetable_skip_subjects.py ← Danh sách môn loại trừ
    │
    ├── solver.py              ← CP-SAT model
    │
    ├── timetable_export.py    ← → timetable.csv
    └── timetable_export_class.py ← → timetable_by_class.xlsx
```
