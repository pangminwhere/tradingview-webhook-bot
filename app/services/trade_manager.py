import logging
import time
import ccxt
from app.config import EX_API_KEY, EX_API_SECRET, DRY_RUN

logger = logging.getLogger("trade_manager")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# ── 설정값 ────────────────────────────────────
SYMBOL          = "ETH/USDT"
BUY_PCT         = 0.98      # 잔고의 98% 사용
TRADE_LEVERAGE  = 1         # 1배 레버리지
TP_RATIO        = 1.005     # +0.5% 익절
TP_PART_RATIO   = 0.5       # 익절 비율 50%
SL_RATIO        = 0.995     # -0.5% 손절
POLL_INTERVAL   = 1.0       # 포지션 체크 간격(초)
MAX_WAIT        = 15        # 최대 대기 시간(초)
# ─────────────────────────────────────────────

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

    def _prepare(self, market_id: str):
        self.exchange.load_markets()
        self.exchange.set_margin_mode("isolated", market_id)
        self.exchange.set_leverage(TRADE_LEVERAGE, market_id)
        # 기존 TP/SL 주문 취소
        for o in self.exchange.fetch_open_orders(market_id):
            if o.get("info", {}).get("reduceOnly"):
                self.exchange.cancel_order(o["id"], market_id)
                logger.info(f"Canceled TP/SL {o['id']}")

    def _position_amt(self) -> float:
        for p in self.exchange.fetch_positions():
            if p.get("symbol") == SYMBOL:
                return float(p["info"].get("positionAmt", 0))
        return 0.0

    def _wait_for(self, target_sign: int) -> bool:
        start = time.time()
        while time.time() - start < MAX_WAIT:
            amt = self._position_amt()
            if target_sign == 0 and amt == 0:
                return True
            if target_sign > 0 and amt > 0:
                return True
            if target_sign < 0 and amt < 0:
                return True
            time.sleep(POLL_INTERVAL)
        logger.warning(f"Timeout waiting for position {target_sign}, current {amt}")
        return False

    def _calc_qty(self) -> float:
        bal = self.exchange.fetch_balance()
        free = float(bal["free"].get("USDT", 0))
        alloc = free * BUY_PCT
        price = float(self.exchange.fetch_ticker(SYMBOL)["last"])
        market = self.exchange.market(SYMBOL)
        if alloc < market["limits"]["cost"]["min"]:
            logger.warning("Alloc < min notional, skip")
            return 0.0
        qty = alloc / price
        min_amt = market["limits"]["amount"]["min"]
        if qty < min_amt:
            qty = min_amt
        return float(self.exchange.amount_to_precision(SYMBOL, qty))

    def buy(self) -> dict:
        mid = SYMBOL.replace("/", "")
        if DRY_RUN:
            return {"skipped": "dry_run"}
        # 준비
        self._prepare(mid)
        existing = self._position_amt()
        if existing > 0:
            return {"skipped": "already_long"}
        # 반대(숏) 포지션 청산
        if existing < 0:
            self.exchange.create_order(
                SYMBOL, "MARKET", "buy", abs(existing), None,
                {"reduceOnly": True}
            )
            if not self._wait_for(0):
                return {"skipped": "close_failed"}
        # 신규 진입 수량
        qty = self._calc_qty()
        if qty <= 0:
            return {"skipped": "calc_zero"}
        # 시장가 롱 진입
        order = self.exchange.create_market_buy_order(SYMBOL, qty)
        if not self._wait_for(1):
            return {"skipped": "open_failed"}
        filled = float(order.get("filled", qty))
        entry = float(order.get("average", order.get("price", 0)))
        logger.info(f"BUY executed {filled}@{entry}")
        # TP/SL 설정
        tp_qty = filled * TP_PART_RATIO
        tp_price = entry * TP_RATIO
        sl_price = entry * SL_RATIO
        self.exchange.create_limit_sell_order(
            SYMBOL, tp_qty, tp_price, {"reduceOnly": True}
        )
        self.exchange.create_order(
            SYMBOL, "STOP_MARKET", "sell", filled, None,
            {"stopPrice": sl_price, "reduceOnly": True}
        )
        return {"buy": {"filled": filled, "entry": entry}}

    def sell(self) -> dict:
        mid = SYMBOL.replace("/", "")
        if DRY_RUN:
            return {"skipped": "dry_run"}
        # 준비
        self._prepare(mid)
        existing = self._position_amt()
        if existing < 0:
            return {"skipped": "already_short"}
        # 반대(롱) 포지션 청산
        if existing > 0:
            self.exchange.create_order(
                SYMBOL, "MARKET", "sell", existing, None,
                {"reduceOnly": True}
            )
            if not self._wait_for(0):
                return {"skipped": "close_failed"}
        # 신규 진입 수량
        qty = self._calc_qty()
        if qty <= 0:
            return {"skipped": "calc_zero"}
        # 시장가 숏 진입
        order = self.exchange.create_market_sell_order(SYMBOL, qty)
        if not self._wait_for(-1):
            return {"skipped": "open_failed"}
        filled = float(order.get("filled", qty))
        entry = float(order.get("average", order.get("price", 0)))
        logger.info(f"SELL executed {filled}@{entry}")
        # TP/SL 설정
        tp_qty = filled * TP_PART_RATIO
        tp_price = entry / TP_RATIO
        sl_price = entry / SL_RATIO
        self.exchange.create_limit_buy_order(
            SYMBOL, tp_qty, tp_price, {"reduceOnly": True}
        )
        self.exchange.create_order(
            SYMBOL, "STOP_MARKET", "buy", filled, None,
            {"stopPrice": sl_price, "reduceOnly": True}
        )
        return {"sell": {"filled": filled, "entry": entry}}