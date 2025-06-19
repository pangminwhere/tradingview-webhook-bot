import logging
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
            logger.info(f"[Margin] Set ISOLATED for {market_id}")
        except Exception as e:
            logger.warning(f"[Margin] Could not set isolated: {e}")

    def _set_leverage(self, market_id: str):
        self.exchange.set_leverage(TRADE_LEVERAGE, market_id)
        logger.info(f"[Leverage] {TRADE_LEVERAGE}x for {market_id}")

    def _cancel_tp_sl(self, market_id: str):
        for o in self.exchange.fetch_open_orders(market_id):
            if o.get("info", {}).get("reduceOnly"):
                self.exchange.cancel_order(o["id"], market_id)
                logger.info(f"[Cancel] reduceOnly {o['id']}")

    def _position_amt(self) -> float:
        for p in self.exchange.fetch_positions():
            if p.get("symbol") == SYMBOL:
                return float(p["info"].get("positionAmt", 0))
        return 0.0

    def _calc_qty(self) -> float:
        bal = self.exchange.fetch_balance()
        free_usdt = float(bal["free"].get("USDT", 0))
        alloc_usdt = free_usdt * BUY_PCT
        price = float(self.exchange.fetch_ticker(SYMBOL)["last"])
        raw_qty = alloc_usdt / price

        market = self.exchange.market(SYMBOL)
        min_amt = market["limits"]["amount"]["min"]
        if raw_qty < min_amt:
            logger.warning(f"[CalcQty] {raw_qty:.6f} < min_amt {min_amt:.6f}, using min_amt")
            raw_qty = min_amt

        qty = float(self.exchange.amount_to_precision(SYMBOL, raw_qty))
        logger.info(f"[CalcQty] Free {free_usdt:.4f} → Alloc {alloc_usdt:.4f} → Qty {qty:.6f}")
        return qty

    def buy(self):
        market_id = SYMBOL.replace("/", "")
        if DRY_RUN:
            logger.info("[DRY_RUN] BUY skipped")
            return {}

        self.exchange.load_markets()
        self._set_isolated(market_id)
        self._set_leverage(market_id)
        self._cancel_tp_sl(market_id)

        # 1) 반대(숏) 포지션 전량 청산
        pos_amt = self._position_amt()
        if pos_amt < 0:
            self.exchange.create_market_buy_order(SYMBOL, abs(pos_amt))
            logger.info(f"[CloseShort] qty={abs(pos_amt)}")

        # 2) 청산 후 잔고 기반 수량 재계산
        qty = self._calc_qty()

        # 3) 이미 롱이면 스킵
        pos_amt = self._position_amt()
        if pos_amt > 0:
            logger.info("[Skip] Already long")
            return {}

        # 4) 시장가 롱 진입
        order = self.exchange.create_market_buy_order(SYMBOL, qty)
        filled     = float(order.get("filled", order.get("amount", 0)))
        entry_price= float(order.get("average", order.get("price", 0)))
        logger.info(f"[BUY] qty={filled}@{entry_price}")

        # 5) TP 리밋 (50%)
        tp_qty   = filled * TP_PART_RATIO
        tp_price = entry_price * TP_RATIO
        self.exchange.create_limit_sell_order(SYMBOL, tp_qty, tp_price, {"reduceOnly": True})
        logger.info(f"[TP] qty={tp_qty}@{tp_price}")

        # 6) SL 스톱마켓 (전량)
        sl_price = entry_price * SL_RATIO
        self.exchange.create_order(SYMBOL, "STOP_MARKET", "sell", filled, None, {"stopPrice": sl_price, "reduceOnly": True})
        logger.info(f"[SL] qty={filled}@{sl_price}")

        return {"buy": order}

    def sell(self):
        market_id = SYMBOL.replace("/", "")
        if DRY_RUN:
            logger.info("[DRY_RUN] SELL skipped")
            return {}

        self.exchange.load_markets()
        self._set_isolated(market_id)
        self._set_leverage(market_id)
        self._cancel_tp_sl(market_id)

        # 1) 반대(롱) 포지션 전량 청산
        pos_amt = self._position_amt()
        if pos_amt > 0:
            self.exchange.create_market_sell_order(SYMBOL, pos_amt)
            logger.info(f"[CloseLong] qty={pos_amt}")

        # 2) 청산 후 잔고 기반 수량 재계산
        qty = self._calc_qty()

        # 3) 이미 숏이면 스킵
        pos_amt = self._position_amt()
        if pos_amt < 0:
            logger.info("[Skip] Already short")
            return {}

        # 4) 시장가 숏 진입
        order = self.exchange.create_market_sell_order(SYMBOL, qty)
        filled     = float(order.get("filled", order.get("amount", 0)))
        entry_price= float(order.get("average", order.get("price", 0)))
        logger.info(f"[SELL] qty={filled}@{entry_price}")

        # 5) TP 리밋
        tp_qty   = filled * TP_PART_RATIO
        tp_price = entry_price / TP_RATIO
        self.exchange.create_limit_buy_order(SYMBOL, tp_qty, tp_price, {"reduceOnly": True})
        logger.info(f"[TP] qty={tp_qty}@{tp_price}")

        # 6) SL 스톱마켓
        sl_price = entry_price / SL_RATIO
        self.exchange.create_order(SYMBOL, "STOP_MARKET", "buy", filled, None, {"stopPrice": sl_price, "reduceOnly": True})
        logger.info(f"[SL] qty={filled}@{sl_price}")

        return {"sell": order}