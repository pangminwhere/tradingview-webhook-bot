import logging
import ccxt
from app.config import EX_API_KEY, EX_API_SECRET, DRY_RUN

logger = logging.getLogger("trade_manager")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

SYMBOL          = "ETH/USDT"
BUY_PCT         = 0.98      # 잔고의 98% 사용
TRADE_LEVERAGE  = 1         # 1배 레버리지
TP_RATIO        = 1.005     # +0.5% 익절
TP_PART_RATIO   = 0.5       # 익절 비율 50%
SL_RATIO        = 0.995     # -0.5% 손절

class TradeManager:
    def __init__(self, testnet: bool = False):
        params = {
            "apiKey": EX_API_KEY,
            "secret": EX_API_SECRET,
            "enableRateLimit": True,
            "options": {"defaultType": "future"},
        }
        self.exchange = ccxt.binanceusdm(params)
        if testnet:
            self.exchange.set_sandbox_mode(True)

    def _prepare(self):
        mid = SYMBOL.replace("/", "")
        self.exchange.load_markets()
        self.exchange.set_margin_mode("isolated", mid)
        self.exchange.set_leverage(TRADE_LEVERAGE, mid)
        # 기존 TP/SL 주문 취소
        for o in self.exchange.fetch_open_orders(mid):
            if o.get("info", {}).get("reduceOnly"):
                self.exchange.cancel_order(o["id"], mid)
                logger.info(f"Canceled TP/SL order {o['id']}")

    def _calc_qty(self) -> float:
        bal = self.exchange.fetch_balance()
        free = float(bal["free"].get("USDT", 0))
        alloc = free * BUY_PCT
        price = float(self.exchange.fetch_ticker(SYMBOL)["last"])
        # 최소 주문 금액(min notional) 검사
        market = self.exchange.market(SYMBOL)
        if alloc < market["limits"]["cost"]["min"]:
            logger.warning("할당 USDT가 최소 주문 금액보다 작습니다.")
            return 0.0
        qty = alloc / price
        # 최소 수량 검사
        min_amt = market["limits"]["amount"]["min"]
        if qty < min_amt:
            qty = min_amt
        return float(self.exchange.amount_to_precision(SYMBOL, qty))

    def buy(self) -> dict:
        if DRY_RUN:
            logger.info("[DRY_RUN] buy skipped")
            return {"skipped": "dry_run"}

        self._prepare()
        qty = self._calc_qty()
        if qty <= 0:
            logger.info("주문 수량 계산 결과 0, buy 스킵")
            return {"skipped": "calc_zero"}

        # 1) 시장가 매수
        order = self.exchange.create_market_buy_order(SYMBOL, qty)
        filled = float(order.get("filled", qty))
        entry = float(order.get("average", order.get("price", 0)))
        logger.info(f"BUY executed: {filled}@{entry}")

        # 2) 1차 익절(limit)
        tp_qty   = filled * TP_PART_RATIO
        tp_price = entry * TP_RATIO
        self.exchange.create_limit_sell_order(
            SYMBOL, tp_qty, tp_price, {"reduceOnly": True}
        )
        logger.info(f"TP set: {tp_qty}@{tp_price}")

        # 3) 손절(stop-market)
        sl_price = entry * SL_RATIO
        self.exchange.create_order(
            SYMBOL, "STOP_MARKET", "sell", filled, None,
            {"stopPrice": sl_price, "reduceOnly": True}
        )
        logger.info(f"SL set: {filled}@{sl_price}")

        return {"buy": {"filled": filled, "entry": entry}}

    def sell(self) -> dict:
        if DRY_RUN:
            logger.info("[DRY_RUN] sell skipped")
            return {"skipped": "dry_run"}

        self._prepare()
        qty = self._calc_qty()
        if qty <= 0:
            logger.info("주문 수량 계산 결과 0, sell 스킵")
            return {"skipped": "calc_zero"}

        # 1) 시장가 매도(숏 진입)
        order = self.exchange.create_market_sell_order(SYMBOL, qty)
        filled = float(order.get("filled", qty))
        entry = float(order.get("average", order.get("price", 0)))
        logger.info(f"SELL executed: {filled}@{entry}")

        # 2) 1차 익절(limit)
        tp_qty   = filled * TP_PART_RATIO
        tp_price = entry / TP_RATIO
        self.exchange.create_limit_buy_order(
            SYMBOL, tp_qty, tp_price, {"reduceOnly": True}
        )
        logger.info(f"SHORT TP set: {tp_qty}@{tp_price}")

        # 3) 손절(stop-market)
        sl_price = entry / SL_RATIO
        self.exchange.create_order(
            SYMBOL, "STOP_MARKET", "buy", filled, None,
            {"stopPrice": sl_price, "reduceOnly": True}
        )
        logger.info(f"SHORT SL set: {filled}@{sl_price}")

        return {"sell": {"filled": filled, "entry": entry}}