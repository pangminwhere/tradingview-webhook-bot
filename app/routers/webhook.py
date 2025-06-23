# app/routers/webhook.py

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import DRY_RUN
from app.services.switching import switch_position
from app.state import last_trade

logger = logging.getLogger("webhook")
router = APIRouter()


class AlertPayload(BaseModel):
    symbol: str   # 예: "ETH/USDT"
    action: str   # "BUY" 또는 "SELL"


@router.post("/webhook")
async def webhook(payload: AlertPayload):
    # "ETH/USDT" → "ETHUSDT"
    sym    = payload.symbol.replace("/", "").upper()
    action = payload.action.upper()

    # Dry-run 모드면 주문 없이 로깅만
    if DRY_RUN:
        logger.info(f"[DRY_RUN] {action} {sym}")
        return {"status": "dry_run"}

    try:
        # 스위칭 로직 (기존 포지션 청산 후 반대 포지션 진입)
        res = switch_position(sym, action)

        # 체결된 주문에서 entry price 추출
        if "buy" in res:
            entry_price = float(res["buy"]["entry"])
            last_trade.update({
                "symbol": sym,
                "side":   "long",
                "entry":  entry_price
            })
        elif "sell" in res:
            entry_price = float(res["sell"]["entry"])
            last_trade.update({
                "symbol": sym,
                "side":   "short",
                "entry":  entry_price
            })
        # else: skipped case, 그대로 리턴

    except Exception as e:
        # 예외 스택 트레이스까지 로그로 남기기
        logger.exception(f"Error processing {action} for {sym}")
        raise HTTPException(status_code=500, detail=str(e))

    return {"status": "ok", "result": res}