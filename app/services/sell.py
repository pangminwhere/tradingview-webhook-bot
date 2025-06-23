# app/services/sell.py

import logging
import time
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


def _wait_for_position(symbol: str, target_amt: float) -> bool:
    """
    target_amt > 0 : 롱 진입 대기
    target_amt < 0 : 숏 진입 대기
    target_amt == 0: 청산 대기
    """
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
        if target_amt < 0 and current < 0:
            return True
        if target_amt == 0 and current == 0:
            return True

        time.sleep(POLL_INTERVAL)

    logger.warning(f"Timeout: target {target_amt}, current {current}")
    return False


def execute_sell(symbol: str) -> dict:
    """
    symbol 예: "ETHUSDT"
    숏 포지션 진입 → TP/SL 설정
    반환: {"sell": {"filled": x, "entry": y}} 또는 {"skipped": reason}
    """
    client = get_binance_client()

    # Dry-run 모드면 실제 주문 없이 로깅만
    if DRY_RUN:
        logger.info(f"[DRY_RUN] SELL {symbol}")
        return {"skipped": "dry_run"}

    # 1) 레버리지 설정
    client.futures_change_leverage(symbol=symbol, leverage=TRADE_LEVERAGE)
    logger.info(f"Leverage set to {TRADE_LEVERAGE}x for {symbol}")

    # 2) 기존 TP/SL 주문 취소
    for order in client.futures_get_open_orders(symbol=symbol):
        if order.get("reduceOnly"):
            client.futures_cancel_order(symbol=symbol, orderId=order["orderId"])
            logger.info(f"Canceled TP/SL order {order['orderId']}")

    # 3) 기존 포지션 체크 (롱 청산 or 이미 숏인 경우 처리)
    positions   = client.futures_position_information(symbol=symbol)
    current_amt = next(
        (float(p["positionAmt"]) for p in positions if p["symbol"] == symbol),
        0.0
    )

    if current_amt < 0:
        return {"skipped": "already_short"}

    if current_amt > 0:
        # 기존 롱 포지션 청산
        client.futures_create_order(
            symbol=symbol,
            side=SIDE_SELL,
            type=ORDER_TYPE_MARKET,
            quantity=current_amt,
            reduceOnly=True
        )
        if not _wait_for_position(symbol, 0.0):
            return {"skipped": "close_failed"}

    # 4) 진입 수량 계산
    bal_list = client.futures_account_balance()
    usdt_bal = float(next(item["balance"] for item in bal_list if item["asset"] == "USDT"))
    alloc    = usdt_bal * BUY_PCT
    price    = float(client.futures_symbol_ticker(symbol=symbol)["price"])
    qty      = round(alloc / price, 3)

    if qty <= 0:
        return {"skipped": "calc_zero"}

    # 5) 시장가 숏 진입
    order = client.futures_create_order(
        symbol=symbol,
        side=SIDE_SELL,
        type=ORDER_TYPE_MARKET,
        quantity=qty
    )
    if not _wait_for_position(symbol, -qty):
        return {"skipped": "open_failed"}

    filled      = float(order["executedQty"])
    entry_price = float(order["avgPrice"])
    logger.info(f"SELL executed {filled}@{entry_price}")

    # 6) TP/SL 주문 설정
    tp_qty   = round(filled * TP_PART_RATIO, 3)
    tp_price = entry_price / TP_RATIO
    sl_price = entry_price / SL_RATIO

    # 익절용 리밋 바이 (숏이므로 가격 하락 기대)
    client.futures_create_order(
        symbol=symbol,
        side=SIDE_BUY,
        type=ORDER_TYPE_LIMIT,
        timeInForce=TIME_IN_FORCE_GTC,
        quantity=tp_qty,
        price=str(tp_price),
        reduceOnly=True
    )
    # 손절용 스탑 마켓 바이
    client.futures_create_order(
        symbol=symbol,
        side=SIDE_BUY,
        type="STOP_MARKET",
        stopPrice=str(sl_price),
        quantity=filled,
        reduceOnly=True
    )

    return {"sell": {"filled": filled, "entry": entry_price}}