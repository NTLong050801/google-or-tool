# google-or-tool (FastAPI + OR-Tools)

API xếp thời khóa biểu dùng **OR-Tools CP-SAT**, nhận input từ Laravel và trả về lịch theo **mẫu tuần** (weekly pattern).

## Cài đặt

```bash
pip install -r requirements.txt
```

## Chạy server

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## Endpoints

- `GET /health`
- `POST /generate`

