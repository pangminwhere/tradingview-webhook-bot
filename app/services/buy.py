# app/services/buy.py

import logging
import math
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from binance.enums import SIDE_BUY, SIDE_SELL, ORDER_TYPE_MARKET
from binance.exceptions import BinanceAPIException
from app.clients.binance_client import get_binance_client
from app.config import DRY_RUN, BUY_PCT, TRADE_LEVERAGE, TP_RATIO, SL_RATIO

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

def adjust_quantity(qty, step_size):
    precision = int(round(-math.log(step_size, 10), 0))
    return float(Decimal(qty).quantize(Decimal(str(step_size)), rounding=ROUND_DOWN))

def adjust_price(price):
    return float(Decimal(price).quantize(Decimal('1.'), rounding=ROUND_HALF_UP))

def execute_buy(symbol: str) -> dict:
    client = get_binance_client()

    if DRY_RUN:
        logger.info(f"[DRY_RUN] BUY {symbol}")
        return {"skipped": "dry_run"}

    try:
        client.futures_change_leverage(symbol=symbol, leverage=TRADE_LEVERAGE)
        logger.info(f"Leverage set to {TRADE_LEVERAGE}x for {symbol}")

        # 기존 TP/SL 주문 취소
        open_orders = client.futures_get_open_orders(symbol=symbol)
        for order in open_orders:
            if order.get("reduceOnly"):
                client.futures_cancel_order(symbol=symbol, orderId=order["orderId"])
                logger.info(f"Cancelled reduceOnly order {order['orderId']}")

        # 가용 USDT 확인
        balances = client.futures_account_balance()
        usdt_balance = float(next(b['balance'] for b in balances if b['asset'] == 'USDT'))
        alloc = usdt_balance * BUY_PCT * 0.98

        price = float(client.futures_symbol_ticker(symbol=symbol)['price'])
        info = client.futures_exchange_info()
        sym_info = next(s for s in info['symbols'] if s['symbol'] == symbol)
        lot_size_filter = next(f for f in sym_info['filters'] if f['filterType'] == 'LOT_SIZE')
        step_size = float(lot_size_filter['stepSize'])
        min_qty = float(lot_size_filter['minQty'])

        qty_raw = alloc / price
        qty = adjust_quantity(qty_raw, step_size)

        logger.info(f"Alloc: {alloc}, Price: {price}, Raw Qty: {qty_raw}, Adjusted Qty: {qty}")

        if qty < min_qty:
            logger.warning(f"Qty {qty} < Min Qty {min_qty}, continuing but may not execute")

        order = client.futures_create_order(
            symbol=symbol,
            side=SIDE_BUY,
            type=ORDER_TYPE_MARKET,
            quantity=str(qty)
        )

        filled_qty = float(order['executedQty'])
        entry_price = float(order['avgPrice'])
        logger.info(f"BUY executed: {filled_qty} @ {entry_price}")

        # TP 설정
        tp_price = adjust_price(entry_price * TP_RATIO)
        tp_qty = adjust_quantity(filled_qty * 0.3, step_size)

        try:
            client.futures_create_order(
                symbol=symbol,
                side=SIDE_SELL,
                type="TAKE_PROFIT_MARKET",
                quantity=str(tp_qty),
                stopPrice=str(tp_price),
                reduceOnly=True
            )
            logger.info(f"Set 1st TP: Qty={tp_qty}, StopPrice={tp_price}")
        except BinanceAPIException as e:
            logger.error(f"Failed to set 1st TP: {e}")

        # SL 설정
        sl_price = adjust_price(entry_price * SL_RATIO)
        sl_qty = adjust_quantity(filled_qty, step_size)

        try:
            client.futures_create_order(
                symbol=symbol,
                side=SIDE_SELL,
                type="STOP_MARKET",
                quantity=str(sl_qty),
                stopPrice=str(sl_price),
                reduceOnly=True
            )
            logger.info(f"Set SL: Qty={sl_qty}, StopPrice={sl_price}")
        except BinanceAPIException as e:
            logger.error(f"Failed to set SL: {e}")

        return {"buy": {"filled": filled_qty, "entry": entry_price}}

    except BinanceAPIException as e:
        logger.exception(f"Binance API exception during BUY: {e}")
        return {"error": str(e)}
    except Exception as e:
        logger.exception(f"Exception during BUY: {e}")
        return {"error": str(e)}