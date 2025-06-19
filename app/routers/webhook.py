import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import DRY_RUN
from app.services.trade_manager import TradeManager
from app.state import last_trade

logger = logging.getLogger("webhook")
router = APIRouter()


class AlertPayload(BaseModel):
    symbol: str   # 예: "ETH/USDT"
    action: str   # "BUY" 또는 "SELL"


@router.post("/webhook")
async def webhook(payload: AlertPayload):
    sym    = payload.symbol.upper()
    action = payload.action.upper()
    tm     = TradeManager()     # 실제 모드: testnet=False 이므로 인자 없이 생성

    # Dry-run 모드면 주문 없이 로그만 찍고 리턴
    if DRY_RUN:
        logger.info(f"[DRY_RUN] {action} {sym}")
        return {"status": "dry_run"}

    try:
        if action == "BUY":
            res = tm.buy()
            # 체결된 매수 주문에서 진입가 추출
            buy_order   = res.get("buy", {})
            entry_price = float(buy_order.get("average", buy_order.get("price", 0)))
            last_trade.update({
                "symbol": sym,
                "side":   "long",
                "entry":  entry_price
            })

        elif action == "SELL":
            res = tm.sell()
            # 체결된 매도 주문에서 진입가(숏 진입가) 추출
            sell_order  = res.get("sell", {})
            entry_price = float(sell_order.get("average", sell_order.get("price", 0)))
            last_trade.update({
                "symbol": sym,
                "side":   "short",
                "entry":  entry_price
            })

        else:
            raise HTTPException(status_code=400, detail="Unknown action")

    except Exception as e:
        # 예외 스택 트레이스까지 로그로 남기기
        logger.exception(f"Error processing {action} for {sym}")
        raise HTTPException(status_code=500, detail=str(e))

    return {"status": "ok", "result": res}