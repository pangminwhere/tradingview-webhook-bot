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
POLL_INTERVAL   = 1.0       # 포지션 상태 체크 폴링 간격(초)
MAX_WAIT        = 10        # 최대 대기 시간(초)
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

    def _set_isolated(self, market_id: str):
        try:
            self.exchange.set_margin_mode("isolated", market_id)
            logger.info(f"[Margin] ISOLATED for {market_id}")
        except Exception as e:
            logger.warning(f"[Margin] {e}")

    def _set_leverage(self, market_id: str):
        self.exchange.set_leverage(TRADE_LEVERAGE, market_id)
        logger.info(f"[Leverage] {TRADE_LEVERAGE}x")

    def _cancel_tp_sl(self, market_id: str):
        for o in self.exchange.fetch_open_orders(market_id):
            if o.get("info", {}).get("reduceOnly"):
                self.exchange.cancel_order(o["id"], market_id)
                logger.info(f"[Cancel] reduceOnly {o['id']}")

    def _position_amt(self) -> float:
        # 현재 포지션 크기 조회
        for p in self.exchange.fetch_positions():
            if p.get("symbol") == SYMBOL:
                return float(p["info"].get("positionAmt", 0))
        return 0.0

    def _wait_for_position(self, target_sign: int) -> bool:
        # target_sign >0: 기다려서 롱 진입, <0: 숏 진입, 0: 청산 완료
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
        logger.warning(f"[Wait] Position {target_sign} not reached ({amt})")
        return False

    def _calc_qty(self) -> float:
        bal = self.exchange.fetch_balance()
        free_usdt = float(bal["free"].get("USDT", 0))
        alloc_usdt = free_usdt * BUY_PCT
        price = float(self.exchange.fetch_ticker(SYMBOL)["last"])
        market = self.exchange.market(SYMBOL)
        min_cost = market["limits"]["cost"]["min"]
        if alloc_usdt < min_cost:
            return 0.0
        raw_qty = alloc_usdt / price
        min_amt = market["limits"]["amount"]["min"]
        if raw_qty < min_amt:
            raw_qty = min_amt
        return float(self.exchange.amount_to_precision(SYMBOL, raw_qty))

    def buy(self) -> dict:
        market_id = SYMBOL.replace("/", "")
        if DRY_RUN:
            return {"skipped": "dry_run"}
        # 준비
        self.exchange.load_markets()
        self._set_isolated(market_id)
        self._set_leverage(market_id)
        self._cancel_tp_sl(market_id)
        existing = self._position_amt()
        if existing > 0:
            return {"skipped": "already_long"}
        # 1) 반대(숏) 청산
        if existing < 0:
            self.exchange.create_market_buy_order(SYMBOL, abs(existing))
            logger.info(f"[CloseShort] {existing}")
            if not self._wait_for_position(0):
                return {"skipped": "close_failed"}
            trade_qty = abs(existing)
        else:
            # 신규
            qty = self._calc_qty()
            if qty <= 0:
                return {"skipped": "calc_zero"}
            trade_qty = qty
        # 2) 시장가 롱 진입
        order = self.exchange.create_market_buy_order(SYMBOL, trade_qty)
        if not self._wait_for_position(1):
            return {"skipped": "open_failed"}
        filled = float(order.get("filled", 0))
        entry = float(order.get("average", order.get("price", 0)))
        # 3) TP/SL
        tp = filled * TP_PART_RATIO; tp_price = entry * TP_RATIO
        sl_price = entry * SL_RATIO
        self.exchange.create_limit_sell_order(SYMBOL, tp, tp_price, {"reduceOnly": True})
        self.exchange.create_order(SYMBOL, "STOP_MARKET", "sell", filled, None,
                                   {"stopPrice": sl_price, "reduceOnly": True})
        return {"buy": {"filled": filled, "entry": entry}}

    def sell(self) -> dict:
        market_id = SYMBOL.replace("/", "")
        if DRY_RUN:
            return {"skipped": "dry_run"}
        # 준비
        self.exchange.load_markets()
        self._set_isolated(market_id)
        self._set_leverage(market_id)
        self._cancel_tp_sl(market_id)
        existing = self._position_amt()
        if existing < 0:
            return {"skipped": "already_short"}
        # 1) 반대(롱) 청산
        if existing > 0:
            self.exchange.create_market_sell_order(SYMBOL, existing)
            logger.info(f"[CloseLong] {existing}")
            if not self._wait_for_position(0):
                return {"skipped": "close_failed"}
            trade_qty = existing
        else:
            qty = self._calc_qty()
            if qty <= 0:
                return {"skipped": "calc_zero"}
            trade_qty = qty
        # 2) 시장가 숏 진입
        order = self.exchange.create_market_sell_order(SYMBOL, trade_qty)
        if not self._wait_for_position(-1):
            return {"skipped": "open_failed"}
        filled = float(order.get("filled", 0))
        entry = float(order.get("average", order.get("price", 0)))
        # 3) TP/SL
        tp = filled * TP_PART_RATIO; tp_price = entry / TP_RATIO
        sl_price = entry / SL_RATIO
        self.exchange.create_limit_buy_order(SYMBOL, tp, tp_price, {"reduceOnly": True})
        self.exchange.create_order(SYMBOL, "STOP_MARKET", "buy", filled, None,
                                   {"stopPrice": sl_price, "reduceOnly": True})
        return {"sell": {"filled": filled, "entry": entry}}