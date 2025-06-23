# app/services/switching.py

import logging
import time
from binance.enums import SIDE_BUY, SIDE_SELL, ORDER_TYPE_MARKET
from app.clients.binance_client import get_binance_client
from app.config import DRY_RUN, POLL_INTERVAL, MAX_WAIT
from app.services.buy import execute_buy
from app.services.sell import execute_sell

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def _wait_for(symbol: str, target_amt: float) -> bool:
    """
    target_amt > 0 : 롱 포지션 대기
    target_amt < 0 : 숏 포지션 대기
    target_amt == 0: 포지션 청산 대기
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

    logger.warning(f"Switch timeout: target {target_amt}, current {current}")
    return False


def switch_position(symbol: str, action: str) -> dict:
    """
    symbol 예: "ETHUSDT"
    action: "BUY" 또는 "SELL"
    - 현재 포지션이 action 과 같으면 건너뜀
    - 반대 포지션이 남아있으면 시장가로 청산 → 새 포지션 진입
    """
    client = get_binance_client()

    if DRY_RUN:
        logger.info(f"[DRY_RUN] switch_position {action} {symbol}")
        return {"skipped": "dry_run"}

    # 1) 현재 보유량 조회
    positions = client.futures_position_information(symbol=symbol)
    current_amt = next(
        (float(p["positionAmt"]) for p in positions if p["symbol"] == symbol),
        0.0
    )

    # 2) BUY 신호 처리
    if action.upper() == "BUY":
        if current_amt > 0:
            return {"skipped": "already_long"}

        # 숏 포지션 있으면 청산
        if current_amt < 0:
            logger.info(f"Closing SHORT {abs(current_amt)} @ market for {symbol}")
            client.futures_create_order(
                symbol=symbol,
                side=SIDE_BUY,
                type=ORDER_TYPE_MARKET,
                quantity=abs(current_amt),
                reduceOnly=True
            )
            if not _wait_for(symbol, 0.0):
                return {"skipped": "close_failed"}

        # 롱 진입
        return execute_buy(symbol)

    # 3) SELL 신호 처리
    if action.upper() == "SELL":
        if current_amt < 0:
            return {"skipped": "already_short"}

        # 롱 포지션 있으면 청산
        if current_amt > 0:
            logger.info(f"Closing LONG {current_amt} @ market for {symbol}")
            client.futures_create_order(
                symbol=symbol,
                side=SIDE_SELL,
                type=ORDER_TYPE_MARKET,
                quantity=current_amt,
                reduceOnly=True
            )
            if not _wait_for(symbol, 0.0):
                return {"skipped": "close_failed"}

        # 숏 진입
        return execute_sell(symbol)

    # 4) 알 수 없는 action
    logger.error(f"Unknown action for switch: {action}")
    return {"skipped": "unknown_action"}