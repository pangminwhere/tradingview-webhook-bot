import threading
import time
import logging
from binance import ThreadedWebsocketManager
from app.clients.binance_client import get_binance_client
from app.state import monitor_state
from app.config import POLL_INTERVAL


def _handle_order_update(msg):
    """
    User Data Stream 으로부터 주문 체결 이벤트를 수신합니다.
    마켓 BUY 체결 시 entry_price, position_qty 초기화
    """
    if msg.get("e") == "ORDER_TRADE_UPDATE":
        o = msg["o"]
        # 마켓 BUY 주문이 체결되면 entry 설정
        if o.get("X") == "FILLED" and o.get("S") == "BUY" and o.get("o") == "MARKET":
            monitor_state["entry_price"]   = float(o["L"])  # 체결 평균가
            monitor_state["position_qty"]  = float(o["q"])  # 체결 수량
            monitor_state["first_tp_done"]  = False
            monitor_state["second_tp_done"] = False


def _poll_price_loop():
    """
    3초마다 현재가를 폴링하며 PnL을 계산하고,
    지정된 수익/손절 조건에 맞춰 시장가 청산을 실행합니다.
    """
    client = get_binance_client()
    symbol = monitor_state["symbol"]

    while True:
        qty = monitor_state["position_qty"]
        if qty > 0:
            # 현재가 조회
            ticker = client.futures_symbol_ticker(symbol=symbol)
            current = float(ticker["price"])
            entry   = monitor_state["entry_price"]
            monitor_state["current_price"] = current
            monitor_state["pnl"]           = (current / entry - 1) * 100

            # 1차 익절: +0.5%, 30%
            if not monitor_state["first_tp_done"] and current >= entry * 1.005:
                client.futures_create_order(
                    symbol=symbol,
                    side="SELL",
                    type="MARKET",
                    quantity=qty * 0.3
                )
                monitor_state["position_qty"]  -= qty * 0.3
                monitor_state["first_tp_done"]  = True

            # 2차 익절: +1.1%, 50%
            elif monitor_state["first_tp_done"] and not monitor_state["second_tp_done"] and current >= entry * 1.011:
                client.futures_create_order(
                    symbol=symbol,
                    side="SELL",
                    type="MARKET",
                    quantity=qty * 0.5
                )
                monitor_state["position_qty"]   -= qty * 0.5
                monitor_state["second_tp_done"]  = True

            # 손절: -0.5% 또는 1차 익절 후 +0.1%
            sl_thresh = entry * (1.001 if monitor_state["first_tp_done"] else 0.995)
            if current <= sl_thresh and monitor_state["position_qty"] > 0:
                client.futures_create_order(
                    symbol=symbol,
                    side="SELL",
                    type="MARKET",
                    quantity=monitor_state["position_qty"]
                )
                monitor_state["position_qty"] = 0

        time.sleep(3)


def start_monitor():
    """
    WebsocketManager와 폴링 스레드를 띄웁니다.
    초기화 예외는 잡아서 로그만 남기고 조용히 종료합니다.
    """
    client = get_binance_client()
    twm = ThreadedWebsocketManager(
        api_key=client.API_KEY,
        api_secret=client.API_SECRET
    )

    # Websocket 초기화
    try:
        twm.start()
        # 선물 유저 데이터 스트림 구독
        twm.start_futures_user_socket(callback=_handle_order_update)
    except Exception:
        logging.getLogger("monitor").exception("WebsocketManager 초기화 실패")
        return

    # 폴링 스레드 안전 실행
    def poll_safe():
        try:
            _poll_price_loop()
        except Exception:
            logging.getLogger("monitor").exception("가격 폴링 실패")

    thread = threading.Thread(target=poll_safe, daemon=True)
    thread.start()