import logging
import math
import threading
import time

from binance.exceptions import BinanceAPIException
from binance.enums import SIDE_BUY, SIDE_SELL, ORDER_TYPE_MARKET
from app.clients.binance_client import get_binance_client
from app.config import DRY_RUN, TRADE_LEVERAGE, POLL_INTERVAL

# 문자열 상수로 TP/SL 마켓 주문 타입 지정
TP_MARKET = "TAKE_PROFIT_MARKET"
SL_MARKET = "STOP_MARKET"

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def execute_buy(symbol: str) -> dict:
    client = get_binance_client()

    if DRY_RUN:
        logger.info(f"[DRY_RUN] BUY {symbol}")
        return {"skipped": "dry_run"}

    try:
        # 1) 레버리지 설정
        client.futures_change_leverage(symbol=symbol, leverage=TRADE_LEVERAGE)
        logger.info(f"Leverage set to {TRADE_LEVERAGE}x for {symbol}")

        # 2) 기존 reduceOnly 주문 삭제
        for order in client.futures_get_open_orders(symbol=symbol):
            if order.get("reduceOnly"):
                client.futures_cancel_order(symbol=symbol, orderId=order["orderId"])
                logger.info(f"Canceled reduceOnly order {order['orderId']}")

        # 3) 진입량 계산
        balances = client.futures_account_balance()
        usdt_balance = float(next(b["balance"] for b in balances if b["asset"] == "USDT"))
        mark_price = float(client.futures_mark_price(symbol=symbol)["markPrice"])
        allocation = usdt_balance * 0.98 * TRADE_LEVERAGE
        raw_qty = allocation / mark_price

        # 필터 정보 조회
        info = client.futures_exchange_info()
        sym_info = next(s for s in info["symbols"] if s["symbol"] == symbol)
        lot_filter   = next(f for f in sym_info["filters"] if f["filterType"] == "LOT_SIZE")
        price_filter = next(f for f in sym_info["filters"] if f["filterType"] == "PRICE_FILTER")
        step_size    = float(lot_filter["stepSize"])
        min_qty      = float(lot_filter["minQty"])
        tick_size    = float(price_filter["tickSize"])

        # precision 계산
        qty_precision   = int(round(-math.log10(step_size), 0))
        price_precision = int(round(-math.log10(tick_size), 0))

        # 4) 주문 수량: 셋째 자리에서 내림
        qty = math.floor(raw_qty / step_size) * step_size
        if qty < min_qty:
            logger.warning(f"Qty {qty} < minQty {min_qty}. Skipping BUY.")
            return {"skipped": "quantity_too_low"}
        qty_str = f"{qty:.{qty_precision}f}"

        # 5) 시장가 진입
        order = client.futures_create_order(
            symbol=symbol,
            side=SIDE_BUY,
            type=ORDER_TYPE_MARKET,
            quantity=qty_str
        )
        logger.info(f"Market BUY submitted: {order}")

        details = client.futures_get_order(symbol=symbol, orderId=order["orderId"])
        entry_price  = float(details["avgPrice"])
        executed_qty = float(details["executedQty"])
        logger.info(f"Entry LONG: {executed_qty}@{entry_price}")

        # 6) TP/SL 가격 올림 함수
        def ceil_price(price: float) -> float:
            factor = 10 ** price_precision
            return math.ceil(price * factor) / factor

        # 1차 TP: +0.5% → 30%
        tp1_price = ceil_price(entry_price * 1.005)
        tp1_qty   = math.floor(executed_qty * 0.30 / step_size) * step_size
        tp1_price_str = f"{tp1_price:.{price_precision}f}"
        tp1_qty_str   = f"{tp1_qty:.{qty_precision}f}"
        order_tp1 = client.futures_create_order(
            symbol=symbol,
            side=SIDE_SELL,
            type=TP_MARKET,
            stopPrice=tp1_price_str,
            reduceOnly=True,
            quantity=tp1_qty_str
        )

        # 2차 TP: +1.1% → 남은 물량의 50%
        remain_after_tp1 = executed_qty - tp1_qty
        tp2_qty   = math.floor(remain_after_tp1 * 0.50 / step_size) * step_size
        tp2_price = ceil_price(entry_price * 1.011)
        tp2_price_str = f"{tp2_price:.{price_precision}f}"
        tp2_qty_str   = f"{tp2_qty:.{qty_precision}f}"
        order_tp2 = client.futures_create_order(
            symbol=symbol,
            side=SIDE_SELL,
            type=TP_MARKET,
            stopPrice=tp2_price_str,
            reduceOnly=True,
            quantity=tp2_qty_str
        )

        # 기본 SL: -0.5% → 전체 수량
        sl_price = ceil_price(entry_price * 0.995)
        sl_price_str = f"{sl_price:.{price_precision}f}"
        sl_qty_str   = f"{executed_qty:.{qty_precision}f}"
        order_sl = client.futures_create_order(
            symbol=symbol,
            side=SIDE_SELL,
            type=SL_MARKET,
            stopPrice=sl_price_str,
            reduceOnly=True,
            quantity=sl_qty_str
        )

        logger.info(
            f"Placed TP1 @ {tp1_price_str} x{tp1_qty_str}, "
            f"TP2 @ {tp2_price_str} x{tp2_qty_str}, "
            f"SL @ {sl_price_str} x{sl_qty_str}"
        )

        # 7) TP1 체결 모니터링 및 SL 이동
        def _monitor_tp1():
            try:
                while True:
                    time.sleep(POLL_INTERVAL)
                    tp1_info = client.futures_get_order(symbol=symbol, orderId=order_tp1["orderId"])
                    if tp1_info.get("status") == "FILLED":
                        # 기존 SL 취소
                        client.futures_cancel_order(symbol=symbol, orderId=order_sl["orderId"])
                        logger.info(f"Canceled SL {order_sl['orderId']} after TP1")

                        # 남은 물량에 대해 SL 재설정 (+0.1%)
                        new_sl_price = ceil_price(entry_price * 1.001)
                        new_sl_price_str = f"{new_sl_price:.{price_precision}f}"
                        remain_str = f"{remain_after_tp1:.{qty_precision}f}"
                        new_sl_order = client.futures_create_order(
                            symbol=symbol,
                            side=SIDE_SELL,
                            type=SL_MARKET,
                            stopPrice=new_sl_price_str,
                            reduceOnly=True,
                            quantity=remain_str
                        )
                        logger.info(
                            f"Moved SL to +0.1% @ {new_sl_price_str} x{remain_str}, "
                            f"new SL id {new_sl_order['orderId']}"
                        )
                        break
            except Exception as e:
                logger.exception(f"Error monitoring TP1: {e}")

        threading.Thread(target=_monitor_tp1, daemon=True).start()

        return {
            "buy": {"filled": executed_qty, "entry": entry_price},
            "orders": {
                "tp1_orderId": order_tp1["orderId"],
                "tp2_orderId": order_tp2["orderId"],
                "sl_orderId":  order_sl["orderId"],
            }
        }

    except BinanceAPIException as e:
        logger.error(f"Buy order failed: {e}")
        return {"skipped": "api_error", "error": str(e)}

    except Exception as e:
        logger.exception(f"Unexpected error in execute_buy: {e}")
        return {"skipped": "unexpected_error", "error": str(e)}