import logging
import time
import math
from binance.exceptions import BinanceAPIException
from binance.enums import (
    SIDE_BUY,
    SIDE_SELL,
    ORDER_TYPE_MARKET,
    ORDER_TYPE_STOP_MARKET,
    ORDER_TYPE_TAKE_PROFIT_MARKET,
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

    # 3) 기존 포지션 체크 및 청산
    positions = client.futures_position_information(symbol=symbol)
    current_amt = next(
        (float(p["positionAmt"]) for p in positions if p["symbol"] == symbol),
        0.0
    )

    if current_amt > 0:
        return {"skipped": "already_long"}

    if current_amt < 0:
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

    info = client.futures_exchange_info()
    sym_info = next(s for s in info["symbols"] if s["symbol"] == symbol)
    lot = next(f for f in sym_info["filters"] if f["filterType"] == "LOT_SIZE")
    step = float(lot["stepSize"])
    min_qty = float(lot["minQty"])

    qty = math.floor(raw_qty / step) * step
    logger.info(f"Balance={usdt_bal}, alloc={alloc}, price={price}, qty={qty}")

    if qty < min_qty or qty <= 0:
        logger.error(f"Calculated qty {qty} < minQty {min_qty}, skipping entry")
        return {"skipped": "calc_zero"}

    # 5) 시장가 매수 진입
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
    logger.info(f"BUY executed {filled}@{entry_price}")

    # 6) TP/SL 설정 (TP: 1차 원 물량의 30%, 2차 잔량의 50% / SL 전체 청산)

    try:
        # 1차 TP: entry_price * 1.005, 원 물량의 30%
        tp_price_1 = round(entry_price * 1.005, 2)
        tp_qty_1 = math.floor((filled * 0.3) / step) * step

        if tp_qty_1 >= min_qty:
            client.futures_create_order(
                symbol=symbol,
                side=SIDE_SELL,
                type=ORDER_TYPE_TAKE_PROFIT_MARKET,
                stopPrice=str(tp_price_1),
                quantity=str(tp_qty_1),
                reduceOnly=True
            )
            logger.info(f"Set TP1: qty={tp_qty_1}, price={tp_price_1}")
        else:
            logger.warning(f"TP1 qty {tp_qty_1} < minQty {min_qty}, skipping")

        # 2차 TP: entry_price * 1.011, 남은 잔량의 50%
        tp_price_2 = round(entry_price * 1.011, 2)
        remaining_qty = filled - tp_qty_1
        tp_qty_2 = math.floor((remaining_qty * 0.5) / step) * step

        if tp_qty_2 >= min_qty:
            client.futures_create_order(
                symbol=symbol,
                side=SIDE_SELL,
                type=ORDER_TYPE_TAKE_PROFIT_MARKET,
                stopPrice=str(tp_price_2),
                quantity=str(tp_qty_2),
                reduceOnly=True
            )
            logger.info(f"Set TP2: qty={tp_qty_2}, price={tp_price_2}")
        else:
            logger.warning(f"TP2 qty {tp_qty_2} < minQty {min_qty}, skipping")

        # SL 설정
        sl_price = round(entry_price * 0.995, 2)
        client.futures_create_order(
            symbol=symbol,
            side=SIDE_SELL,
            type=ORDER_TYPE_STOP_MARKET,
            stopPrice=str(sl_price),
            quantity=str(filled),
            reduceOnly=True
        )
        logger.info(f"Set SL: qty={filled}, stopPrice={sl_price}")

    except BinanceAPIException as e:
        logger.error(f"Failed to set TP/SL orders: {e}")

    return {"buy": {"filled": filled, "entry": entry_price}}