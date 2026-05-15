# Luồng hoạt động: Xếp thời khóa biểu (CLI)

Lệnh: `python -m app.local_timetable --term 2025_2026_HK2 --departments cntt --seconds 120`

---

## Tổng quan

```
┌─────────────┐     ┌──────────────────┐     ┌────────────┐     ┌──────────────┐
│  Đọc data   │ ──► │  Build Request   │ ──► │   Solver   │ ──► │  Export CSV  │
│  (loader)   │     │  (builder)       │     │  (CP-SAT)  │     │  (export)    │
└─────────────┘     └──────────────────┘     └────────────┘     └──────────────┘
```

---

## Bước 1: Parse CLI arguments (`local_timetable.py`)

| Tham số | Default | Ý nghĩa |
|---------|---------|---------|
| `--data-root` | `app/data` | Thư mục gốc |
| `--term` | `2025_2026_HK2` | Mã học kỳ → tìm `terms/<term>/` |
| `--departments` | tất cả | Danh sách khoa (phẩy phân cách) |
| `--seconds` | `120` | Thời gian tối đa cho solver |
| `--lessons-cluster` | `5` | Số tiết liên tiếp mỗi buổi |

---

## Bước 2: Build Request (`timetable_builder.py`)

### 2.1 Đọc dữ liệu dùng chung (shared + term)

```
shared/rooms.csv          → load_classrooms_from_csv() → List[Classroom]
terms/<term>/weeks.csv    → week_bounds_from_csv()     → (week_lo, week_hi)
```

- `rooms.csv`: lọc `in_use=1`, bỏ phòng "ảo", map `room_property_code` → type (LT=1, TH=2, X=3, K=4).
- `weeks.csv`: lấy min/max `week_order` → xác định dải tuần học kỳ.
- `num_weeks = week_hi - week_lo + 1`.

### 2.2 Lặp qua từng khoa

Với mỗi khoa (vd `cntt`), đọc:

```
departments/cntt/raw/projects.xls       → parse_projects_catalog()  → Dict[mã_môn → classroom_type]
departments/cntt/raw/classes_project.xls → load_classes_project()   → DataFrame (mỗi dòng = 1 lớp-môn)
departments/cntt/cleans/classes.csv      → load_class_group_ids()   → Dict[tên_lớp → class_group_id]
departments/cntt/cleans/teachers.csv     → load_teacher_lookup()    → Dict[fold(tên_GV) → teacher_id]
departments/cntt/cleans/teacher_aliases.csv → (optional) bổ sung thêm mapping tên GV
```

### 2.3 Xử lý từng dòng trong `classes_project.xls`

Mỗi dòng có: `Class`, `Mã môn học`, `Tên môn học`, `Tổng số giờ`, `Giảng viên`.

Luồng xử lý:

```
Dòng Excel
  │
  ├─ class_name rỗng hoặc mã môn rỗng? → SKIP
  │
  ├─ class_name không có trong classes.csv? → SKIP + WARN
  │
  ├─ Tên môn nằm trong danh sách loại trừ? → SKIP
  │   (thực tập tốt nghiệp, giáo dục quốc phòng)
  │
  ├─ Thiếu GV hoặc GV rỗng? → SKIP + WARN
  │
  ├─ Tên GV không khớp teachers.csv? → SKIP + WARN
  │   (so sánh bằng fold_vi: bỏ dấu, lowercase, bỏ ngoặc)
  │
  ├─ Tổng giờ ≤ 0? → SKIP + WARN
  │
  └─ OK → Tạo Assignment
```

### 2.4 Tính `sessions_per_week` (công thức quy đổi)

```python
total_periods = round(total_hours)                             # 1 giờ Excel = 1 tiết
periods_per_week = ceil(total_periods / num_weeks)             # VD: 30 / 29 = 2 tiết/tuần
sessions_per_week = ceil(periods_per_week / lessons_cluster)   # VD: 2 / 5 = 1 buổi/tuần
```

Giới hạn: min 1, max 20.

### 2.5 Xác định `classroom_type`

- Tra mã môn (chuẩn hóa: uppercase, bỏ space/underscore) trong `projects.xls`.
- Nếu catalog ghi `LT-*` → type=1 (lý thuyết).
- Mọi mã khác (TH, PTH, K, trống) → type=2 (thực hành).
- Không tìm thấy trong catalog → default type=1.

### 2.6 Output của builder

```python
BuildResult(
    request=GenerateRequest(
        or_tools=ORToolsConfig(days=[2..8], periods_per_day=10, morning=[1-5], afternoon=[6-10]),
        assignments=[...],      # 44 assignments (với data CNTT hiện tại)
        classrooms=[...],       # 126 phòng
    ),
    warnings=[...],
    skipped_rows=24,
    assignment_labels={aid: {class_name, subject_code, subject_name, teacher_id, teacher_name, department_code}}
)
```

---

## Bước 3: Solver CP-SAT (`solver.py`)

### 3.1 Chuẩn bị

- Phân nhóm phòng theo type: `rooms_by_type[1] = [idx phòng LT]`, `rooms_by_type[2] = [idx phòng TH]`.
- Xác định khối tiết: sáng (1–5), chiều (6–10).
- Kiểm tra `lessons_cluster` phải = 5 (bằng độ dài khối sáng/chiều). Nếu không → INFEASIBLE.

