# app/routers/dashboard.py
from fastapi import APIRouter, Response
from app.state import last_trade
from app.services.trade_manager import SYMBOL
from ccxt import ExchangeError

router = APIRouter()

@router.get("/dashboard")
def dashboard():
    # 마지막 거래 정보 가져오기
    symbol = last_trade.get("symbol", SYMBOL)
    side   = last_trade.get("side", "none")
    entry  = float(last_trade.get("entry", 0))

    # 현재 가격 조회
    from app.services.trade_manager import TradeManager
    tm = TradeManager()
    try:
        ticker = tm.exchange.fetch_ticker(symbol)
        last_price = float(ticker.get("last", 0))
    except ExchangeError as e:
        return Response(content=f"Error fetching price: {e}", status_code=500)

    # PnL 계산
    pnl_pct = 0.0
    if entry > 0:
        pnl_pct = (last_price / entry - 1) * 100 if side == "long" else (entry / last_price - 1) * 100

    return {
        "symbol": symbol,
        "side": side,
        "entry": entry,
        "last_price": last_price,
        "unrealized_pnl_pct": round(pnl_pct, 2)
    }