# app/routers/dashboard.py
from fastapi import APIRouter
from app.state import monitor_state

router = APIRouter()

@router.get("/dashboard")
async def dashboard():
    """
    현재 모니터링 상태 반환:
    {
      symbol: str,
      entry_price: float,
      position_qty: float,
      current_price: float,
      pnl: float,
      first_tp_done: bool,
      second_tp_done: bool
    }
    """
    return monitor_state