### 3.2 Tạo biến quyết định

Với mỗi assignment `a`:
- `candidate_rooms` = phòng có type khớp `a.classroom_type`.
- `starts` = vị trí tiết bắt đầu hợp lệ trong khối (sáng: [1], chiều: [6] — vì cluster=5 nên chỉ có 1 vị trí/khối).
- Tạo biến boolean: `x[a_id, day_idx, p_start, room_idx]` cho mọi tổ hợp.

**Số biến** = Σ(assignments) × |days| × |starts| × |candidate_rooms|

Với data hiện tại: 44 × 7 × 2 × ~63 = **~38,808 biến boolean**.

### 3.3 Ràng buộc cứng

#### (a) Đúng số buổi/tuần
```
Σ x[a, *, *, *] == a.sessions_per_week    (cho mỗi assignment a)
```

#### (b) Xung đột phòng
```
Với mỗi (day, period, room):
    Σ x[a, day, p_start, room] ≤ 1
    (chỉ tính các a mà [p_start, p_start+cluster-1] chứa period)
```

#### (c) Xung đột giảng viên
```
Với mỗi (day, period, teacher_id):
    Σ x[a, day, p_start, *] ≤ 1
    (chỉ tính các a có cùng teacher_id và cluster chứa period)
```

#### (d) Xung đột nhóm lớp
```
Với mỗi (day, period, class_group_id):
    Σ x[a, day, p_start, *] ≤ 1
    (chỉ tính các a có cùng class_group_id và cluster chứa period)
```

### 3.4 Mục tiêu mềm (objective)

```
Maximize Σ x[a, d, p_start, r] × (periods_per_day + 1 - p_start)
```

Ý nghĩa: ưu tiên xếp tiết sớm hơn trong ngày (tiết 1 có trọng số 10, tiết 6 có trọng số 5).

### 3.5 Giải

- Engine: CP-SAT với 4 worker threads.
- Time limit: `--seconds` (default 120s).
- Trả về: OPTIMAL / FEASIBLE / INFEASIBLE / UNKNOWN.

### 3.6 Trích xuất kết quả

Với mỗi biến `x[key] == 1`:
```python
ScheduledSession(
    assignment_id, teacher_id, course_id, classroom_id,
    day, period_start, period_end, week_start, week_end, department_code
)
```

---

## Bước 4: Export (`timetable_export.py`)

- Chuyển `ScheduledSession` → dòng CSV với nhãn hiển thị (tên lớp, tên môn, tên GV, tên phòng).
- Sắp xếp theo: Thứ → Tiết → Lớp.
- Ghi ra `output/<term>/timetable.csv` (UTF-8 BOM).
- Ghi JSON đầy đủ ra `output/<term>/timetable_result.json`.

---

## Điểm nghẽn hiệu năng hiện tại

### 1. Solver build constraints: O(D × P × R × |x|) cho phòng

```python
for d_i in range(7):              # 7 ngày
    for p in range(1, 11):        # 10 tiết
        for r_i in range(126):    # 126 phòng
            for k, var in x.items():   # ~38,808 biến
                ...
```

Tổng iterations: 7 × 10 × 126 × 38,808 = **~342 triệu** iterations chỉ cho ràng buộc phòng.

Tương tự cho GV (7 × 10 × N_teachers × 38,808) và group (7 × 10 × N_groups × 38,808).

**Giải pháp (Phase 5)**: build index `dict[(day, period, room)] → list[var]` một lần O(|x|), sau đó mỗi constraint chỉ quét list nhỏ.

### 2. Solver chỉ xếp 1 mẫu tuần

- Mọi assignment dùng chung 1 mẫu tuần, áp cho toàn dải `[week_start, week_end]`.
- Không phân biệt được môn block 1 (tuần 1-8) vs block 2 (tuần 9-15).
- GV bận 1 phần kỳ → không xử lý được.

**Giải pháp (Phase 3+4)**: chia block tuần, solver giải mỗi block riêng.

### 3. `lessons_cluster` cố định = 5

- Mọi môn đều bị ép 5 tiết/buổi.
- Môn LT 2 tiết/tuần vẫn phải xếp 1 buổi 5 tiết → lãng phí.
- Check cứng ở `solver.py:94-103` sẽ reject nếu cluster ≠ 5.

**Giải pháp (Phase 6)**: cho phép per-assignment cluster, gỡ check cứng.

### 4. Không có ràng buộc giờ bận GV

- GV có lịch họp, đi công tác, dạy khoa khác → solver không biết.
- Kết quả có thể xếp GV vào slot thực tế không khả dụng.

**Giải pháp (Phase 3+4)**: `teacher_busy.csv` + constraint ép `var=0`.

---

## Sơ đồ file tham gia

```
local_timetable.py          ← Entrypoint CLI
    │
    ├── paths.py            ← Resolve đường dẫn data theo (term, dept)
    │
    ├── timetable_builder.py ← Ghép data → GenerateRequest
    │       │
    │       ├── timetable_loader.py   ← Đọc CSV/XLS, chuẩn hóa tên
    │       └── timetable_skip_subjects.py ← Danh sách môn loại trừ
    │
    ├── solver.py           ← CP-SAT model + solve
    │
    ├── timetable_export.py ← Sessions → CSV
    │
    └── schemas.py          ← Pydantic models (Assignment, Classroom, ScheduledSession, ...)
```
