import threading
import time
from binance import ThreadedWebsocketManager
from app.clients.binance_client import get_binance_client
from app.state import monitor_state
from app.config import POLL_INTERVAL

def _handle_order_update(msg):
    # 사용자 데이터 스트림: 주문 체결 이벤트
    if msg.get('e') == 'ORDER_TRADE_UPDATE':
        o = msg['o']
        # BUY 진입 체결
        if o.get('X') == 'FILLED' and o.get('S') == 'BUY' and o.get('o') == 'MARKET':
            monitor_state['entry_price'] = float(o['L'])  # 체결 평균가
            monitor_state['position_qty'] = float(o['q'])
            monitor_state['first_tp_done'] = False
            monitor_state['second_tp_done'] = False
            
def _poll_price_loop():
    client = get_binance_client()
    symbol = monitor_state['symbol']
    while True:
        qty = monitor_state['position_qty']
        if qty > 0:
            ticker = client.futures_symbol_ticker(symbol=symbol)
            current = float(ticker['price'])
            entry = monitor_state['entry_price']
            monitor_state['current_price'] = current
            monitor_state['pnl'] = (current / entry - 1) * 100

            # 1차 익절: +0.5%, 30%
            if not monitor_state['first_tp_done'] and current >= entry * 1.005:
                client.futures_create_order(
                    symbol=symbol,
                    side='SELL', type='MARKET',
                    quantity=qty * 0.3
                )
                monitor_state['position_qty'] -= qty * 0.3
                monitor_state['first_tp_done'] = True
            
            # 2차 익절: +1.1%, 50%
            elif monitor_state['first_tp_done'] and not monitor_state['second_tp_done'] and current >= entry * 1.011:
                client.futures_create_order(
                    symbol=symbol,
                    side='SELL', type='MARKET',
                    quantity=qty * 0.5
                )
                monitor_state['position_qty'] -= qty * 0.5
                monitor_state['second_tp_done'] = True
                
            # 손절: -0.5% or +0.1% after 1차
            sl_thresh = entry * (1.001 if monitor_state['first_tp_done'] else 0.995)
            if current <= sl_thresh and monitor_state['position_qty'] > 0:
                client.futures_create_order(
                    symbol=symbol,
                    side='SELL', type='MARKET',
                    quantity=monitor_state['position_qty']
                )
                monitor_state['position_qty'] = 0

        time.sleep(3)
        
def start_monitor():
    client = get_binance_client()
    
    twm = ThreadedWebsocketManager(
        api_key=client.API_KEY,
        api_secret=client.API_SECRET
    )
    twm.start()
    # user data(주문 체결) 스트림 구독
    twm.start_user_socket(callback=_handle_order_update)

    # 2) 폴링 스레드 시작
    thread = threading.Thread(target=_poll_price_loop, daemon=True)
    thread.start()