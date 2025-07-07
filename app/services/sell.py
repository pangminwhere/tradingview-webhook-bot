import math
import logging
from binance.enums import SIDE_SELL, SIDE_BUY, ORDER_TYPE_MARKET
from binance.exceptions import BinanceAPIException
from app.clients.binance_client import get_binance_client
from app.config import BUY_PCT, TRADE_LEVERAGE

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

def execute_sell(symbol: str) -> dict:
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
        if position_amt < 0:
            logger.info("Already in short position, skipping sell")
            return {"skipped": "already_short"}

        if position_amt > 0:
            client.futures_create_order(
                symbol=symbol,
                side=SIDE_SELL,
                type=ORDER_TYPE_MARKET,
                quantity=position_amt,
                reduceOnly=True
            )
            logger.info(f"Closed long position of {position_amt}")

        # Calculate qty based on USDT balance
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
            logger.warning(f"Calculated qty {qty} < min_qty {min_qty}, skipping sell")
            return {"skipped": "qty_below_min"}

        # Market sell
        order = client.futures_create_order(
            symbol=symbol,
            side=SIDE_SELL,
            type=ORDER_TYPE_MARKET,
            quantity=qty
        )
        avg_price = float(order['fills'][0]['price'])
        logger.info(f"Sold {qty} {symbol} at {avg_price}")

        # TP/SL 예약 주문 설정
        tp1_qty = math.ceil(qty * 0.3 / step_size) * step_size
        tp2_qty = math.ceil((qty - tp1_qty) * 0.5 / step_size) * step_size

        try:
            client.futures_create_order(
                symbol=symbol,
                side=SIDE_BUY,
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
                side=SIDE_BUY,
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
                side=SIDE_BUY,
                type=ORDER_TYPE_MARKET,
                quantity=qty,
                reduceOnly=True
            )
            logger.info(f"Set SL: {qty} {symbol}")
        except BinanceAPIException as e:
            logger.error(f"Failed to set SL: {e}")

        return {"status": "sell_placed", "qty": qty, "entry_price": avg_price}

    except Exception as e:
        logger.error(f"Sell order failed: {e}")
        return {"error": str(e)}