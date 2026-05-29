# Xếp thời khóa biểu – OR-Tools CP-SAT

Công cụ xếp lịch theo mẫu một tuần (weekly pattern) cho trường cao đẳng/trung cấp, dùng **Google OR-Tools CP-SAT**.

Hỗ trợ 2 chế độ:
- **CLI** – chạy trực tiếp từ terminal, đọc/ghi file local.
- **API** – chạy như FastAPI service, nhận lệnh từ Laravel (hoặc bất kỳ HTTP client nào).

---

## Mục lục

1. [Cài đặt](#cài-đặt)
2. [Cấu trúc thư mục](#cấu-trúc-thư-mục)
3. [Chuẩn bị dữ liệu](#chuẩn-bị-dữ-liệu)
4. [Chạy CLI (không dùng API)](#chạy-cli-không-dùng-api)
5. [Chạy API + kết nối Laravel](#chạy-api--kết-nối-laravel)
6. [Cấu hình ràng buộc](#cấu-hình-ràng-buộc)
7. [Cấu trúc code](#cấu-trúc-code)

---

## Cài đặt

**Yêu cầu:** Python 3.10+

```bash
pip install -r requirements.txt
```

Tạo file `.env` từ mẫu:

```bash
cp .env.example .env
```

Nội dung `.env`:

```env
# Shared secret với Laravel
TIMETABLE_API_KEY=ctech2025@

# Kết nối DB cdata (chỉ cần khi dùng API mode với use_db=true)
CDATA_DB_HOST=127.0.0.1
CDATA_DB_PORT=3306
CDATA_DB_USER=root
CDATA_DB_PASSWORD=
CDATA_DB_NAME=cdata
```

---

## Cấu trúc thư mục

```
app/data/
├── shared/                          # Dùng chung cho mọi học kỳ
│   ├── rooms.csv                    # Danh sách phòng học
│   ├── teachers.xlsx                # Danh sách giảng viên toàn trường
│   ├── departments.xlsx             # Danh sách khoa
│   ├── majors.xlsx                  # Danh sách ngành
│   ├── subjects_trungcap.xls        # Danh sách môn hệ trung cấp (tdhocphan TC, export CTOOL)
│   └── subjects_caodang.xls         # Danh sách môn hệ cao đẳng (tdhocphan CĐ, export CTOOL)
│
└── terms/
    └── 2025_2026_HK2/               # Mỗi học kỳ 1 thư mục
        ├── weeks.csv                # Danh sách tuần học
        ├── holidays.csv             # Tuần nghỉ lễ (fallback nếu không dùng DB)
        ├── raw/                     # File export thô từ CTOOL (input cho parse_ctdt.py)
        │   ├── ctdt_trungcap.xls    # CTĐT trung cấp toàn trường
        │   └── ctdt_caodang.xls     # CTĐT cao đẳng toàn trường
        └── departments/
            └── cntt/                # Mỗi khoa 1 thư mục (tự sinh bởi parse_ctdt.py)
                └── cleans/
                    ├── classes.csv             # Lớp → class_group_id
                    ├── dao_tao_trung_cap.xlsx  # CTĐT trung cấp (sinh bởi parse_ctdt.py)
                    ├── dao_tao_cao_dang.xlsx   # CTĐT cao đẳng (sinh bởi parse_ctdt.py)
                    ├── teacher_subjects.xlsx   # Phân công GV – môn
                    └── availability.csv        # Lịch bận GV (fallback nếu không dùng DB)

app/data/output/
└── 2025_2026_HK2/
    ├── warnings.txt
    ├── assignment_log.xlsx
    └── by_department/
        └── cntt/
            ├── timetable.csv
            └── timetable_by_class.xlsx
```

---

## Chuẩn bị dữ liệu

### Bước 1 – Parse CTĐT từ CTOOL

Export 2 file từ CTOOL cho toàn trường, đặt vào `terms/<term>/raw/`:
- `ctdt_trungcap.xls` — CTĐT thực hiện khối lớp theo lớp (trung cấp)
- `ctdt_caodang.xls`  — CTĐT thực hiện khối lớp theo lớp (cao đẳng)

Chạy script để tự động tách theo khoa và sinh `dao_tao_*.xlsx`:

```bash
# Chạy cả 2 bậc, tất cả khoa
python scripts/parse_ctdt.py --term 2025_2026_HK2

# Chỉ 1 bậc
python scripts/parse_ctdt.py --term 2025_2026_HK2 --level trungcap
python scripts/parse_ctdt.py --term 2025_2026_HK2 --level caodang

# Chỉ 1 khoa
python scripts/parse_ctdt.py --term 2025_2026_HK2 --dept cntt
```

Script đọc `subjects_trungcap.xls` hoặc `subjects_caodang.xls` tương ứng để tra `room_type`, tự tách theo prefix lớp
(vd `LT*`, `TK*`, `TT*` → `cntt`), và ghi thẳng vào `cleans/` của từng khoa.

Nếu môn nào không tìm thấy `room_type` → in cảnh báo, cần điền tay hoặc thêm vào `CUSTOM_ROOM_TYPE` trong script.

**Cấu hình prefix lớp → khoa** nằm trong `scripts/parse_ctdt.py`, phần `CLASS_PREFIX_MAPPING`.

### Bước 2 – Chuẩn bị `teacher_subjects.xlsx`

File này map GV → môn học → lớp. Các cột:

| Cột | Ý nghĩa |
|-----|---------|
| `teacher_id` | Mã GV (khớp với `teachers.xlsx`) |
| `subject_code` | Mã môn |
| `class_id` | Mã lớp |
| `priority` | 1–3 (1 = ưu tiên cao nhất) |
| `teacher_type` | `cơ hữu` hoặc `thỉnh giảng` |

### Bước 3 – Kiểm tra `rooms.csv`

Các cột quan trọng:

| Cột | Ý nghĩa |
|-----|---------|
| `room_name` | Mã phòng (unique) |
| `room_label` | Tên hiển thị |
| `room_property_code` | `LT` / `TH` / `PTH` / `K` |
| `capacity` | Sức chứa |
| `in_use` | `1` = đưa vào xếp lịch |
| `priority_room` | `1` = ưu tiên xếp trước |
| `home_department` | Khoa chủ quản (để ưu tiên) |

---

## Chạy CLI (không dùng API)

Chạy từ thư mục gốc repo:

```bash
# Tất cả khoa trong kỳ 2025_2026_HK2
python -m app.local_timetable --term 2025_2026_HK2

# Chỉ khoa cntt
python -m app.local_timetable --term 2025_2026_HK2 --departments cntt

# Nhiều khoa
python -m app.local_timetable --term 2025_2026_HK2 --departments cntt,cssk,dl

# Giới hạn thời gian solver
python -m app.local_timetable --term 2025_2026_HK2 --seconds 300
```

**Tham số:**

| Tham số | Mặc định | Ý nghĩa |
|---------|----------|---------|
| `--term` | `2025_2026_HK2` | Mã học kỳ |
| `--departments` | tất cả | Danh sách khoa, phân cách bằng dấu phẩy |
| `--seconds` | theo config | Giới hạn thời gian solver (giây) |
| `--data-root` | `app/data` | Thư mục gốc dữ liệu |

Ở chế độ CLI, availability GV và tuần nghỉ đọc từ file CSV (`availability.csv`, `holidays.csv`).

**Output:**

```
app/data/output/2025_2026_HK2/
├── warnings.txt              # Cảnh báo từ builder + solver
├── assignment_log.xlsx       # Log phân công GV (mỗi khoa 1 sheet)
└── by_department/
    └── cntt/
        ├── timetable.csv
        └── timetable_by_class.xlsx
```

---

## Chạy API + kết nối Laravel

### Khởi động FastAPI server

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Kiểm tra server:

```bash
curl http://localhost:8000/health
# {"ok": true, "db": true}
```

### Endpoints

| Method | Path | Auth | Mô tả |
|--------|------|------|-------|
| `GET` | `/health` | Không | Kiểm tra server + DB |
| `POST` | `/api/timetable/generate` | `X-API-Key` | Chạy xếp lịch |
| `GET` | `/api/timetable/download/{term_code}` | `X-API-Key` | Tải ZIP toàn bộ output |
| `GET` | `/api/timetable/download/{term_code}/{dept}/timetable_by_class.xlsx` | `X-API-Key` | Tải xlsx 1 khoa |

### Request body – `/api/timetable/generate`

```json
{
  "term_code": "2025_2026_HK2",
  "departments": ["cntt"],
  "schoolyear": "2025-2026",
  "semester": 2,
  "only_submitted": true,
  "use_db": true,
  "max_seconds": 120
}
```

| Field | Bắt buộc | Mô tả |
|-------|----------|-------|
| `term_code` | ✓ | Mã học kỳ, khớp với tên thư mục trong `terms/` |
| `departments` | | Danh sách khoa. `null` = tất cả khoa |
| `schoolyear` | ✓ | Năm học, vd `"2025-2026"` |
| `semester` | ✓ | Học kỳ: `1` hoặc `2` |
| `only_submitted` | | `true` = chỉ lấy availability đã submit (bỏ draft) |
| `use_db` | | `true` = đọc availability + holidays từ DB cdata; `false` = dùng file CSV |
| `max_seconds` | | Override thời gian solver |

### Cấu hình Laravel (`.env`)

```env
TIMETABLE_API_URL=http://localhost:8000
TIMETABLE_API_KEY=ctech2025@
TIMETABLE_API_TIMEOUT=180
```

### Luồng Laravel → Python

```
[Admin nhấn "Chạy xếp TKB"]
        │
        ▼
Admin\TimetableRunController@run
        │  POST /api/timetable/generate
        │  Header: X-API-Key: ctech2025@
        ▼
FastAPI /api/timetable/generate
        │
        ├─ use_db=true → đọc edu_teacher_availabilities + edu_week_holidays từ DB cdata
        │
        ├─ build_generate_request() → load file cleans/, gán GV, tính sessions_per_week
        │
        ├─ solve_weekly_timetable() → CP-SAT solver
        │
        └─ export → app/data/output/<term>/by_department/<dept>/timetable_by_class.xlsx
        │
        ▼
Laravel nhận response JSON:
{
  "status": "OPTIMAL",
  "sessions": 312,
  "departments": ["cntt"],
  "warnings": [],
  "output_dir": "...",
  "download_url": "/api/timetable/download/2025_2026_HK2"
}
        │
        ▼
Admin tải file qua GET /api/timetable/download/2025_2026_HK2
```

### Nguồn dữ liệu availability

| Chế độ | Nguồn | Khi nào dùng |
|--------|-------|--------------|
| `use_db=true` | Bảng `edu_teacher_availabilities` trong DB `cdata` | GV đã đăng ký qua web Laravel |
| `use_db=false` | `cleans/availability.csv` | Test local, không có DB |

---

## Cấu hình ràng buộc

File `app/config/scheduling_rules.json` điều chỉnh các tham số solver:

```json
{
  "general": {
    "days": [2, 3, 4, 5, 6, 7],
    "periods_per_day": 10,
    "morning_periods": [1, 2, 3, 4, 5],
    "afternoon_periods": [6, 7, 8, 9, 10],
    "max_time_seconds": 120,
    "num_workers": 4
  },
  "hard_constraints": {
    "no_room_conflict": true,
    "no_teacher_conflict": true,
    "no_class_group_conflict": true,
    "respect_teacher_availability": true,
    "trung_cap_morning_only": true,
    "match_room_type": true,
    "match_room_capacity": true,
    "max_spw_trung_cap": 6,
    "max_spw_cao_dang": 12,
    "fixed_rooms_by_subject_keyword": [
      {
        "keywords": ["thể chất", "gdtc"],
        "room_names": ["SAN1", "SAN2"],
        "description": "GDTC chỉ dùng sân thể thao"
      }
    ]
  }
}
```

**Ràng buộc cứng:**

| Tên | Mô tả |
|-----|-------|
| `no_room_conflict` | Một phòng không dùng cho 2 lớp cùng lúc |
| `no_teacher_conflict` | Một GV không dạy 2 lớp cùng lúc |
| `no_class_group_conflict` | Một lớp không học 2 môn cùng lúc |
| `respect_teacher_availability` | Không xếp GV vào slot GV đã đăng ký bận |
| `trung_cap_morning_only` | Trung cấp chỉ học buổi sáng |
| `match_room_type` | Môn LT → phòng LT, môn TH → phòng TH |
| `match_room_capacity` | Phòng phải đủ sức chứa cho lớp |
| `max_spw_trung_cap` | Tối đa N buổi/tuần cho lớp trung cấp |
| `max_spw_cao_dang` | Tối đa N buổi/tuần cho lớp cao đẳng |
| `fixed_rooms_by_subject_keyword` | Môn match keyword chỉ được xếp vào phòng chỉ định |

---

## Cấu trúc code

```
app/
├── main.py                  # FastAPI app + endpoints
├── local_timetable.py       # Entrypoint CLI
├── timetable_builder.py     # Đọc data → GenerateRequest + gán GV
├── teacher_assigner.py      # Greedy GV-môn assignment (priority + load balance)
├── timetable_loader.py      # Đọc CSV/XLS, chuẩn hóa tên
├── timetable_export.py      # Sessions → timetable.csv
├── timetable_export_class.py# Sessions → timetable_by_class.xlsx (grid Tuần×Thứ×Buổi)
├── timetable_skip_subjects.py # Danh sách môn loại trừ
├── solver.py                # CP-SAT model: biến, ràng buộc, objective
├── schemas.py               # Pydantic models
├── paths.py                 # Resolve đường dẫn theo (term, dept)
├── scheduling_config.py     # Load scheduling_rules.json
├── db_reader.py             # Đọc availability + holidays từ DB cdata
└── config/
    └── scheduling_rules.json

scripts/
└── parse_ctdt.py            # Parse ctdt_trungcap.xls / ctdt_caodang.xls toàn trường → dao_tao_*.xlsx theo khoa

docs/
└── flow.md                  # Chi tiết luồng xử lý nội bộ
```
