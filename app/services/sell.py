import logging
import math
from binance.exceptions import BinanceAPIException
from binance.enums import (
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

def execute_sell(symbol: str) -> dict:
    client = get_binance_client()

    if DRY_RUN:
        logger.info(f"[DRY_RUN] SELL {symbol}")
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

        mark_price = float(client.futures_mark_price(symbol=symbol)["markPrice"])

        # leverage 고려한 사용 가능 금액
        allocation = usdt_balance * 0.98 * TRADE_LEVERAGE

        qty = allocation / mark_price

        # 최소 수량 및 stepSize 처리
        info = client.futures_exchange_info()
        symbol_info = next(s for s in info["symbols"] if s["symbol"] == symbol)
        lot_size_filter = next(f for f in symbol_info["filters"] if f["filterType"] == "LOT_SIZE")
        step_size = float(lot_size_filter["stepSize"])
        min_qty = float(lot_size_filter["minQty"])

        # step_size 기준으로 올림 처리
        qty = math.ceil(qty / step_size) * step_size

        # qty가 min_qty 보다 작으면 min_qty 로 강제 진입
        if qty < min_qty:
            logger.warning(f"Calculated qty {qty} < min_qty {min_qty}, forcing to min_qty.")
            qty = min_qty

        logger.info(f"Final qty to sell: {qty} at price: {mark_price}")

        # 시장가 숏 진입 주문
        order = client.futures_create_order(
            symbol=symbol,
            side=SIDE_SELL,
            type=ORDER_TYPE_MARKET,
            quantity=str(qty)
        )
        logger.info(f"SELL executed: {order}")

        # 체결 정보
        order_id = order["orderId"]
        order_details = client.futures_get_order(symbol=symbol, orderId=order_id)
        entry_price = float(order_details["avgPrice"])
        executed_qty = float(order_details["executedQty"])

        logger.info(f"Entry complete: {executed_qty} @ {entry_price}")

        return {"sell": {"filled": executed_qty, "entry": entry_price}}

    except BinanceAPIException as e:
        logger.error(f"Sell order failed: {e}")
        return {"skipped": "api_error", "error": str(e)}
    except Exception as e:
        logger.exception(f"Unexpected error in execute_sell: {e}")
        return {"skipped": "unexpected_error", "error": str(e)}