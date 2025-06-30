# app/services/sell.py

import logging
import time
import math
from decimal import Decimal, ROUND_DOWN
from binance.exceptions import BinanceAPIException
from binance.enums import (
    SIDE_SELL,
    SIDE_BUY,
    ORDER_TYPE_MARKET,
    ORDER_TYPE_LIMIT,
    TIME_IN_FORCE_GTC,
)
from app.clients.binance_client import get_binance_client
from app.config import (
    DRY_RUN,
    BUY_PCT,
    TRADE_LEVERAGE,
    TP_RATIO,
    TP_PART_RATIO,
    SL_RATIO,
    POLL_INTERVAL,
    MAX_WAIT,
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

def adjust_to_step_size(value, step_size):
    step_dec = Decimal(str(step_size))
    precision = abs(step_dec.as_tuple().exponent)
    quantize_str = '1.' + '0' * precision
    return float(Decimal(str(value)).quantize(Decimal(quantize_str), rounding=ROUND_DOWN))

def adjust_to_tick_size(value, tick_size):
    tick_dec = Decimal(str(tick_size))
    precision = abs(tick_dec.as_tuple().exponent)
    quantize_str = '1.' + '0' * precision
    return float(Decimal(str(value)).quantize(Decimal(quantize_str), rounding=ROUND_DOWN))

def _wait_for_position(symbol: str, target_amt: float) -> bool:
    client = get_binance_client()
    start = time.time()
    current = None

    while time.time() - start < MAX_WAIT:
        positions = client.futures_position_information(symbol=symbol)
        current = next(
            (float(p["positionAmt"]) for p in positions if p["symbol"] == symbol),
            0.0
        )
        if target_amt < 0 and current < 0:
            return True
        if target_amt == 0 and current == 0:
            return True
        time.sleep(POLL_INTERVAL)

    logger.warning(f"Timeout: target {target_amt}, current {current}")
    return False

def execute_sell(symbol: str) -> dict:
    client = get_binance_client()

    if DRY_RUN:
        logger.info(f"[DRY_RUN] SELL {symbol}")
        return {"skipped": "dry_run"}

    # 1️⃣ 레버리지 설정
    client.futures_change_leverage(symbol=symbol, leverage=TRADE_LEVERAGE)
    logger.info(f"Leverage set to {TRADE_LEVERAGE}x for {symbol}")

    # 2️⃣ 기존 TP/SL 주문 취소
    for order in client.futures_get_open_orders(symbol=symbol):
        if order.get("reduceOnly"):
            client.futures_cancel_order(symbol=symbol, orderId=order["orderId"])
            logger.info(f"Canceled TP/SL order {order['orderId']}")

    # 3️⃣ 기존 포지션 확인 및 정리
    positions = client.futures_position_information(symbol=symbol)
    current_amt = next(
        (float(p["positionAmt"]) for p in positions if p["symbol"] == symbol),
        0.0
    )

    if current_amt < 0:
        return {"skipped": "already_short"}

    if current_amt > 0:
        client.futures_create_order(
            symbol=symbol,
            side=SIDE_SELL,
            type=ORDER_TYPE_MARKET,
            quantity=current_amt,
            reduceOnly=True
        )
        if not _wait_for_position(symbol, 0.0):
            return {"skipped": "close_failed"}

    # 4️⃣ 진입 수량 계산
    bal_list = client.futures_account_balance()
    usdt_bal = float(next(item["balance"] for item in bal_list if item["asset"] == "USDT"))
    alloc = usdt_bal * BUY_PCT
    price = float(client.futures_symbol_ticker(symbol=symbol)["price"])
    raw_qty = alloc / price
    logger.info(f"Balance={usdt_bal}, alloc={alloc}, price={price}, raw_qty={raw_qty}")

    # 심볼 precision 정보
    info = client.futures_exchange_info()
    sym_info = next(s for s in info["symbols"] if s["symbol"] == symbol)
    lot = next(f for f in sym_info["filters"] if f["filterType"] == "LOT_SIZE")
    step_size = float(lot["stepSize"])
    min_qty = float(lot["minQty"])
    price_filter = next(f for f in sym_info["filters"] if f["filterType"] == "PRICE_FILTER")
    tick_size = float(price_filter["tickSize"])

    qty = adjust_to_step_size(raw_qty, step_size)
    logger.info(f"Adjusted qty by step_size({step_size}): {qty}")

    if qty < min_qty or qty <= 0:
        logger.error(f"Calculated qty {qty} < minQty {min_qty}, skipping entry")
        return {"skipped": "calc_zero"}

    # 5️⃣ 시장가 숏 진입
    order = client.futures_create_order(
        symbol=symbol,
        side=SIDE_SELL,
        type=ORDER_TYPE_MARKET,
        quantity=str(qty)
    )

    if not _wait_for_position(symbol, -qty):
        return {"skipped": "open_failed"}

    order_info = client.futures_get_order(
        symbol=symbol,
        orderId=order["orderId"]
    )
    filled = float(order_info["executedQty"])
    entry_price = float(order_info["avgPrice"])
    logger.info(f"SELL executed {filled}@{entry_price}")

    # 6️⃣ TP/SL 설정
    try:
        # 1차 TP: -0.5% ROI, 원물량의 30%
        tp_price_1 = adjust_to_tick_size(entry_price * (1 - 0.005), tick_size)
        tp_qty_1 = adjust_to_step_size(filled * 0.3, step_size)

        client.futures_create_order(
            symbol=symbol,
            side=SIDE_BUY,
            type=ORDER_TYPE_LIMIT,
            timeInForce=TIME_IN_FORCE_GTC,
            quantity=str(tp_qty_1),
            price=str(tp_price_1),
            reduceOnly=True
        )
        logger.info(f"Set 1st TP: qty={tp_qty_1}, price={tp_price_1}")
    except BinanceAPIException as e:
        logger.error(f"Failed to set 1st TP order: {e}")

    try:
        # 2차 TP: -1.1% ROI, 남은 물량의 50%
        tp_price_2 = adjust_to_tick_size(entry_price * (1 - 0.011), tick_size)
        tp_qty_2 = adjust_to_step_size(filled * 0.7 * 0.5, step_size)  # 남은 물량의 50%

        client.futures_create_order(
            symbol=symbol,
            side=SIDE_BUY,
            type=ORDER_TYPE_LIMIT,
            timeInForce=TIME_IN_FORCE_GTC,
            quantity=str(tp_qty_2),
            price=str(tp_price_2),
            reduceOnly=True
        )
        logger.info(f"Set 2nd TP: qty={tp_qty_2}, price={tp_price_2}")
    except BinanceAPIException as e:
        logger.error(f"Failed to set 2nd TP order: {e}")

    try:
        # SL: +0.5% ROI, 전체 물량
        sl_price = adjust_to_tick_size(entry_price * 1.005, tick_size)
        sl_qty = filled

        client.futures_create_order(
            symbol=symbol,
            side=SIDE_BUY,
            type=ORDER_TYPE_LIMIT,
            timeInForce=TIME_IN_FORCE_GTC,
            quantity=str(adjust_to_step_size(sl_qty, step_size)),
            price=str(sl_price),
            reduceOnly=True
        )
        logger.info(f"Set SL: qty={sl_qty}, price={sl_price}")
    except BinanceAPIException as e:
        logger.error(f"Failed to set SL order: {e}")

    return {"sell": {"filled": filled, "entry": entry_price}}