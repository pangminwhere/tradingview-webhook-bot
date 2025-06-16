import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.config import EX_API_KEY, EX_API_SECRET, DRY_RUN
from app.services.trade_manager import TradeManager

logger = logging.getLogger("webhook")
router = APIRouter()

class AlertPayload(BaseModel):
    symbol: str   # e.g. "ETH/USDT"
    action: str   # "BUY" or "SELL"

@router.post("/webhook")
async def webhook(payload: AlertPayload):
    sym    = payload.symbol.upper()
    action = payload.action.upper()
    tm     = TradeManager(EX_API_KEY, EX_API_SECRET)

    if DRY_RUN:
        logger.info(f"[DRY_RUN] {action} {sym}")
        return {"status": "dry_run"}

    try:
        if action == "BUY":
            res = tm.buy(sym)
        elif action == "SELL":
            res = tm.sell(sym)
        else:
            raise HTTPException(400, "Unknown action")
    except Exception as e:
        logger.error(f"Error in {action}: {e}")
        raise HTTPException(500, str(e))

    return {"status": "ok", "result": res}