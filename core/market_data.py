import ccxt
import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import SMAIndicator, MACD
from ta.volatility import AverageTrueRange
from dotenv import load_dotenv
import os
import logging
import math

load_dotenv()

logger = logging.getLogger(__name__)

_exchange:         ccxt.binance | None = None
_futures_exchange: ccxt.binance | None = None

# Candle limits per timeframe (must be ≥200 for MA200 to compute cleanly)
_TF_LIMIT: dict[str, int] = {
    "1m": 300, "5m": 250, "15m": 200,
    "1h": 250, "4h": 200, "1d": 300,
}


def get_futures_exchange() -> ccxt.binance:
    """Return a Binance USDT-margined futures exchange (testnet or live)."""
    global _futures_exchange
    if _futures_exchange is None:
        api_key = os.getenv('BINANCE_API_KEY')
        secret  = os.getenv('BINANCE_SECRET_KEY')
        if not api_key or not secret:
            raise ValueError("BINANCE_API_KEY atau BINANCE_SECRET_KEY tidak ditemukan di environment")
        sandbox = os.getenv('BINANCE_SANDBOX', 'true').lower() != 'false'
        _futures_exchange = ccxt.binance({
            'apiKey': api_key,
            'secret': secret,
            'options': {'defaultType': 'future'},
            'sandbox': sandbox,
        })
    return _futures_exchange


def get_exchange() -> ccxt.binance:
    global _exchange
    if _exchange is None:
        api_key = os.getenv('BINANCE_API_KEY')
        secret = os.getenv('BINANCE_SECRET_KEY')
        if not api_key or not secret:
            raise ValueError("BINANCE_API_KEY atau BINANCE_SECRET_KEY tidak ditemukan di environment")
        _exchange = ccxt.binance({
            'apiKey': api_key,
            'secret': secret,
            'options': {'defaultType': 'spot'},
            'sandbox': True,
        })
    return _exchange


def fetch_market_data(pair: str = 'BTC/USDT', timeframe: str = '1h', limit: int = 200) -> pd.DataFrame:
    exchange = get_exchange()
    ohlcv = exchange.fetch_ohlcv(pair, timeframe, limit=limit)

    if not ohlcv:
        raise ValueError(f"Data OHLCV kosong untuk {pair} {timeframe}")

    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')

    df['rsi'] = RSIIndicator(df['close'], window=14).rsi()
    df['ma50'] = SMAIndicator(df['close'], window=50).sma_indicator()
    df['ma200'] = SMAIndicator(df['close'], window=200).sma_indicator()

    macd_indicator = MACD(df['close'])
    df['macd'] = macd_indicator.macd()
    df['macd_signal'] = macd_indicator.macd_signal()

    df['atr'] = AverageTrueRange(df['high'], df['low'], df['close'], window=14).average_true_range()

    # Validate only on fast indicators; ma50/ma200 may be NaN for short histories
    df_valid = df.dropna(subset=['rsi', 'macd'])
    if len(df_valid) < 2:
        raise ValueError("Data tidak cukup untuk menghitung indikator teknikal")

    return df


def fetch_multi_timeframe(pair: str = 'BTC/USDT') -> dict[str, pd.DataFrame]:
    """Fetch short/medium/long timeframe candles based on TIMEFRAME_* env vars."""
    tf_short  = os.getenv('TIMEFRAME_SHORT',  '15m')
    tf_medium = os.getenv('TIMEFRAME_MEDIUM', '1h')
    tf_long   = os.getenv('TIMEFRAME_LONG',   '4h')
    # deduplicate while preserving order
    timeframes = list(dict.fromkeys([tf_short, tf_medium, tf_long]))
    result: dict[str, pd.DataFrame] = {}
    for tf in timeframes:
        limit = _TF_LIMIT.get(tf, 250)
        try:
            result[tf] = fetch_market_data(pair, tf, limit)
            logger.debug(f"MTF fetch {tf}: {len(result[tf])} candles")
        except Exception as e:
            logger.warning(f"MTF fetch {tf} gagal: {e}")
    if not result:
        raise ValueError(f"Semua timeframe gagal di-fetch untuk {pair}")
    return result


def get_portfolio_status(pair: str = 'BTC/USDT') -> dict:
    exchange = get_exchange()
    balance  = exchange.fetch_balance()
    base     = pair.split('/')[0]   # 'BTC', 'ETH', 'BNB', dll

    usdt = float(balance.get('USDT', {}).get('free', 0) or 0)
    coin = float(balance.get(base, {}).get('total', 0) or 0)

    ticker    = exchange.fetch_ticker(pair)
    coin_price = float(ticker.get('last') or 0)
    if coin_price <= 0:
        raise ValueError(f"Harga {base} tidak valid dari exchange")

    return {
        'usdt_available':  usdt,
        'btc_held':        coin,           # key dipertahankan agar kompatibel
        'btc_value_usdt':  coin * coin_price,
        'btc_price':       coin_price,
        'total_value_usdt': usdt + (coin * coin_price),
    }


