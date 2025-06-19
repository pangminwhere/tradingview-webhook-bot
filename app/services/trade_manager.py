# app/services/trade_manager.py

import logging
import ccxt

logger = logging.getLogger("trade_manager")

# —————————————————————
# 거래 파라미터
TRADE_LEVERAGE  = 1       # 1배 레버리지
TP_RATIO        = 1.005   # +0.5% 익절
TP_PART_RATIO   = 0.5     # 익절 물량 비율(50%)
SL_RATIO        = 0.995   # -0.5% 손절
BUY_PCT         = 0.98    # 잔고의 98% 사용
# —————————————————————

class TradeManager:
    def __init__(self, api_key: str, secret: str):
        self.exchange = ccxt.binance({
            "apiKey":    api_key,
            "secret":    secret,
            "options":   {"defaultType": "future"}
        })

    def _cancel_tp_orders(self, symbol: str):
        for o in self.exchange.fetch_open_orders(symbol):
            if o.get("info", {}).get("reduceOnly"):
                self.exchange.cancel_order(o["id"], symbol)
                logger.info(f"Canceled reduceOnly order {o['id']}")

    def _fetch_position_amount(self, symbol: str) -> float:
        amt = 0.0
        for p in self.exchange.fetch_positions([symbol]):
            if p["symbol"] == symbol:
                amt = float(p["info"]["positionAmt"])
        return amt

    def _calc_buy_amount(self) -> float:
        bal = self.exchange.fetch_balance()
        free_usdt = float(bal["free"]["USDT"])
        return free_usdt * BUY_PCT

    def buy(self, symbol: str):
        results = {}

        # 1) 레버리지 설정
        self.exchange.set_leverage(TRADE_LEVERAGE, symbol)

        # 2) 기존 TP/SL 주문 취소
        self._cancel_tp_orders(symbol)

        # 3) 숏 포지션 전량 청산
        pos_amt = self._fetch_position_amount(symbol)
        if pos_amt < 0:
            close_short = self.exchange.create_market_buy_order(symbol, abs(pos_amt))
            results["close_short"] = close_short
            logger.info(f"Closed short: qty={abs(pos_amt)}")

        # 4) 이미 롱이면 건너뛰기
        pos_amt = self._fetch_position_amount(symbol)
        if pos_amt > 0:
            logger.info("Already long, skipping new buy")
            return results

        # 5) 시장가 롱 진입 (잔고의 98% 사용)
        buy_qty_usdt = self._calc_buy_amount()
        order = self.exchange.create_order(
            symbol, "market", "buy",
            None, None,
            {"quoteOrderQty": buy_qty_usdt}
        )
        filled_qty  = float(order.get("filled", order.get("amount", 0)))
        entry_price = float(order.get("average", order.get("price", 0)))
        results["buy"] = order
        logger.info(f"BUY executed: ${buy_qty_usdt} → qty={filled_qty}@{entry_price}")

        # 6) 1차 익절 리밋 주문
        tp_qty   = filled_qty * TP_PART_RATIO
        tp_price = entry_price * TP_RATIO
        tp_order = self.exchange.create_limit_sell_order(
            symbol, tp_qty, tp_price, {"reduceOnly": True}
        )
        results["tp"] = tp_order
        logger.info(f"TP order: qty={tp_qty}@{tp_price}")

        # 7) 손절 주문
        sl_price = entry_price * SL_RATIO
        sl_order = self.exchange.create_order(
            symbol, "STOP_MARKET", "sell", filled_qty, None,
            {"stopPrice": sl_price, "reduceOnly": True}
        )
        results["sl"] = sl_order
        logger.info(f"SL order: qty={filled_qty}@{sl_price}")

        return results

    def sell(self, symbol: str):
        results = {}

        # 1) 레버리지 설정
        self.exchange.set_leverage(TRADE_LEVERAGE, symbol)

        # 2) 기존 TP/SL 주문 취소
        self._cancel_tp_orders(symbol)

        # 3) 롱 포지션 전량 청산
        pos_amt = self._fetch_position_amount(symbol)
        if pos_amt > 0:
            close_long = self.exchange.create_market_sell_order(symbol, pos_amt)
            results["close_long"] = close_long
            logger.info(f"Closed long: qty={pos_amt}")

        # 4) 이미 숏이면 건너뛰기
        pos_amt = self._fetch_position_amount(symbol)
        if pos_amt < 0:
            logger.info("Already short, skipping new short")
            return results

        # 5) 시장가 숏 진입 (잔고의 98% 사용)
        sell_qty_usdt = self._calc_buy_amount()
        order = self.exchange.create_order(
            symbol, "market", "sell",
            None, None,
            {"quoteOrderQty": sell_qty_usdt}
        )
        filled_qty  = float(order.get("filled", order.get("amount", 0)))
        entry_price = float(order.get("average", order.get("price", 0)))
        results["short"] = order
        logger.info(f"SHORT executed: ${sell_qty_usdt} → qty={filled_qty}@{entry_price}")

        # 6) 숏 TP 주문
        tp_qty   = filled_qty * TP_PART_RATIO
        tp_price = entry_price / TP_RATIO
        tp_order = self.exchange.create_limit_buy_order(
            symbol, tp_qty, tp_price, {"reduceOnly": True}
        )
        results["tp"] = tp_order
        logger.info(f"SHORT TP order: qty={tp_qty}@{tp_price}")

        # 7) 숏 SL 주문
        sl_price = entry_price / SL_RATIO
        sl_order = self.exchange.create_order(
            symbol, "STOP_MARKET", "buy", filled_qty, None,
            {"stopPrice": sl_price, "reduceOnly": True}
        )
        results["sl"] = sl_order
        logger.info(f"SHORT SL order: qty={filled_qty}@{sl_price}")

        return results