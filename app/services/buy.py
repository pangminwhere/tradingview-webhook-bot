# app/services/buy.py

import logging
import time
import math
from decimal import Decimal, ROUND_DOWN
from binance.exceptions import BinanceAPIException
from binance.enums import (
    SIDE_BUY,
    SIDE_SELL,
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
    """
    step_size: 예) 0.001
    value: float
    """
    step_dec = Decimal(str(step_size))
    precision = abs(step_dec.as_tuple().exponent)
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

        if target_amt > 0 and current > 0:
            return True
        if target_amt == 0 and current == 0:
            return True

        time.sleep(POLL_INTERVAL)

    logger.warning(f"Timeout: target {target_amt}, current {current}")
    return False


def execute_buy(symbol: str) -> dict:
    """
    symbol 예: "ETHUSDT"
    롱 진입 → TP/SL 설정
    반환: {"buy": {"filled": x, "entry": y}} 또는 {"skipped": reason}
    """
    client = get_binance_client()

    if DRY_RUN:
        logger.info(f"[DRY_RUN] BUY {symbol}")
        return {"skipped": "dry_run"}

    # 1) 레버리지 설정
    client.futures_change_leverage(symbol=symbol, leverage=TRADE_LEVERAGE)
    logger.info(f"Leverage set to {TRADE_LEVERAGE}x for {symbol}")

    # 2) 기존 TP/SL 주문 취소
    for order in client.futures_get_open_orders(symbol=symbol):
        if order.get("reduceOnly"):
            client.futures_cancel_order(symbol=symbol, orderId=order["orderId"])
            logger.info(f"Canceled TP/SL order {order['orderId']}")

    # 3) 기존 포지션 체크
    positions = client.futures_position_information(symbol=symbol)
    current_amt = next(
        (float(p["positionAmt"]) for p in positions if p["symbol"] == symbol),
        0.0
    )

    if current_amt > 0:
        return {"skipped": "already_long"}

    if current_amt < 0:
        # 숏 포지션 청산
        client.futures_create_order(
            symbol=symbol,
            side=SIDE_BUY,
            type=ORDER_TYPE_MARKET,
            quantity=abs(current_amt),
            reduceOnly=True
        )
        if not _wait_for_position(symbol, 0.0):
            return {"skipped": "close_failed"}

    # 4) 진입 수량 계산
    bal_list = client.futures_account_balance()
    usdt_bal = float(next(item["balance"] for item in bal_list if item["asset"] == "USDT"))
    alloc = usdt_bal * BUY_PCT
    price = float(client.futures_symbol_ticker(symbol=symbol)["price"])
    raw_qty = alloc / price
    logger.info(f"Balance={usdt_bal}, alloc={alloc}, price={price}, raw_qty={raw_qty}")

    # 심볼별 최소 수량/스텝 조회
    info = client.futures_exchange_info()
    sym_info = next(s for s in info["symbols"] if s["symbol"] == symbol)
    lot = next(f for f in sym_info["filters"] if f["filterType"] == "LOT_SIZE")
    step = float(lot["stepSize"])
    min_qty = float(lot["minQty"])

    # 스텝 단위로 조정
    qty = adjust_to_step_size(raw_qty, step)
    logger.info(f"Adjusted qty by step({step}): {qty}")

    if qty < min_qty or qty <= 0:
        logger.error(f"Calculated qty {qty} < minQty {min_qty}, skipping entry")
        return {"skipped": "calc_zero"}

    # 5) 시장가 롱 진입
    order = client.futures_create_order(
        symbol=symbol,
        side=SIDE_BUY,
        type=ORDER_TYPE_MARKET,
        quantity=str(qty)
    )

    if not _wait_for_position(symbol, qty):
        return {"skipped": "open_failed"}

    # 체결된 주문 정보 재조회
    order_info = client.futures_get_order(
        symbol=symbol,
        orderId=order["orderId"]
    )
    filled = float(order_info["executedQty"])
    entry_price = float(order_info["avgPrice"])
    logger.info(f"BUY executed {filled}@{entry_price}")

    # 6) TP/SL 설정
    # 1차 TP: entry_price * TP_RATIO, 원 물량의 30%
    tp_price = entry_price * TP_RATIO
    tp_qty = adjust_to_step_size(filled * TP_PART_RATIO, step)

    if tp_qty >= min_qty:
        try:
            client.futures_create_order(
                symbol=symbol,
                side=SIDE_SELL,
                type=ORDER_TYPE_LIMIT,
                timeInForce=TIME_IN_FORCE_GTC,
                quantity=str(tp_qty),
                price=str(tp_price),
                reduceOnly=True
            )
            logger.info(f"Set 1st TP: qty={tp_qty}, price={tp_price}")
        except BinanceAPIException as e:
            logger.error(f"Failed to set 1st TP order: {e}")
    else:
        logger.warning(f"TP qty {tp_qty} < minQty {min_qty}, skipping TP")

    # 2차 TP: entry_price * 1.011, 현재 남은 물량의 50% (0.7 * 0.5 = 0.35)
    tp_price_2 = entry_price * 1.011
    tp_qty_2 = adjust_to_step_size(filled * 0.7 * 0.5, step)

    if tp_qty_2 >= min_qty:
        try:
            client.futures_create_order(
                symbol=symbol,
                side=SIDE_SELL,
                type=ORDER_TYPE_LIMIT,
                timeInForce=TIME_IN_FORCE_GTC,
                quantity=str(tp_qty_2),
                price=str(tp_price_2),
                reduceOnly=True
            )
            logger.info(f"Set 2nd TP: qty={tp_qty_2}, price={tp_price_2}")
        except BinanceAPIException as e:
            logger.error(f"Failed to set 2nd TP order: {e}")
    else:
        logger.warning(f"2nd TP qty {tp_qty_2} < minQty {min_qty}, skipping 2nd TP")

    # SL: entry_price * SL_RATIO, 전체 청산
    sl_price = entry_price * SL_RATIO
    sl_qty = adjust_to_step_size(filled, step)

    if sl_qty >= min_qty:
        try:
            client.futures_create_order(
                symbol=symbol,
                side=SIDE_SELL,
                type="STOP_MARKET",
                stopPrice=str(sl_price),
                quantity=str(sl_qty),
                reduceOnly=True
            )
            logger.info(f"Set SL: qty={sl_qty}, stopPrice={sl_price}")
        except BinanceAPIException as e:
            logger.error(f"Failed to set SL order: {e}")
    else:
        logger.warning(f"SL qty {sl_qty} < minQty {min_qty}, skipping SL")

    return {"buy": {"filled": filled, "entry": entry_price}}