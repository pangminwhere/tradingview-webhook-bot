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
POLL_INTERVAL   = 1.0       # 포지션 상태 체크 간격(초)
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

    def _set_margin_and_leverage(self, market_id: str):
        try:
            self.exchange.set_margin_mode("isolated", market_id)
            logger.info(f"[Margin] ISOLATED for {market_id}")
        except Exception as e:
            logger.warning(f"[Margin] {e}")
        self.exchange.set_leverage(TRADE_LEVERAGE, market_id)
        logger.info(f"[Leverage] {TRADE_LEVERAGE}x for {market_id}")

    def _cancel_tp_sl(self, market_id: str):
        for o in self.exchange.fetch_open_orders(market_id):
            if o.get("info", {}).get("reduceOnly"):  # TP/SL 주문
                self.exchange.cancel_order(o["id"], market_id)
                logger.info(f"[Cancel] reduceOnly {o['id']}")

    def _position_amt(self) -> float:
        for p in self.exchange.fetch_positions():
            if p.get("symbol") == SYMBOL:
                return float(p["info"].get("positionAmt", 0))
        return 0.0

    def _wait_for_position(self, target: int) -> bool:
        start = time.time()
        while time.time() - start < MAX_WAIT:
            amt = self._position_amt()
            # target: 0=fully closed, >0=long open, <0=short open
            if target == 0 and amt == 0:
                return True
            if target > 0 and amt > 0:
                return True
            if target < 0 and amt < 0:
                return True
            time.sleep(POLL_INTERVAL)
        logger.warning(f"[Wait] position did not reach {target}, current {amt}")
        return False

    def _calc_qty(self) -> float:
        bal = self.exchange.fetch_balance()
        free_usdt = float(bal.get("free", {}).get("USDT", 0))
        alloc = free_usdt * BUY_PCT
        price = float(self.exchange.fetch_ticker(SYMBOL)["last"])
        market = self.exchange.market(SYMBOL)
        # 최소 notional 체크
        min_cost = market["limits"]["cost"]["min"]
        if alloc < min_cost:
            logger.warning(f"[CalcQty] alloc {alloc:.4f} < min_notional {min_cost}")
            return 0.0
        qty = alloc / price
        # 최소 수량 체크
        min_amt = market["limits"]["amount"]["min"]
        if qty < min_amt:
            qty = min_amt
        qty = float(self.exchange.amount_to_precision(SYMBOL, qty))
        logger.info(f"[CalcQty] use {qty} {SYMBOL} (alloc {alloc:.2f} USDT)")
        return qty

    def buy(self) -> dict:
        market_id = SYMBOL.replace("/", "")
        if DRY_RUN:
            logger.info("[DRY_RUN] buy skipped")
            return {"skipped": "dry_run"}
        # 1) 준비
        self.exchange.load_markets()
        self._set_margin_and_leverage(market_id)
        self._cancel_tp_sl(market_id)
        existing = self._position_amt()
        if existing > 0:
            logger.info("already long, skip buy")
            return {"skipped": "already_long"}
        # 2) 반대(숏) 포지션 청산
        if existing < 0:
            logger.info(f"closing short {existing}")
            self.exchange.create_market_buy_order(SYMBOL, abs(existing))
            if not self._wait_for_position(0):
                return {"skipped": "close_failed"}
        # 3) 잔고 기반 수량 계산
        qty = self._calc_qty()
        if qty <= 0:
            return {"skipped": "calc_zero"}
        # 4) 시장가 롱 진입
        order = self.exchange.create_market_buy_order(SYMBOL, qty)
        if not self._wait_for_position(1):
            return {"skipped": "open_failed"}
        filled = float(order.get("filled", qty))
        entry = float(order.get("average", order.get("price", 0)))
        logger.info(f"bought {filled}@{entry}")
        # 5) TP/SL
        tp_qty = filled * TP_PART_RATIO
        tp_price = entry * TP_RATIO
        sl_price = entry * SL_RATIO
        self.exchange.create_limit_sell_order(SYMBOL, tp_qty, tp_price, {"reduceOnly": True})
        self.exchange.create_order(SYMBOL, "STOP_MARKET", "sell", filled, None,
                                   {"stopPrice": sl_price, "reduceOnly": True})
        return {"buy": {"filled": filled, "entry": entry}}

    def sell(self) -> dict:
        market_id = SYMBOL.replace("/", "")
        if DRY_RUN:
            logger.info("[DRY_RUN] sell skipped")
            return {"skipped": "dry_run"}
        # 1) 준비
        self.exchange.load_markets()
        self._set_margin_and_leverage(market_id)
        self._cancel_tp_sl(market_id)
        existing = self._position_amt()
        if existing < 0:
            logger.info("already short, skip sell")
            return {"skipped": "already_short"}
        # 2) 반대(롱) 포지션 청산
        if existing > 0:
            logger.info(f"closing long {existing}")
            self.exchange.create_market_sell_order(SYMBOL, existing)
            if not self._wait_for_position(0):
                return {"skipped": "close_failed"}
        # 3) 잔고 기반 수량 계산
        qty = self._calc_qty()
        if qty <= 0:
            return {"skipped": "calc_zero"}
        # 4) 시장가 숏 진입
        order = self.exchange.create_market_sell_order(SYMBOL, qty)
        if not self._wait_for_position(-1):
            return {"skipped": "open_failed"}
        filled = float(order.get("filled", qty))
        entry = float(order.get("average", order.get("price", 0)))
        logger.info(f"sold short {filled}@{entry}")
        # 5) TP/SL
        tp_qty = filled * TP_PART_RATIO
        tp_price = entry / TP_RATIO
        sl_price = entry / SL_RATIO
        self.exchange.create_limit_buy_order(SYMBOL, tp_qty, tp_price, {"reduceOnly": True})
        self.exchange.create_order(SYMBOL, "STOP_MARKET", "buy", filled, None,
                                   {"stopPrice": sl_price, "reduceOnly": True})
        return {"sell": {"filled": filled, "entry": entry}}