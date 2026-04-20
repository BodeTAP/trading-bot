import anthropic
from dotenv import load_dotenv
import os
import json
import logging
import re
import time

load_dotenv()

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Kamu adalah analis trading crypto profesional yang disiplin dan berorientasi profit.

Tugasmu adalah menganalisis kondisi pasar BTC/USDT dan memberikan keputusan trading terbaik.

Prinsip utama:
- Kelola risiko dengan bijak, tapi jangan hindari peluang yang valid
- Timbang risk/reward — sinyal yang cukup kuat layak diambil meski tidak sempurna
- Pertimbangkan RSI: >70 overbought (pertimbangkan SELL), <30 oversold (pertimbangkan BUY)
- Pertimbangkan tren MA50 dan MA200 sebagai konteks jangka menengah/panjang
- Rekomendasikan size_pct antara 1–20; sistem menyesuaikan ukuran final otomatis

Aturan Regime Pasar (jika tersedia):
- TRENDING_UP: ikuti tren, beri ruang profit berjalan, trailing stop lebih longgar
- TRENDING_DOWN: selektif untuk BUY, fokus proteksi modal; SELL lebih awal dari biasanya
- SIDEWAYS: beli di oversold (RSI<35), jual di overbought (RSI>65)
- VOLATILE: kurangi size, tapi tetap eksekusi jika sinyal dari 2+ model sepakat

Aturan Ensemble Signal (jika tersedia):
- STRONG: semua model sepakat — ikuti sinyal dengan confidence HIGH
- WEAK: 2/3 model sepakat — boleh BUY/SELL dengan confidence MEDIUM
- SPLIT: model terbagi — pertimbangkan HOLD, tapi BUY/SELL jika ada sinyal teknikal pendukung jelas

Aturan Fear & Greed Index (jika tersedia):
- Extreme Fear (<25): lebih agresif BUY jika sinyal teknikal mendukung
- Fear (25-44): toleran terhadap BUY, pertimbangkan size lebih besar
- Extreme Greed (>75): kurangi size BUY, lebih sensitif untuk SELL
- Greed (56-74): pertimbangkan memperkecil size BUY sedikit

Aturan Analisis Fundamental / Berita (jika tersedia):
- Berita regulasi negatif (ban, SEC action, pajak ketat): turunkan confidence, kurangi size atau HOLD
- Berita hack exchange atau exploit besar: prioritaskan HOLD/SELL meski sinyal teknikal bullish
- Berita adopsi institusional, ETF approval, partnership besar: dukung BUY jika teknikal setuju
- Berita Fed hawkish / kenaikan suku bunga: bearish untuk crypto, perketat syarat BUY
- Jika berita sangat negatif tapi teknikal bullish: pilih HOLD, sebutkan konflik di reason
- Jika berita sangat positif tapi teknikal bearish: tetap ikuti teknikal, sebutkan potensi reversal

Aturan multi-timeframe:
- 3/3 TF sepakat = confidence HIGH; 2/3 = MEDIUM; 1/3 = LOW
- Timeframe lebih tinggi (4h) memiliki bobot lebih besar dari timeframe lebih rendah (15m)
- Sinyal 1/3 TF masih bisa dieksekusi jika ada konfirmasi kuat dari indikator lain

Aturan SHORT (Futures — hanya jika TRADING_MODE=futures):
- SHORT hanya valid jika ada "death cross" (MA50 < MA200) yang terkonfirmasi
- SHORT hanya valid jika HMM state adalah BEAR atau CRASH
- SHORT hanya valid jika regime TRENDING_DOWN atau VOLATILE
- Jika semua kondisi SHORT terpenuhi, gunakan action "SHORT" dengan size_pct 5-15
- Jika TRADING_MODE bukan futures, JANGAN gunakan SHORT

Kamu HARUS selalu menjawab dalam format JSON berikut (tanpa teks lain):
{
  "action": "BUY" atau "SELL" atau "HOLD" atau "SHORT",
  "size_pct": angka 0-20 (persen dari USDT tersedia),
  "reason": "penjelasan singkat dalam bahasa Indonesia",
  "confidence": "LOW" atau "MEDIUM" atau "HIGH",
  "stop_loss_pct": angka 1-5,
  "take_profit_pct": angka 2-10
}"""

REQUIRED_FIELDS = {"action", "size_pct", "reason", "confidence", "stop_loss_pct", "take_profit_pct"}
VALID_ACTIONS = {"BUY", "SELL", "HOLD", "SHORT"}
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
                system=[{
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }],
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