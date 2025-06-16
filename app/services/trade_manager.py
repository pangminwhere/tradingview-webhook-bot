import logging
import ccxt

logger = logging.getLogger("trade_manager")

# 거래 파라미터
TRADE_AMOUNT   = 50     # USDT
TRADE_LEVERAGE = 10     # 10배 레버리지
TP_RATIO       = 1.01   # +1% 익절
TP_PART_RATIO  = 0.5    # 50% 익절

class TradeManager:
    def __init__(self, api_key: str, secret: str):
        self.exchange = ccxt.binance({
            "apiKey": api_key,
            "secret": secret,
            "options": {"defaultType": "future"}
        })

    def _cancel_tp_orders(self, symbol: str):
        for o in self.exchange.fetch_open_orders(symbol):
            if o.get("info", {}).get("reduceOnly"):
                self.exchange.cancel_order(o["id"], symbol)
                logger.info(f"Canceled TP order {o['id']}")

    def _fetch_position_amount(self, symbol: str) -> float:
        pos_amt = 0.0
        for p in self.exchange.fetch_positions([symbol]):
            if p["symbol"] == symbol:
                pos_amt = float(p["info"]["positionAmt"])
        return pos_amt

    def buy(self, symbol: str):
        results = {}
        # 1) 레버리지 설정
        self.exchange.set_leverage(TRADE_LEVERAGE, symbol)
        # 2) 기존 TP 주문 취소
        self._cancel_tp_orders(symbol)
        # 3) 현재 숏 포지션이 있으면 전량 청산
        pos_amt = self._fetch_position_amount(symbol)
        if pos_amt < 0:
            close_short = self.exchange.create_market_buy_order(symbol, abs(pos_amt))
            results["close_short"] = close_short
            logger.info(f"Closed short: qty={abs(pos_amt)}")
        # 4) 이미 롱 포지션이면 진입 방어
        pos_amt = self._fetch_position_amount(symbol)
        if pos_amt > 0:
            logger.info("Already long, skipping new buy entry")
            return results
        # 5) 시장가 롱 진입
        order = self.exchange.create_market_buy_order(symbol, TRADE_AMOUNT)
        filled = float(order.get("filled", order.get("amount", 0)))
        avg    = float(order.get("average", order.get("price", 0)))
        results["buy"] = order
        # 6) 1차 익절 리밋 걸기
        tp_qty   = filled * TP_PART_RATIO
        tp_price = avg * TP_RATIO
        tp_order = self.exchange.create_limit_sell_order(
            symbol, tp_qty, tp_price, {"reduceOnly": True}
        )
        results["tp"] = tp_order
        logger.info(f"BUY: {filled}@{avg}, TP: {tp_qty}@{tp_price}")
        return results

    def sell(self, symbol: str):
        results = {}
        # 1) 레버리지 설정
        self.exchange.set_leverage(TRADE_LEVERAGE, symbol)
        # 2) 기존 TP 주문 취소
        self._cancel_tp_orders(symbol)
        # 3) 현재 롱 포지션이 있으면 전량 청산
        pos_amt = self._fetch_position_amount(symbol)
        if pos_amt > 0:
            close_long = self.exchange.create_market_sell_order(symbol, pos_amt)
            results["close_long"] = close_long
            logger.info(f"Closed long: qty={pos_amt}")
        # 4) 이미 숏이면 진입 방어
        pos_amt = self._fetch_position_amount(symbol)
        if pos_amt < 0:
            logger.info("Already short, skipping new short entry")
            return results
        # 5) 시장가 숏 진입
        short = self.exchange.create_market_sell_order(symbol, TRADE_AMOUNT)
        results["short"] = short
        logger.info(f"Opened short: amount={TRADE_AMOUNT}")
        return results