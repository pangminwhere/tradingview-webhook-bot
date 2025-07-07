# app/services/buy.py

import math
import logging
from binance.enums import SIDE_BUY, SIDE_SELL, ORDER_TYPE_MARKET
from binance.exceptions import BinanceAPIException
from app.clients.binance_client import get_binance_client
from app.config import BUY_PCT, TRADE_LEVERAGE

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

def execute_buy(symbol: str) -> dict:
    client = get_binance_client()

    try:
        client.futures_change_leverage(symbol=symbol, leverage=TRADE_LEVERAGE)
        logger.info(f"Set leverage {TRADE_LEVERAGE}x for {symbol}")

        # Cancel existing reduceOnly orders
        open_orders = client.futures_get_open_orders(symbol=symbol)
        for order in open_orders:
            client.futures_cancel_order(symbol=symbol, orderId=order['orderId'])
            logger.info(f"Canceled open order: {order['orderId']}")

        # Check position
        positions = client.futures_position_information(symbol=symbol)
        position_amt = float(next(p for p in positions if p['symbol'] == symbol)['positionAmt'])
        if position_amt > 0:
            logger.info("Already in long position, skipping buy")
            return {"skipped": "already_long"}

        if position_amt < 0:
            client.futures_create_order(
                symbol=symbol,
                side=SIDE_BUY,
                type=ORDER_TYPE_MARKET,
                quantity=abs(position_amt),
                reduceOnly=True
            )
            logger.info(f"Closed short position of {abs(position_amt)}")

        # Calculate qty
        usdt_balance = float(next(item['balance'] for item in client.futures_account_balance() if item['asset'] == 'USDT'))
        alloc = usdt_balance * BUY_PCT
        price = float(client.futures_symbol_ticker(symbol=symbol)['price'])
        qty_raw = alloc / price

        # Precision handling: truncate to 3 decimals (floor)
        info = client.futures_exchange_info()
        symbol_info = next(s for s in info['symbols'] if s['symbol'] == symbol)
        lot_size_filter = next(f for f in symbol_info['filters'] if f['filterType'] == 'LOT_SIZE')
        step_size = float(lot_size_filter['stepSize'])
        min_qty = float(lot_size_filter['minQty'])

        qty = math.floor(qty_raw / step_size) * step_size
        qty = round(qty, 3)

        if qty < min_qty:
            logger.warning(f"Calculated qty {qty} < min_qty {min_qty}, skipping buy")
            return {"skipped": "qty_below_min"}

        # Market buy
        order = client.futures_create_order(
            symbol=symbol,
            side=SIDE_BUY,
            type=ORDER_TYPE_MARKET,
            quantity=qty
        )
        avg_price = float(order['fills'][0]['price'])
        logger.info(f"Bought {qty} {symbol} at {avg_price}")

        # Setting TP1, TP2, SL using MARKET orders with reduceOnly
        tp1_qty = math.ceil(qty * 0.3 / step_size) * step_size
        tp2_qty = math.ceil((qty - tp1_qty) * 0.5 / step_size) * step_size

        try:
            client.futures_create_order(
                symbol=symbol,
                side=SIDE_SELL,
                type=ORDER_TYPE_MARKET,
                quantity=tp1_qty,
                reduceOnly=True
            )
            logger.info(f"Set TP1: {tp1_qty} {symbol}")
        except BinanceAPIException as e:
            logger.error(f"Failed to set TP1: {e}")

        try:
            client.futures_create_order(
                symbol=symbol,
                side=SIDE_SELL,
                type=ORDER_TYPE_MARKET,
                quantity=tp2_qty,
                reduceOnly=True
            )
            logger.info(f"Set TP2: {tp2_qty} {symbol}")
        except BinanceAPIException as e:
            logger.error(f"Failed to set TP2: {e}")

        try:
            client.futures_create_order(
                symbol=symbol,
                side=SIDE_SELL,
                type=ORDER_TYPE_MARKET,
                quantity=qty,
                reduceOnly=True
            )
            logger.info(f"Set SL: {qty} {symbol}")
        except BinanceAPIException as e:
            logger.error(f"Failed to set SL: {e}")

        return {"status": "buy_placed", "qty": qty, "entry_price": avg_price}

    except Exception as e:
        logger.error(f"Buy order failed: {e}")
        return {"error": str(e)}