def _fmt_indicator(value, fmt='.1f', prefix='$') -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return 'N/A'
    if prefix == '$':
        return f"${value:,.2f}"
    return f"{value:{fmt}}"


def _tf_bias(df: pd.DataFrame) -> str:
    """Compute BULLISH / BEARISH / NEUTRAL bias from technical indicators."""
    valid = df.dropna(subset=['rsi', 'ma50', 'macd'])
    if valid.empty:
        return "NEUTRAL"
    row = valid.iloc[-1]
    price = row['close']

    buy_votes = sell_votes = 0

    # MA50
    if price > row['ma50']:
        buy_votes += 1
    else:
        sell_votes += 1

    # MA200 (skip if not available)
    ma200 = row.get('ma200', float('nan'))
    if not pd.isna(ma200):
        if price > ma200:
            buy_votes += 1
        else:
            sell_votes += 1

    # RSI mid-point
    if row['rsi'] < 50:
        buy_votes += 1
    elif row['rsi'] > 50:
        sell_votes += 1

    # MACD vs signal
    if row['macd'] > row['macd_signal']:
        buy_votes += 1
    else:
        sell_votes += 1

    if buy_votes > sell_votes:
        return "BULLISH"
    if sell_votes > buy_votes:
        return "BEARISH"
    return "NEUTRAL"


def _macd_direction(df: pd.DataFrame) -> str:
    valid = df.dropna(subset=['macd', 'macd_signal'])
    if valid.empty:
        return "N/A"
    row = valid.iloc[-1]
    return "bullish crossover" if row['macd'] > row['macd_signal'] else "bearish crossover"


