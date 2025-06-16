# app/state.py

# 가장 최근 체결된 포지션 정보를 간단히 저장
last_trade = {
    "symbol": None,   # ex: "ETH/USDT"
    "side":   None,   # "long" or "short"
    "entry":  0.0     # 진입 평균가
}