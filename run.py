#!/usr/bin/env python3
"""Entry point: chạy FastAPI + ghi log ra file logs/app.log"""

from __future__ import annotations

import io
import logging
import sys
import traceback
from datetime import datetime
from pathlib import Path

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "app.log"

# Ghi cả stdout/stderr + file
_stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(_stdout),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)
log = logging.getLogger("run")


def _check_env() -> list[str]:
    """Kiểm tra các điều kiện cần trước khi chạy, trả danh sách lỗi."""
    errors: list[str] = []

    # .env
    env_file = Path(__file__).parent / ".env"
    if not env_file.exists():
        errors.append(".env không tồn tại (copy .env.example → .env và điền biến)")

    # import thử các module quan trọng
    try:
        import ortools  # noqa: F401
    except ImportError:
        errors.append("ortools chưa được cài — chạy: pip install ortools")

    try:
        import fastapi  # noqa: F401
    except ImportError:
        errors.append("fastapi chưa được cài — chạy: pip install fastapi")

    try:
        import uvicorn  # noqa: F401
    except ImportError:
        errors.append("uvicorn chưa được cài — chạy: pip install uvicorn[standard]")

    # Import app để bắt lỗi cấu hình sớm
    try:
        from app.main import app as _  # noqa: F401
    except Exception as exc:
        errors.append(f"Lỗi import app: {exc}")

    return errors


if __name__ == "__main__":
    log.info("=" * 60)
    log.info("Khởi động lúc %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    log.info("Log file: %s", LOG_FILE)

    errors = _check_env()
    if errors:
        log.error("Phát hiện %d lỗi cấu hình:", len(errors))
        for i, err in enumerate(errors, 1):
            log.error("  [%d] %s", i, err)
        log.error("Dừng lại. Xem chi tiết tại: %s", LOG_FILE)
        sys.exit(1)

    try:
        import uvicorn

        host = "0.0.0.0"
        port = 8000
        log.info("Chạy server tại http://%s:%d", host, port)
        uvicorn.run(
            "app.main:app",
            host=host,
            port=port,
            reload=False,
            log_config={
                "version": 1,
                "disable_existing_loggers": False,
                "formatters": {
                    "default": {
                        "format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                    }
                },
                "handlers": {
                    "console": {
                        "class": "logging.StreamHandler",
                        "stream": "ext://sys.stdout",
                        "formatter": "default",
                    },
                    "file": {
                        "class": "logging.FileHandler",
                        "filename": str(LOG_FILE),
                        "encoding": "utf-8",
                        "formatter": "default",
                    },
                },
                "root": {"handlers": ["console", "file"], "level": "INFO"},
                "loggers": {
                    "uvicorn": {"handlers": ["console", "file"], "level": "INFO", "propagate": False},
                    "uvicorn.error": {"handlers": ["console", "file"], "level": "INFO", "propagate": False},
                    "uvicorn.access": {"handlers": ["console", "file"], "level": "INFO", "propagate": False},
                },
            },
        )
    except SystemExit:
        raise
    except Exception:
        log.error("App bị crash:\n%s", traceback.format_exc())
        log.error("Xem chi tiết tại: %s", LOG_FILE)
        sys.exit(1)
