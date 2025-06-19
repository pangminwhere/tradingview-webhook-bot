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
        # USDT-M Futures 전용 인스턴스
        self.exchange = ccxt.binanceusdm(params)
        if testnet:
            self.exchange.set_sandbox_mode(True)

    def _set_isolated(self, market_id: str):
        """Isolated margin 모드로 전환"""
        try:
            self.exchange.set_margin_mode("isolated", market_id)
            logger.info(f"[Margin] Set ISOLATED for {market_id}")
        except Exception as e:
            logger.warning(f"[Margin] Could not set isolated: {e}")

    def _set_leverage(self, market_id: str):
        """레버리지 설정"""
        self.exchange.set_leverage(TRADE_LEVERAGE, market_id)
        logger.info(f"[Leverage] {TRADE_LEVERAGE}x for {market_id}")

    def _calc_qty(self, symbol: str) -> float:
        """잔고의 BUY_PCT 만큼 USDT → base 수량 환산"""
        bal = self.exchange.fetch_balance()
        free_usdt = float(bal["free"].get("USDT", 0))
        alloc_usdt = free_usdt * BUY_PCT
        price = float(self.exchange.fetch_ticker(symbol)["last"])
        qty = alloc_usdt / price
        logger.info(f"[CalcQty] Free {free_usdt:.4f} USDT → Alloc {alloc_usdt:.4f} USDT → Qty {qty:.6f} {symbol}")
        return qty

    def _cancel_tp_sl(self, market_id: str):
        """기존 TP/SL (reduceOnly) 주문 모두 취소"""
        for o in self.exchange.fetch_open_orders(market_id):
            if o.get("info", {}).get("reduceOnly"):
                self.exchange.cancel_order(o["id"], market_id)
                logger.info(f"[Cancel] reduceOnly {o['id']}")

    def buy(self):
        """ETH/USDT 롱 진입 + TP/SL"""
        market_id = SYMBOL.replace("/", "")
        if DRY_RUN:
            logger.info("[DRY_RUN] BUY skipped")
            return

        # 1. 마진 모드 & 레버리지
        self._set_isolated(market_id)
        self._set_leverage(market_id)

        # 2. 기존 TP/SL 취소
        self._cancel_tp_sl(market_id)

        # 3. 숏 포지션 청산 (if any)
        pos = self.exchange.fetch_positions([SYMBOL])[0]
        pos_amt = float(pos["info"]["positionAmt"])
        if pos_amt < 0:
            close = self.exchange.create_market_buy_order(SYMBOL, abs(pos_amt))
            logger.info(f"[CloseShort] qty={abs(pos_amt)}")

        # 4. 이미 롱이면 스킵
        pos_now = float(self.exchange.fetch_positions([SYMBOL])[0]["info"]["positionAmt"])
        if pos_now > 0:
            logger.info("[Skip] Already long")
            return

        # 5. 시장가 롱 진입 (잔고의 98%)
        qty = self._calc_qty(SYMBOL)
        order = self.exchange.create_market_buy_order(SYMBOL, qty)
        filled = float(order.get("filled", order.get("amount", 0)))
        entry_price = float(order.get("average", order.get("price", 0)))
        logger.info(f"[BUY] qty={filled}@{entry_price}")

        # 6. 익절 리밋 주문 (50%)
        tp_qty = filled * TP_PART_RATIO
        tp_price = entry_price * TP_RATIO
        tp = self.exchange.create_limit_sell_order(SYMBOL, tp_qty, tp_price, {"reduceOnly": True})
        logger.info(f"[TP] qty={tp_qty}@{tp_price}")

        # 7. 손절 스톱마켓 주문 (전량)
        sl_price = entry_price * SL_RATIO
        sl = self.exchange.create_order(
            SYMBOL,
            "STOP_MARKET",
            "sell",
            filled,
            None,
            {"stopPrice": sl_price, "reduceOnly": True}
        )
        logger.info(f"[SL] qty={filled}@{sl_price}")

    def sell(self):
        """ETH/USDT 숏 진입 + TP/SL"""
        market_id = SYMBOL.replace("/", "")
        if DRY_RUN:
            logger.info("[DRY_RUN] SELL skipped")
            return

        # 1. 마진 모드 & 레버리지
        self._set_isolated(market_id)
        self._set_leverage(market_id)

        # 2. 기존 TP/SL 취소
        self._cancel_tp_sl(market_id)

        # 3. 롱 포지션 청산 (if any)
        pos = self.exchange.fetch_positions([SYMBOL])[0]
        pos_amt = float(pos["info"]["positionAmt"])
        if pos_amt > 0:
            close = self.exchange.create_market_sell_order(SYMBOL, pos_amt)
            logger.info(f"[CloseLong] qty={pos_amt}")

        # 4. 이미 숏이면 스킵
        pos_now = float(self.exchange.fetch_positions([SYMBOL])[0]["info"]["positionAmt"])
        if pos_now < 0:
            logger.info("[Skip] Already short")
            return

        # 5. 시장가 숏 진입 (잔고의 98%)
        qty = self._calc_qty(SYMBOL)
        order = self.exchange.create_market_sell_order(SYMBOL, qty)
        filled = float(order.get("filled", order.get("amount", 0)))
        entry_price = float(order.get("average", order.get("price", 0)))
        logger.info(f"[SELL] qty={filled}@{entry_price}")

        # 6. 익절 리밋 주문
        tp_qty = filled * TP_PART_RATIO
        tp_price = entry_price / TP_RATIO
        tp = self.exchange.create_limit_buy_order(SYMBOL, tp_qty, tp_price, {"reduceOnly": True})
        logger.info(f"[TP] qty={tp_qty}@{tp_price}")

        # 7. 손절 스톱마켓 주문
        sl_price = entry_price / SL_RATIO
        sl = self.exchange.create_order(
            SYMBOL,
            "STOP_MARKET",
            "buy",
            filled,
            None,
            {"stopPrice": sl_price, "reduceOnly": True}
        )
        logger.info(f"[SL] qty={filled}@{sl_price}")