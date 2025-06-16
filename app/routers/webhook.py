import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.config import EX_API_KEY, EX_API_SECRET, DRY_RUN
from app.services.trade_manager import TradeManager
from app.state import last_trade

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
            # BUY 후 상태 저장
            entry = float(res["buy"].get("average", res["buy"].get("price", 0)))
            last_trade.update({
                "symbol": sym,
                "side":   "long",
                "entry":  entry
            })
        elif action == "SELL":
            res = tm.sell(sym)
            # SELL 후 상태 저장
            # (가장 최근 시장가 체결가로 단순히 entry 업데이트)
            entry = float(res.get("short", {}).get("average",
                         res.get("short", {}).get("price", 0)))
            last_trade.update({
                "symbol": sym,
                "side":   "short",
                "entry":  entry
            })
        else:
            raise HTTPException(400, "Unknown action")
    except Exception as e:
        logger.error(f"Error in {action}: {e}")
        raise HTTPException(500, str(e))

    return {"status": "ok", "result": res}