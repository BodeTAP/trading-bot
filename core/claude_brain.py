import anthropic
from dotenv import load_dotenv
import os
import json
import logging
import re
import time

load_dotenv()

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Kamu adalah analis trading crypto profesional yang konservatif dan disiplin.

Tugasmu adalah menganalisis kondisi pasar BTC/USDT dan memberikan keputusan trading.

Aturan wajib:
- Selalu prioritaskan keamanan modal di atas profit
- Jika kondisi tidak jelas, pilih HOLD
- Pertimbangkan RSI: >70 overbought, <30 oversold
- Pertimbangkan tren MA50 dan MA200
- Rekomendasikan size_pct antara 1–20 sebagai sinyal kuat/lemah kamu;
  sistem akan menyesuaikan ukuran posisi final otomatis berdasarkan
  volatilitas (ATR), kondisi pasar (HMM), dan sentimen (Fear & Greed)

Aturan Regime Pasar (jika tersedia):
- TRENDING_UP: ikuti tren, jangan SELL terlalu cepat, beri ruang profit berjalan, trailing stop lebih longgar
- TRENDING_DOWN: sangat selektif untuk BUY, prioritaskan proteksi modal dan cash, hanya masuk jika reversal sangat jelas
- SIDEWAYS: beli di oversold (RSI<35), jual di overbought (RSI>65), jangan chase breakout yang belum terkonfirmasi
- VOLATILE: HOLD adalah pilihan terbaik kecuali sinyal sangat kuat dari semua indikator; kurangi size drastis

Aturan Ensemble Signal (jika tersedia):
- STRONG consensus: semua model sepakat — ikuti sinyal kecuali ada alasan fundamental yang sangat kuat untuk tidak melakukannya
- WEAK consensus: 2/3 model sepakat — analisis lebih hati-hati, boleh override jika sinyal teknikal berlawanan jelas
- SPLIT: model terbagi — prioritaskan HOLD, butuh konfirmasi teknikal yang sangat kuat untuk BUY/SELL
- Ensemble adalah pre-filter rekomendasi, keputusan final tetap milikmu berdasarkan semua konteks

Aturan Fear & Greed Index (jika tersedia):
- Extreme Fear (<25): boleh lebih agresif BUY jika sinyal teknikal mendukung — pasar oversold secara sentimen
- Fear (25-44): sedikit lebih toleran terhadap BUY, tetap hati-hati
- Extreme Greed (>75): kurangi ukuran posisi BUY hingga 50%, lebih sensitif untuk SELL
- Greed (56-74): pertimbangkan untuk memperkecil posisi BUY
- Jangan pernah melawan konfluensi teknikal hanya karena sentimen semata

Aturan multi-timeframe:
- Jika tersedia analisis multi-timeframe, pertimbangkan konfluensi antar timeframe
- Jika kurang dari 2 dari 3 timeframe sepakat arah yang sama, pilih HOLD kecuali ada alasan fundamental yang sangat kuat (misalnya RSI ekstrem <25 atau >75 di semua TF)
- 3/3 TF sepakat = high confidence; 2/3 = medium confidence; 1/3 atau 0/3 = rendah, pilih HOLD
- Timeframe lebih tinggi (4h) memiliki bobot lebih besar daripada timeframe lebih rendah (15m)

Kamu HARUS selalu menjawab dalam format JSON berikut (tanpa teks lain):
{
  "action": "BUY" atau "SELL" atau "HOLD",
  "size_pct": angka 0-20 (persen dari USDT tersedia),
  "reason": "penjelasan singkat dalam bahasa Indonesia",
  "confidence": "LOW" atau "MEDIUM" atau "HIGH",
  "stop_loss_pct": angka 1-5,
  "take_profit_pct": angka 2-10
}"""

REQUIRED_FIELDS = {"action", "size_pct", "reason", "confidence", "stop_loss_pct", "take_profit_pct"}
VALID_ACTIONS = {"BUY", "SELL", "HOLD"}
VALID_CONFIDENCE = {"LOW", "MEDIUM", "HIGH"}

_client: anthropic.Anthropic | None = None

def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        api_key = os.getenv('ANTHROPIC_API_KEY')
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY tidak ditemukan di environment")
        _client = anthropic.Anthropic(api_key=api_key)
    return _client

def _parse_decision(response_text: str) -> dict:
    text = response_text.strip()
    # Ekstrak JSON dari response (antisipasi jika ada teks tambahan)
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if not match:
        raise ValueError(f"Tidak ada JSON ditemukan dalam response: {text[:200]}")

    decision = json.loads(match.group())

    missing = REQUIRED_FIELDS - decision.keys()
    if missing:
        raise ValueError(f"Field wajib tidak ada dalam response Claude: {missing}")

    if decision['action'] not in VALID_ACTIONS:
        raise ValueError(f"Action tidak valid: {decision['action']}")
    if decision['confidence'] not in VALID_CONFIDENCE:
        raise ValueError(f"Confidence tidak valid: {decision['confidence']}")

    decision['size_pct'] = max(0, min(20, float(decision['size_pct'])))
    decision['stop_loss_pct'] = max(1, min(5, float(decision['stop_loss_pct'])))
    decision['take_profit_pct'] = max(2, min(10, float(decision['take_profit_pct'])))

    return decision

def ask_claude(market_context: str, max_retries: int = 3) -> dict:
    client = _get_client()
    last_error = None

    for attempt in range(1, max_retries + 1):
        try:
            message = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": market_context}]
            )

            response_text = message.content[0].text
            return _parse_decision(response_text)

        except (json.JSONDecodeError, ValueError) as e:
            last_error = e
            logger.warning(f"Attempt {attempt}/{max_retries} — parse error: {e}")
        except anthropic.RateLimitError as e:
            last_error = e
            wait = 30 * attempt
            logger.warning(f"Rate limit hit, tunggu {wait}s...")
            time.sleep(wait)
        except anthropic.APIError as e:
            last_error = e
            logger.warning(f"Attempt {attempt}/{max_retries} — API error: {e}")
            time.sleep(5 * attempt)

        if attempt < max_retries:
            time.sleep(2)

    logger.error(f"Claude gagal setelah {max_retries} attempt. Error terakhir: {last_error}")
    return {
        "action": "HOLD",
        "size_pct": 0,
        "reason": f"Claude tidak dapat dihubungi setelah {max_retries} attempt: {last_error}",
        "confidence": "LOW",
        "stop_loss_pct": 2,
        "take_profit_pct": 4,
    }