def format_context_for_claude(
    df: pd.DataFrame,
    portfolio: dict,
    hmm_state: str | None = None,
    hmm_confidence: float | None = None,
    hmm_bias: str | None = None,
    multi_tf: dict | None = None,
    hmm_tf_states: dict | None = None,
    sentiment: dict | None = None,
    ensemble_result: dict | None = None,
    regime_result:  dict | None = None,
    pair: str = 'BTC/USDT',
) -> str:
    # Use last valid row from primary df
    df_valid = df.dropna(subset=['rsi', 'ma50', 'macd'])
    if len(df_valid) < 2:
        return "Data tidak cukup untuk analisis teknikal.", ""
    latest = df_valid.iloc[-1]
    prev = df_valid.iloc[-2]

    price_change = ((latest['close'] - prev['close']) / prev['close'] * 100) if prev['close'] else 0

    ma200_text = _fmt_indicator(latest.get('ma200'))
    vs_ma50 = "DI ATAS" if latest['close'] > latest['ma50'] else "DI BAWAH"
    if pd.isna(latest.get('ma200', float('nan'))):
        vs_ma200 = "N/A (data belum cukup)"
    else:
        vs_ma200 = "DI ATAS" if latest['close'] > latest['ma200'] else "DI BAWAH"

    hmm_section = ""
    if hmm_state is not None:
        conf_pct = f"{hmm_confidence * 100:.0f}%" if hmm_confidence is not None else "N/A"
        hmm_section = f"""
KONDISI PASAR (HMM Regime Classifier):
- State: {hmm_state} (confidence: {conf_pct})
- Bias trading: {hmm_bias or 'N/A'}
"""

    # ── Multi-timeframe section ───────────────────────────────────────────────
    mtf_section = ""
    confluence_text = ""  # returned for logging

    if multi_tf:
        tf_order = [
            ("4h",  "Long-term bias"),
            ("1h",  "Medium-term trend"),
            ("15m", "Short-term momentum"),
        ]
        tf_blocks: list[str] = []
        biases: list[str] = []

        for tf, label in tf_order:
            tf_df = multi_tf.get(tf)
            if tf_df is None:
                continue

            valid = tf_df.dropna(subset=['rsi', 'ma50', 'macd'])
            if valid.empty:
                continue

            row = valid.iloc[-1]
            price = row['close']

            vs_ma50_tf = "DI ATAS" if price > row['ma50'] else "DI BAWAH"
            ma200_tf = row.get('ma200', float('nan'))
            vs_ma200_tf = (
                "N/A (data belum cukup)" if pd.isna(ma200_tf)
                else ("DI ATAS" if price > ma200_tf else "DI BAWAH")
            )
            macd_txt = _macd_direction(tf_df)
            bias = _tf_bias(tf_df)
            biases.append(bias)

            hmm_tf_line = ""
            if hmm_tf_states and tf in hmm_tf_states:
                hmm_tf_line = f"\nHMM State: {hmm_tf_states[tf]}"

            tf_blocks.append(
                f"\n[{tf} - {label}]\n"
                f"Harga vs MA50: {vs_ma50_tf} | Harga vs MA200: {vs_ma200_tf}\n"
                f"RSI: {row['rsi']:.1f} | MACD: {macd_txt}"
                f"{hmm_tf_line}\n"
                f"Bias: {bias}"
            )

        if biases:
            total       = len(biases)
            bull_count  = biases.count("BULLISH")
            bear_count  = biases.count("BEARISH")

            if bull_count >= 2:
                confluence_text = f"{bull_count}/{total} BULLISH"
                recommendation  = "STRONG BUY" if bull_count == total else "BUY"
            elif bear_count >= 2:
                confluence_text = f"{bear_count}/{total} BEARISH"
                recommendation  = "STRONG SELL" if bear_count == total else "SELL"
            else:
                confluence_text = f"MIXED ({bull_count}/{total} bullish, {bear_count}/{total} bearish)"
                recommendation  = "HOLD"

            mtf_section = (
                "\nANALISIS MULTI-TIMEFRAME:"
                + "".join(tf_blocks)
                + f"\n\nKONFLUENSI: {confluence_text}"
                + f"\nREKOMENDASI AWAL: {recommendation}\n"
            )

    # ── Regime section ───────────────────────────────────────────────────────
    regime_section = ""
    if regime_result:
        r      = regime_result
        adx_v  = r.get("adx")
        adx_s  = f"{adx_v:.1f} ({'trend kuat' if adx_v and adx_v > 25 else 'trend lemah/sideways'})" if adx_v else "N/A"
        bb_s   = f"{r['bb_width']:.4f} ({'squeeze' if r.get('bb_width', 1) < 0.05 else 'normal'})" if r.get("bb_width") else "N/A"
        atr_s  = f"{r['atr_ratio']:.2f}x ({'volatil' if r.get('atr_ratio', 0) > 1.5 else 'normal'})" if r.get("atr_ratio") else "N/A"
        regime_section = (
            f"\nREGIME PASAR:\n"
            f"Regime     : {r['regime']} (confidence: {r['confidence']:.0%})\n"
            f"ADX        : {adx_s}\n"
            f"BB Width   : {bb_s}\n"
            f"ATR Ratio  : {atr_s}\n"
            f"Strategi   : {r['strategy_hint']}\n"
        )

    # ── Ensemble section ─────────────────────────────────────────────────────
    ensemble_section = ""
    if ensemble_result:
        e          = ensemble_result
        signal     = e["signal"]
        score      = e["score"]
        consensus  = e["consensus"]
        votes      = e["votes"]
        confs      = e["confidences"]
        n_agree    = max(
            list(votes.values()).count("BUY"),
            list(votes.values()).count("SELL"),
        )
        rekomendasi_map = {
            "STRONG": f"Ikuti sinyal — semua model sepakat {signal}",
            "WEAK":   f"Analisis hati-hati — {n_agree}/3 model {signal}",
            "SPLIT":  "Prioritaskan HOLD — model terbagi, butuh sinyal teknikal sangat jelas",
        }
        ensemble_section = (
            f"\nENSEMBLE SIGNAL:\n"
            f"Signal     : {signal} (score: {score:+.3f})\n"
            f"Konsensus  : {consensus} ({n_agree}/3 model sepakat)\n"
            f"Rule-based : {votes['rule']} (conf: {confs['rule']:.0%})\n"
            f"HMM-based  : {votes['hmm']} (conf: {confs['hmm']:.0%})\n"
            f"Momentum   : {votes['momentum']} (conf: {confs['momentum']:.0%})\n"
            f"Rekomendasi: {rekomendasi_map.get(consensus, '')}\n"
        )

    # ── Sentiment section ────────────────────────────────────────────────────
    sentiment_section = ""
    if sentiment:
        sentiment_section = (
            f"\nSENTIMEN PASAR (Fear & Greed Index):\n"
            f"Hari ini  : {sentiment['current_value']} — {sentiment['current_label']}\n"
            f"Kemarin   : {sentiment['yesterday_value']} — {sentiment['yesterday_label']}\n"
            f"Tren 7hr  : {sentiment['trend_7d']}\n"
            f"Interpretasi: {sentiment['interpretation']}\n"
        )

    return f"""
KONDISI PASAR SAAT INI - {pair}

Harga: ${latest['close']:,.2f}
Perubahan 1 candle: {price_change:.2f}%

INDIKATOR TEKNIKAL:
- RSI(14): {latest['rsi']:.1f}
- MA50: ${latest['ma50']:,.2f}
- MA200: {ma200_text}
- Posisi harga vs MA50: {vs_ma50}
- Posisi harga vs MA200: {vs_ma200}
- MACD: {latest['macd']:.4f} | Signal: {latest['macd_signal']:.4f}
{hmm_section}{regime_section}{ensemble_section}{sentiment_section}{mtf_section}
STATUS PORTOFOLIO:
- USDT tersedia: ${portfolio['usdt_available']:,.2f}
- {pair.split('/')[0]} dipegang: {portfolio['btc_held']:.6f} {pair.split('/')[0]} (≈ ${portfolio['btc_value_usdt']:,.2f})
- Total nilai: ${portfolio['total_value_usdt']:,.2f}
""", confluence_text
