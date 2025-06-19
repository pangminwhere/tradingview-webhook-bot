from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from app.state import last_trade
from app.services.trade_manager import SYMBOL
import ccxt

router = APIRouter()

@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    symbol     = last_trade.get("symbol", SYMBOL)
    side       = last_trade.get("side", "")
    entry      = last_trade.get("entry", 0.0)

    # 현재가 조회
    ex = ccxt.binanceusdm({
        "apiKey": "",
        "secret": "",
        "enableRateLimit": True,
        "options": {"defaultType": "future"},
    })
    ticker = ex.fetch_ticker(symbol)
    last_price = float(ticker["last"])

    # 미실현 PnL
    pnl = 0.0
    if entry > 0:
        if side == "long":
            pnl = (last_price / entry - 1) * 100
        elif side == "short":
            pnl = (entry / last_price - 1) * 100

    html = f"""
    <html>
      <head><title>Trade Dashboard</title></head>
      <body>
        <h1>Trade Status</h1>
        <p><strong>Symbol:</strong> {symbol}</p>
        <p><strong>Side:</strong> {side or '–'}</p>
        <p><strong>Entry Price:</strong> {entry:.4f}</p>
        <p><strong>Current Price:</strong> {last_price:.4f}</p>
        <p><strong>Unrealized PnL:</strong> {pnl:.2f}%</p>
      </body>
    </html>
    """
    return HTMLResponse(content=html)