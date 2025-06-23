# app/state.py

from datetime import datetime
from zoneinfo import ZoneInfo

monitor_state = {
    "symbol": "ETHUSDT",

    # 진입 정보
    "entry_price": 0.0,
    "position_qty": 0.0,
    "entry_time": "",

    # 1차 익절 정보
    "first_tp_done": False,
    "first_tp_price": 0.0,
    "first_tp_qty": 0.0,
    "first_tp_time": "",
    "first_tp_pnl": 0.0,

    # 2차 익절 정보
    "second_tp_done": False,
    "second_tp_price": 0.0,
    "second_tp_qty": 0.0,
    "second_tp_time": "",
    "second_tp_pnl": 0.0,

    # 손절 정보
    "sl_done": False,
    "sl_price": 0.0,
    "sl_qty": 0.0,
    "sl_time": "",
    "sl_pnl": 0.0,

    # 현재가 & PnL
    "current_price": 0.0,
    "pnl": 0.0,
}