from dotenv import load_dotenv
import os
import json
import logging
from datetime import datetime
from pathlib import Path
from core.position_sizer import PositionSizer

load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / '.env', override=True)

logger = logging.getLogger(__name__)

LOGS_DIR = Path('logs')


class RiskManager:
    def __init__(self):
        self.max_drawdown_pct              = float(os.getenv('MAX_DRAWDOWN_PCT', 10))
        self.starting_balance: float | None = None
        self.circuit_breaker_triggered     = False
        self.trade_log: list[dict]         = []
        self._sizer                        = PositionSizer()
        LOGS_DIR.mkdir(exist_ok=True)

    def set_starting_balance(self, balance: float):
        if self.starting_balance is None:
            if balance <= 0:
                raise ValueError(f"Starting balance tidak valid: {balance}")
            self.starting_balance = balance
            logger.info(f"Starting balance dicatat: ${balance:,.2f}")

    def check_circuit_breaker(self, current_balance: float) -> bool:
        if self.starting_balance is None or self.starting_balance <= 0:
            return False

        drawdown_pct = ((self.starting_balance - current_balance) / self.starting_balance) * 100

        if drawdown_pct >= self.max_drawdown_pct:
            self.circuit_breaker_triggered = True
            logger.critical(f"CIRCUIT BREAKER AKTIF! Drawdown: {drawdown_pct:.1f}% (max: {self.max_drawdown_pct}%)")
            return True

        logger.debug(f"Drawdown saat ini: {drawdown_pct:.2f}%")
        return False

    def validate_decision(
        self,
        decision: dict,
        portfolio: dict,
        atr: float | None = None,
        avg_atr: float | None = None,
    ) -> dict:
        if self.circuit_breaker_triggered:
            return {
                "action": "HOLD",
                "size_pct": 0,
                "reason": "Circuit breaker aktif - trading dihentikan",
                "confidence": "HIGH",
                "stop_loss_pct": 2,
                "take_profit_pct": 4,
            }

        decision.setdefault('action', 'HOLD')
        decision.setdefault('size_pct', 0)
        decision.setdefault('reason', '')
        decision.setdefault('confidence', 'LOW')
        decision.setdefault('stop_loss_pct', 2)
        decision.setdefault('take_profit_pct', 4)

        # Hard cap before sizing
        if decision['size_pct'] > 20:
            decision['size_pct'] = 20

        if decision['confidence'] == 'LOW':
            decision['action']   = 'HOLD'
            decision['size_pct'] = 0
            decision['reason']  += " (HOLD karena confidence rendah)"

        if decision['action'] == 'BUY' and portfolio['usdt_available'] < 10:
            decision['action']   = 'HOLD'
            decision['size_pct'] = 0
            decision['reason']  += " (USDT tidak cukup, minimum $10)"

        if decision['action'] == 'SELL' and portfolio['btc_held'] < 0.0001:
            decision['action']   = 'HOLD'
            decision['size_pct'] = 0
            decision['reason']  += " (tidak ada BTC untuk dijual)"

        if decision['action'] == 'SHORT':
            trading_mode = os.getenv('TRADING_MODE', 'spot').lower()
            if trading_mode != 'futures':
                decision['action']   = 'HOLD'
                decision['size_pct'] = 0
                decision['reason']  += " (SHORT diblokir — TRADING_MODE bukan futures)"
            elif portfolio['usdt_available'] < 10:
                decision['action']   = 'HOLD'
                decision['size_pct'] = 0
                decision['reason']  += " (USDT tidak cukup untuk SHORT)"
            else:
                hmm_state = decision.get('market_state')
                regime    = decision.get('regime')
                if hmm_state not in ('BEAR', 'CRASH'):
                    decision['action']   = 'HOLD'
                    decision['size_pct'] = 0
                    decision['reason']  += f" (SHORT diblokir — HMM state {hmm_state} bukan BEAR/CRASH)"
                elif regime not in ('TRENDING_DOWN', 'VOLATILE', None):
                    decision['action']   = 'HOLD'
                    decision['size_pct'] = 0
                    decision['reason']  += f" (SHORT diblokir — regime {regime} tidak mendukung)"

        # ── Dynamic position sizing (BUY only) ────────────────────────────────
        if decision['action'] == 'BUY' and decision['size_pct'] > 0:
            claude_size = decision['size_pct']
            hmm_state   = decision.get('market_state')

            # Block immediately on CRASH without going through sizer
            if hmm_state == 'CRASH':
                decision['action']   = 'HOLD'
                decision['size_pct'] = 0
                decision['reason']  += " (HOLD — HMM: CRASH, trading diblokir)"
            else:
                final_size = self._sizer.calculate(
                    base_pct         = claude_size,
                    atr              = atr   or 0.0,
                    avg_atr          = avg_atr or 0.0,
                    confidence       = decision['confidence'],
                    hmm_state        = hmm_state,
                    fear_greed_value = decision.get('fear_greed_value'),
                    regime           = decision.get('regime'),
                )

                if final_size < 0.1:
                    decision['action']   = 'HOLD'
                    decision['size_pct'] = 0
                    decision['reason']  += " (HOLD — PositionSizer: size mendekati 0)"
                else:
                    if abs(final_size - claude_size) >= 0.5:
                        logger.info(
                            f"PositionSizer: Claude={claude_size:.1f}% → final={final_size:.1f}%"
                        )
                        decision['reason'] += (
                            f" (size disesuaikan: Claude {claude_size:.0f}% → {final_size:.0f}%)"
                        )
                    decision['size_pct']       = final_size
                    decision['claude_size_pct'] = claude_size   # preserve for logging

        return decision

    def log_decision(self, decision: dict, portfolio: dict):
        entry = {
            "timestamp":      datetime.now().isoformat(),
            "action":         decision['action'],
            "size_pct":       decision['size_pct'],
            "confidence":     decision['confidence'],
            "reason":         decision['reason'],
            "usdt_available": portfolio['usdt_available'],
            "btc_held":       portfolio['btc_held'],
            "total_value":    portfolio['total_value_usdt'],
        }

        # Optional fields — only written when present
        for field in (
            "market_state", "hmm_confidence",
            "tf_15m_state", "tf_1h_state", "tf_4h_state", "confluence",
            "fear_greed_value", "fear_greed_label",
            "claude_size_pct",
            "ensemble_signal", "ensemble_score", "ensemble_consensus",
            "regime", "regime_confidence",
        ):
            if field in decision:
                entry[field] = decision[field]

        self.trade_log.append(entry)

        log_file = LOGS_DIR / 'decisions.json'
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry) + '\n')

        logger.info(
            f"[{entry['timestamp']}] {entry['action']} | "
            f"Confidence: {entry['confidence']} | "
            f"Portfolio: ${entry['total_value']:,.2f}\n"
            f"Alasan: {entry['reason']}"
        )
