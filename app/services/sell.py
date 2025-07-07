import logging
import math
import decimal
from binance.exceptions import BinanceAPIException
from binance.enums import SIDE_SELL, SIDE_BUY, ORDER_TYPE_MARKET, ORDER_TYPE_LIMIT
from app.clients.binance_client import get_binance_client
from app.config import (
    DRY_RUN,
    BUY_PCT,
    TRADE_LEVERAGE,
    TP_RATIO,
    TP_PART_RATIO,
    SL_RATIO,
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

def ceil_step_size(value, step_size):
    d_value = decimal.Decimal(str(value))
    d_step = decimal.Decimal(str(step_size))
    return float((d_value / d_step).to_integral_value(rounding=decimal.ROUND_CEILING) * d_step)

def execute_sell(symbol: str) -> dict:
    client = get_binance_client()

    if DRY_RUN:
        logger.info(f"[DRY_RUN] SELL {symbol}")
        return {"skipped": "dry_run"}

    # 레버리지 설정
    client.futures_change_leverage(symbol=symbol, leverage=TRADE_LEVERAGE)
    logger.info(f"Leverage set to {TRADE_LEVERAGE}x for {symbol}")

    # 기존 reduceOnly 주문 취소
    for order in client.futures_get_open_orders(symbol=symbol):
        if order.get("reduceOnly"):
            client.futures_cancel_order(symbol=symbol, orderId=order["orderId"])
            logger.info(f"Canceled TP/SL order {order['orderId']}")

    # 심볼 정보
    info = client.futures_exchange_info()
    sym_info = next(s for s in info["symbols"] if s["symbol"] == symbol)

    lot = next(f for f in sym_info["filters"] if f["filterType"] == "LOT_SIZE")
    step_size = float(lot["stepSize"])   # ex) 0.001 ETH

    price_filter = next(f for f in sym_info["filters"] if f["filterType"] == "PRICE_FILTER")
    tick_size = float(price_filter["tickSize"])  # ex) 0.01 USDT

    # 진입 수량 계산
    bal_list = client.futures_account_balance()
    usdt_bal = float(next(item["balance"] for item in bal_list if item["asset"] == "USDT"))
    alloc = usdt_bal * BUY_PCT
    price = float(client.futures_symbol_ticker(symbol=symbol)["price"])
    raw_qty = alloc / price

    qty = ceil_step_size(raw_qty, step_size)
    logger.info(f"Balance={usdt_bal}, alloc={alloc}, price={price}, raw_qty={raw_qty}, qty={qty}")

    if qty <= 0:
        logger.error("Calculated qty <= 0, skipping entry")
        return {"skipped": "calc_zero"}

    # 시장가 매도 (숏 진입)
    try:
        order = client.futures_create_order(
            symbol=symbol,
            side=SIDE_SELL,
            type=ORDER_TYPE_MARKET,
            quantity=qty
        )
        logger.info(f"SELL order executed: {order}")
    except BinanceAPIException as e:
        logger.error(f"Sell order failed: {e}")
        return {"skipped": "sell_failed"}

    # 체결 가격
    entry_price = float(client.futures_symbol_ticker(symbol=symbol)["price"])

    # TP 주문 (30%)
    tp_price = ceil_step_size(entry_price / TP_RATIO, tick_size)
    tp_qty = ceil_step_size(qty * TP_PART_RATIO, step_size)

    if tp_qty > 0:
        try:
            client.futures_create_order(
                symbol=symbol,
                side=SIDE_BUY,
                type=ORDER_TYPE_LIMIT,
                timeInForce='GTC',
                quantity=tp_qty,
                price=tp_price,
                reduceOnly=True
            )
            logger.info(f"1st TP set: qty={tp_qty}, price={tp_price}")
        except BinanceAPIException as e:
            logger.error(f"Failed to set 1st TP: {e}")
    else:
        logger.warning(f"TP qty {tp_qty} <= 0, skipping TP")

    # SL 주문 (100%)
    sl_price = ceil_step_size(entry_price / SL_RATIO, tick_size)
    sl_qty = ceil_step_size(qty, step_size)

    if sl_qty > 0:
        try:
            client.futures_create_order(
                symbol=symbol,
                side=SIDE_BUY,
                type="STOP_MARKET",
                stopPrice=sl_price,
                quantity=sl_qty,
                reduceOnly=True
            )
            logger.info(f"SL set: qty={sl_qty}, stopPrice={sl_price}")
        except BinanceAPIException as e:
            logger.error(f"Failed to set SL: {e}")
    else:
        logger.warning(f"SL qty {sl_qty} <= 0, skipping SL")

    return {"sell": {"filled": qty, "entry": entry_price}}