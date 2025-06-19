# app/routers/dashboard.py

from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from app.state import last_trade  # entry, side, symbol 등 저장된 상태

router = APIRouter()

@router.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    symbol = last_trade.get("symbol", SYMBOL)
    side   = last_trade.get("side", "-")
    entry  = last_trade.get("entry", 0.0)
    # 현재가 조회 (예: ccxt나 클라이언트에서)
    last_price = fetch_current_price(symbol)

    if entry and entry > 0:
        pnl_pct = (last_price / entry - 1) * 100 if side == "long" else (entry / last_price - 1) * 100
        pnl_str = f"{pnl_pct:.2f}%"
    else:
        # 진입가가 없으면 아직 포지션 없음
        pnl_str = "-"

    html = f"""
    <h1>Trade Status</h1>
    <p>Symbol: {symbol}</p>
    <p>Side: {side}</p>
    <p>Entry Price: {entry if entry>0 else '-'}</p>
    <p>Current Price: {last_price}</p>
    <p>Unrealized PnL: {pnl_str}</p>
    """
    return HTMLResponse(html)