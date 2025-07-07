import logging
import math
from binance.exceptions import BinanceAPIException
from binance.enums import SIDE_BUY, SIDE_SELL, ORDER_TYPE_MARKET
from app.clients.binance_client import get_binance_client
from app.config import DRY_RUN, BUY_PCT, TRADE_LEVERAGE

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

def adjust_amount(value: float) -> float:
    """ 물량: 소수점 3자리까지 """
    return round(value, 3)

def adjust_price(value: float) -> float:
    """ 가격: 소수점 1자리까지 """
    return round(value, 1)

def execute_buy(symbol: str) -> dict:
    client = get_binance_client()

    if DRY_RUN:
        logger.info(f"[DRY_RUN] BUY {symbol}")
        return {"skipped": "dry_run"}

    client.futures_change_leverage(symbol=symbol, leverage=TRADE_LEVERAGE)

    for order in client.futures_get_open_orders(symbol=symbol):
        if order.get("reduceOnly"):
            client.futures_cancel_order(symbol=symbol, orderId=order["orderId"])

    positions = client.futures_position_information(symbol=symbol)
    current_amt = next((float(p["positionAmt"]) for p in positions if p["symbol"] == symbol), 0.0)

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

    bal_list = client.futures_account_balance()
    usdt_bal = float(next(item["balance"] for item in bal_list if item["asset"] == "USDT"))
    alloc = usdt_bal * BUY_PCT
    price = float(client.futures_symbol_ticker(symbol=symbol)["price"])
    qty = adjust_amount(alloc / price)

    order = client.futures_create_order(
        symbol=symbol,
        side=SIDE_BUY,
        type=ORDER_TYPE_MARKET,
        quantity=str(qty)
    )

    order_info = client.futures_get_order(symbol=symbol, orderId=order["orderId"])
    filled = float(order_info["executedQty"])
    entry_price = float(order_info["avgPrice"])

    try:
        # 1차 TP: 0.5% → 30%
        tp_price_1 = adjust_price(entry_price * 1.005)
        tp_qty_1 = adjust_amount(filled * 0.3)
        client.futures_create_order(
            symbol=symbol,
            side=SIDE_SELL,
            type="TAKE_PROFIT_MARKET",
            quantity=str(tp_qty_1),
            params={'stopPrice': str(tp_price_1), "reduceOnly": True}
        )
        logger.info(f"Set 1st TP at {tp_price_1} for qty {tp_qty_1}")

        # 2차 TP: 1.1% → 70%
        tp_price_2 = adjust_price(entry_price * 1.011)
        tp_qty_2 = adjust_amount(filled * 0.7)
        client.futures_create_order(
            symbol=symbol,
            side=SIDE_SELL,
            type="TAKE_PROFIT_MARKET",
            quantity=str(tp_qty_2),
            params={'stopPrice': str(tp_price_2), "reduceOnly": True}
        )
        logger.info(f"Set 2nd TP at {tp_price_2} for qty {tp_qty_2}")

        # SL: -0.5%
        sl_price = adjust_price(entry_price * 0.995)
        client.futures_create_order(
            symbol=symbol,
            side=SIDE_SELL,
            type="STOP_MARKET",
            quantity=str(filled),
            params={'stopPrice': str(sl_price), "reduceOnly": True}
        )
        logger.info(f"Set SL at {sl_price} for qty {filled}")

    except BinanceAPIException as e:
        logger.error(f"TP/SL order failed: {e}")

    return {"buy": {"filled": filled, "entry": entry_price}}