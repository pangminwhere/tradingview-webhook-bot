import logging
import math
from binance.exceptions import BinanceAPIException
from binance.enums import (
    SIDE_BUY,
    SIDE_SELL,
    ORDER_TYPE_MARKET,
)
from app.clients.binance_client import get_binance_client
from app.config import (
    DRY_RUN,
    BUY_PCT,
    TRADE_LEVERAGE,
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

def execute_buy(symbol: str) -> dict:
    client = get_binance_client()

    if DRY_RUN:
        logger.info(f"[DRY_RUN] BUY {symbol}")
        return {"skipped": "dry_run"}

    try:
        # 레버리지 설정
        client.futures_change_leverage(symbol=symbol, leverage=TRADE_LEVERAGE)
        logger.info(f"Leverage set to {TRADE_LEVERAGE}x for {symbol}")

        # 기존 reduceOnly 주문 취소
        for order in client.futures_get_open_orders(symbol=symbol):
            if order.get("reduceOnly"):
                client.futures_cancel_order(symbol=symbol, orderId=order["orderId"])
                logger.info(f"Canceled reduceOnly order {order['orderId']}")

        # 잔고 기반 qty 계산
        balances = client.futures_account_balance()
        usdt_balance = float(next(b["balance"] for b in balances if b["asset"] == "USDT"))
        allocation = usdt_balance * 0.98

        mark_price = float(client.futures_mark_price(symbol=symbol)["markPrice"])
        qty = allocation / mark_price

        # 최소 수량 체크 및 3자리 올림
        info = client.futures_exchange_info()
        symbol_info = next(s for s in info["symbols"] if s["symbol"] == symbol)
        lot_size_filter = next(f for f in symbol_info["filters"] if f["filterType"] == "LOT_SIZE")
        step_size = float(lot_size_filter["stepSize"])
        min_qty = float(lot_size_filter["minQty"])

        qty = math.ceil(qty / step_size) * step_size

        if qty < min_qty:
            logger.error(f"Calculated qty {qty} < min_qty {min_qty}, skipping entry.")
            return {"skipped": "qty_too_small"}

        # 시장가 진입
        order = client.futures_create_order(
            symbol=symbol,
            side=SIDE_BUY,
            type=ORDER_TYPE_MARKET,
            quantity=str(qty)
        )
        logger.info(f"BUY executed: {order}")

        # 체결 정보 조회
        order_id = order["orderId"]
        order_details = client.futures_get_order(symbol=symbol, orderId=order_id)
        entry_price = float(order_details["avgPrice"])
        executed_qty = float(order_details["executedQty"])

        logger.info(f"Entry complete: {executed_qty} @ {entry_price}")

        return {"buy": {"filled": executed_qty, "entry": entry_price}}

    except BinanceAPIException as e:
        logger.error(f"Buy order failed: {e}")
        return {"skipped": "api_error", "error": str(e)}
    except Exception as e:
        logger.exception(f"Unexpected error in execute_buy: {e}")
        return {"skipped": "unexpected_error", "error": str(e)}