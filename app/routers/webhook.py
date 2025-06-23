# app/routers/webhook.py

import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import DRY_RUN
from app.services.switching import switch_position
from app.state import monitor_state

logger = logging.getLogger("webhook")
router = APIRouter()


class AlertPayload(BaseModel):
    symbol: str   # e.g. "ETH/USDT"
    action: str   # "BUY" or "SELL"


@router.post("/webhook")
async def webhook(payload: AlertPayload):
    sym    = payload.symbol.upper().replace("/", "")
    action = payload.action.upper()

    if DRY_RUN:
        logger.info(f"[DRY_RUN] {action} {sym}")
        return {"status": "dry_run"}

    try:
        # 스위칭 로직 실행 (기존 포지션 청산 후 새 진입)
        result = switch_position(sym, action)

        # 시각 (한국시간)
        now = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M:%S")

        if action == "BUY":
            buy = result.get("buy", {})
            entry = float(buy.get("entry", 0))
            qty   = float(buy.get("filled", 0))
            # 모니터 상태 업데이트
            monitor_state.update({
                "symbol":        sym,
                "entry_price":   entry,
                "position_qty":  qty,
                "entry_time":    now,
                # 진입 시 TP/SL 상태 초기화
                "first_tp_done":  False,
                "second_tp_done": False,
                "sl_done":        False,
            })

        elif action == "SELL":
            sell = result.get("sell", {})
            entry = float(sell.get("entry", 0))
            # 숏 포지션 진입은 모니터링하지 않으므로 qty = 0
            monitor_state.update({
                "symbol":        sym,
                "entry_price":   entry,
                "position_qty":  0.0,
                "entry_time":    now,
                "first_tp_done":  False,
                "second_tp_done": False,
                "sl_done":        False,
            })

        else:
            raise HTTPException(status_code=400, detail="Unknown action")

    except Exception as e:
        logger.exception(f"Error processing {action} for {sym}")
        raise HTTPException(status_code=500, detail=str(e))

    return {"status": "ok", "result": result}