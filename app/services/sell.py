# app/services/sell.py

import math
import logging
from binance.enums import SIDE_SELL, SIDE_BUY, ORDER_TYPE_MARKET, TIME_IN_FORCE_GTC
from binance.exceptions import BinanceAPIException
from app.clients.binance_client import get_binance_client
from app.config import SELL_PCT, TRADE_LEVERAGE

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

def execute_sell(symbol: str) -> dict:
    client = get_binance_client()

    try:
        # 레버리지 설정
        client.futures_change_leverage(symbol=symbol, leverage=TRADE_LEVERAGE)
        logger.info(f"Set leverage {TRADE_LEVERAGE}x for {symbol}")

        # 기존 TP/SL 예약 주문 삭제
        open_orders = client.futures_get_open_orders(symbol=symbol)
        for order in open_orders:
            client.futures_cancel_order(symbol=symbol, orderId=order['orderId'])
            logger.info(f"Canceled open order: {order['orderId']}")

        # 현재 포지션 확인
        positions = client.futures_position_information(symbol=symbol)
        position_amt = float(next(p for p in positions if p['symbol'] == symbol)['positionAmt'])
        if position_amt < 0:
            logger.info("Already in short, skipping entry")
            return {"skipped": "already_short"}

        # 포지션 청산 후 숏 진입
        if position_amt > 0:
            client.futures_create_order(
                symbol=symbol,
                side=SIDE_SELL,
                type=ORDER_TYPE_MARKET,
                quantity=position_amt,
                reduceOnly=True
            )
            logger.info(f"Closed long position of {position_amt}")

        # 잔고 기반 매도 수량 계산
        usdt_balance = float(next(item['balance'] for item in client.futures_account_balance() if item['asset'] == 'USDT'))
        alloc = usdt_balance * SELL_PCT
        price = float(client.futures_symbol_ticker(symbol=symbol)['price'])
        qty_raw = alloc / price

        # 심볼별 정밀도 확인
        info = client.futures_exchange_info()
        symbol_info = next(s for s in info['symbols'] if s['symbol'] == symbol)
        lot_size_filter = next(f for f in symbol_info['filters'] if f['filterType'] == 'LOT_SIZE')
        step_size = float(lot_size_filter['stepSize'])
        min_qty = float(lot_size_filter['minQty'])

        # 셋째자리 올림으로 수량 설정
        qty = math.ceil(qty_raw / step_size) * step_size
        qty = round(qty, 3)

        if qty < min_qty:
            logger.warning(f"Calculated qty {qty} < min_qty {min_qty}, skipping sell entry")
            return {"skipped": "qty_below_min"}

        # 시장가 숏 진입
        order = client.futures_create_order(
            symbol=symbol,
            side=SIDE_SELL,
            type=ORDER_TYPE_MARKET,
            quantity=qty
        )
        avg_price = float(order['fills'][0]['price'])
        logger.info(f"Entered short: {qty} {symbol} at {avg_price}")

        # TP1 설정 (30% 분할 청산)
        tp1_qty = math.ceil(qty * 0.3 / step_size) * step_size
        tp1_qty = round(tp1_qty, 3)
        tp1_price = avg_price * 0.99  # 1% 이익 목표
        tp1_price = round(tp1_price, 2)

        try:
            client.futures_create_order(
                symbol=symbol,
                side=SIDE_BUY,
                type=ORDER_TYPE_MARKET,
                quantity=tp1_qty,
                reduceOnly=True
            )
            logger.info(f"Set 1st TP: qty={tp1_qty}, price={tp1_price}")
        except BinanceAPIException as e:
            logger.error(f"Failed to set 1st TP: {e}")

        # TP2 설정 (남은 물량의 50% 청산)
        remaining_qty = qty - tp1_qty
        tp2_qty = math.ceil(remaining_qty * 0.5 / step_size) * step_size
        tp2_qty = round(tp2_qty, 3)
        tp2_price = avg_price * 0.98  # 2% 이익 목표
        tp2_price = round(tp2_price, 2)

        try:
            client.futures_create_order(
                symbol=symbol,
                side=SIDE_BUY,
                type=ORDER_TYPE_MARKET,
                quantity=tp2_qty,
                reduceOnly=True
            )
            logger.info(f"Set 2nd TP: qty={tp2_qty}, price={tp2_price}")
        except BinanceAPIException as e:
            logger.error(f"Failed to set 2nd TP: {e}")

        # SL 설정 (전체 물량 손절)
        sl_price = avg_price * 1.005  # -0.5% 손절 기준
        sl_price = round(sl_price, 2)

        try:
            client.futures_create_order(
                symbol=symbol,
                side=SIDE_BUY,
                type=ORDER_TYPE_MARKET,
                quantity=qty,
                reduceOnly=True
            )
            logger.info(f"Set SL: qty={qty}, price={sl_price}")
        except BinanceAPIException as e:
            logger.error(f"Failed to set SL: {e}")

        return {"status": "sell_placed", "qty": qty, "entry_price": avg_price}

    except Exception as e:
        logger.error(f"Sell order failed: {e}")
        return {"error": str(e)}