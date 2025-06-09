import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import ccxt
from app.config import EX_API_KEY, EX_API_SECRET, DRY_RUN

# 로거 세팅
logger = logging.getLogger("webhook")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# CCXT-Binance 세팅
exchange = ccxt.binance({
    "apiKey" : EX_API_KEY,
    "secret" : EX_API_SECRET
})

router = APIRouter()

# TradingView에서 보내는 JSON 구조 정의
class AlertPayload(BaseModel):
    symbol: str
    action: str
    amount: float
    
@router.post("/webhook")
async def webhook(payload: AlertPayload):
    """
    * 시그니처 검증 없이 바로 주문 실행 *
    """
    # 1) ETH 알림만 처리
    if not payload.symbol.upper().startswith("ETH"):
        logger.info(f"Ignored non-ETH alert: {payload.symbol}")
        return {"status": "ignored", "symbol": payload.symbol}
    
    # 2) Dry-Run 체크
    if DRY_RUN:
        logger.info(f"[DRY_RUN] Received ETH alert → action: {payload.action}, amount: {payload.amount}")
        return {
            "status": "dry_run",
            "received": payload.dict()
        }
        
    action = payload.action.upper()
    
    try:
        if action == "BUY":
            order = exchange.create_market_buy_order(payload.symbol, payload.amount)
        elif action == "SELL":
            order = exchange.create_market_sell_order(payload.symbol, payload.amount)
        else:
            raise HTTPException(status_code=400, detail="Unknown action")
    except Exception as e:
        #CCXT나 네트워크 에러 등 처리
        raise HTTPException(status_code=500, detail=str(e))
    
    logger.info(f"Executed order: {order}")
    return {"status" : "ok", "order": order}