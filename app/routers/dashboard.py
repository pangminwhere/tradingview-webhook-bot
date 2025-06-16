# app/routers/dashboard.py

from fastapi import APIRouter
from fastapi.responses import HTMLResponse
import ccxt
from app.state import last_trade

router = APIRouter()

@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    s = last_trade
    html = """
    <html>
      <head>
        <meta http-equiv="refresh" content="5">
        <title>Trade Dashboard</title>
      </head>
      <body>
        <h1>Trade Status</h1>
    """
    if not s["symbol"]:
        html += "<p>No trades executed yet.</p>"
    else:
        # 현재가 가져오기
        ex = ccxt.binance()
        ticker = ex.fetch_ticker(s["symbol"])
        last_price = float(ticker["last"])
        entry      = s["entry"]
        if s["side"] == "long":
            pnl_pct = (last_price/entry - 1) * 100
        else:
            pnl_pct = (entry/last_price - 1) * 100

        html += f"<p>Symbol: {s['symbol']}</p>"
        html += f"<p>Side: {s['side']}</p>"
        html += f"<p>Entry Price: {entry:.4f}</p>"
        html += f"<p>Current Price: {last_price:.4f}</p>"
        html += f"<p>Unrealized PnL: {pnl_pct:.2f}%</p>"
    html += """
      </body>
    </html>
    """
    return html