# app/state.py

# 가장 최근 체결된 포지션 정보를 간단히 저장
last_trade = {
    "symbol": None,   # ex: "ETH/USDT"
    "side":   None,   # "long" or "short"
    "entry":  0.0     # 진입 평균가
}

# app/state.py

# 기존 last_trade 외에 아래 추가
monitor_state = {
    "symbol": "ETHUSDT",       # 모니터링 심볼
    "entry_price": 0.0,
    "position_qty": 0.0,
    "current_price": 0.0,
    "pnl": 0.0,                 # % 수익률
    "first_tp_done": False,
    "second_tp_done": False,
}