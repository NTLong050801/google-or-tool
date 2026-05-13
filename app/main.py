from __future__ import annotations

from fastapi import FastAPI, HTTPException

from .schemas import GenerateRequest, GenerateResponse
from .solver import solve_weekly_timetable


app = FastAPI(title="Timetable OR-Tools API", version="0.1.0")


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/generate", response_model=GenerateResponse)
def generate(req: GenerateRequest) -> GenerateResponse:
    cfg = req.or_tools

    # Basic guards (keep API predictable for Laravel caller)
    if cfg.periods_per_day <= 0:
        raise HTTPException(status_code=422, detail="periods_per_day must be > 0")
    if not cfg.days:
        raise HTTPException(status_code=422, detail="days must not be empty")

    res = solve_weekly_timetable(
        days=cfg.days,
        periods_per_day=cfg.periods_per_day,
        morning_periods=cfg.morning_periods,
        afternoon_periods=cfg.afternoon_periods,
        assignments=req.assignments,
        classrooms=req.classrooms,
    )
    return res

