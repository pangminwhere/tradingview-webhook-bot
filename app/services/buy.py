# app/services/buy.py

import logging
import time
import math
from binance.exceptions import BinanceAPIException
from binance.enums import (
    SIDE_BUY,
    SIDE_SELL,
    ORDER_TYPE_MARKET,
    ORDER_TYPE_LIMIT,
    ORDER_TYPE_STOP_MARKET,
    TIME_IN_FORCE_GTC,
)
from app.clients.binance_client import get_binance_client
from app.config import (
    DRY_RUN,
    BUY_PCT,
    TRADE_LEVERAGE,
    POLL_INTERVAL,
    MAX_WAIT,
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

def _wait_for_position(symbol: str, target_amt: float) -> bool:
    client = get_binance_client()
    start = time.time()
    while time.time() - start < MAX_WAIT:
        pos = client.futures_position_information(symbol=symbol)
        current = float(next(p["positionAmt"] for p in pos if p["symbol"] == symbol))
        if (target_amt > 0 and current > 0) or (target_amt == 0 and current == 0):
            return True
        time.sleep(POLL_INTERVAL)
    logger.warning(f"Timeout while waiting for position {target_amt}")
    return False

def execute_buy(symbol: str) -> dict:
    client = get_binance_client()

    if DRY_RUN:
        logger.info(f"[DRY_RUN] BUY {symbol}")
        return {"skipped": "dry_run"}

    client.futures_change_leverage(symbol=symbol, leverage=TRADE_LEVERAGE)
    logger.info(f"Leverage set to {TRADE_LEVERAGE}x for {symbol}")

    # Cancel existing reduceOnly orders
    for order in client.futures_get_open_orders(symbol=symbol):
        if order.get("reduceOnly"):
            client.futures_cancel_order(symbol=symbol, orderId=order["orderId"])

    positions = client.futures_position_information(symbol=symbol)
    current_amt = float(next(p["positionAmt"] for p in positions if p["symbol"] == symbol))

    if current_amt > 0:
        return {"skipped": "already_long"}

    if current_amt < 0:
        client.futures_create_order(
            symbol=symbol,
            side=SIDE_BUY,
            type=ORDER_TYPE_MARKET,
            quantity=str(abs(current_amt)),
            reduceOnly=True
        )
        if not _wait_for_position(symbol, 0):
            return {"skipped": "close_failed"}

    # Calculate qty using 98% of balance
    usdt_balance = float(next(a["balance"] for a in client.futures_account_balance() if a["asset"] == "USDT"))
    alloc = usdt_balance * BUY_PCT
    price = float(client.futures_symbol_ticker(symbol=symbol)["price"])
    raw_qty = alloc / price

    # Step precision
    info = client.futures_exchange_info()
    symbol_info = next(s for s in info["symbols"] if s["symbol"] == symbol)
    lot_filter = next(f for f in symbol_info["filters"] if f["filterType"] == "LOT_SIZE")
    step_size = float(lot_filter["stepSize"])

    qty = math.floor(raw_qty / step_size) * step_size
    qty = round(qty, 3)

    if qty < 0.001:
        logger.warning(f"Qty {qty} below min trade size, skipping.")
        return {"skipped": "qty_too_small"}

    # Market Buy
    order = client.futures_create_order(
        symbol=symbol,
        side=SIDE_BUY,
        type=ORDER_TYPE_MARKET,
        quantity=str(qty)
    )
    if not _wait_for_position(symbol, qty):
        return {"skipped": "open_failed"}

    order_info = client.futures_get_order(symbol=symbol, orderId=order["orderId"])
    filled = float(order_info["executedQty"])
    entry_price = float(order_info["avgPrice"])

    logger.info(f"BUY executed: {filled} @ {entry_price}")

    # 예약 TP / SL 설정
    tp1_price = math.ceil(entry_price * 1.005 * 100) / 100  # 2째자리 올림
    tp2_price = math.ceil(entry_price * 1.011 * 100) / 100
    sl_price = math.ceil(entry_price * 0.995 * 100) / 100

    tp1_qty = math.ceil(filled * 0.3 * 1000) / 1000  # 3째자리 올림
    tp2_qty = math.ceil(filled * 0.5 * 1000) / 1000

    try:
        if tp1_qty >= 0.001:
            client.futures_create_order(
                symbol=symbol,
                side=SIDE_SELL,
                type=ORDER_TYPE_LIMIT,
                quantity=str(tp1_qty),
                price=str(tp1_price),
                timeInForce=TIME_IN_FORCE_GTC,
                reduceOnly=True
            )
            logger.info(f"1st TP set: {tp1_qty} @ {tp1_price}")

        if tp2_qty >= 0.001:
            client.futures_create_order(
                symbol=symbol,
                side=SIDE_SELL,
                type=ORDER_TYPE_LIMIT,
                quantity=str(tp2_qty),
                price=str(tp2_price),
                timeInForce=TIME_IN_FORCE_GTC,
                reduceOnly=True
            )
            logger.info(f"2nd TP set: {tp2_qty} @ {tp2_price}")

        # SL은 전체 포지션 크기 그대로 사용 (정밀도 불필요, reduceOnly로만)
        client.futures_create_order(
            symbol=symbol,
            side=SIDE_SELL,
            type=ORDER_TYPE_STOP_MARKET,
            stopPrice=str(sl_price),
            quantity=str(filled),
            reduceOnly=True
        )
        logger.info(f"SL set: {filled} @ {sl_price}")

    except BinanceAPIException as e:
        logger.error(f"Failed to set TP/SL: {e}")

    return {"buy": {"filled": filled, "entry_price": entry_price}}