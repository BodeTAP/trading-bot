# 📈 Trading Bot — Powered by Claude AI

![Python](https://img.shields.io/badge/Python-3.10%2B-blue?style=flat-square&logo=python)
![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux-lightgrey?style=flat-square)
![Exchange](https://img.shields.io/badge/Exchange-Binance%20Testnet-yellow?style=flat-square)
![AI](https://img.shields.io/badge/AI-Claude%20Haiku%204.5-purple?style=flat-square)
![Status](https://img.shields.io/badge/Status-Active-brightgreen?style=flat-square)

Bot trading crypto otomatis yang menggunakan **Claude AI (Anthropic)** sebagai "otak" pengambil keputusan, dilengkapi analisis multi-timeframe, Hidden Markov Model, ensemble signal dari 3 model independen, dan manajemen risiko berlapis.

> ⚠️ **Bot ini berjalan di Binance Testnet secara default.** Tidak ada uang sungguhan yang dipertaruhkan selama testing.

> **Dashboard** berjalan di `localhost:8501` — dark theme dengan sidebar health monitor, 5 metric card (portfolio, trades, win rate, drawdown, last action), 4 intel card (HMM state, ensemble signal, regime, fear & greed), equity curve + drawdown chart real-time, tabel keputusan 20 terakhir dengan color coding BUY/SELL/HOLD, performance gauge, dan tab backtesting interaktif.

---

## ✨ Keunggulan Dibanding Bot Biasa

| Fitur | Bot Biasa | Bot Ini |
|-------|-----------|---------|
| Pengambil keputusan | Indikator teknikal saja | Claude AI + 3 model ensemble |
| Analisis timeframe | 1 timeframe | 3 timeframe (15m/1h/4h) |
| Context pasar | Tidak ada | Fear & Greed, Regime, HMM State |
| Risk management | Fixed stop loss | Trailing stop ATR-based + circuit breaker |
| Monitoring | Manual | Dashboard real-time + Telegram |
| Auto-maintenance | Tidak ada | Log cleaner, health check, perf monitor |

---

## 📋 Daftar Isi

1. [Fitur Lengkap](#-fitur-lengkap)
2. [Instalasi](#-instalasi)
3. [Cara Penggunaan](#-cara-penggunaan)
4. [Config Profiles](#-config-profiles)
5. [Konfigurasi Parameter](#-konfigurasi-parameter)
6. [Arsitektur Teknis](#-arsitektur-teknis)
7. [Troubleshooting](#-troubleshooting)
8. [Disclaimer](#️-disclaimer)

---

## 🚀 Fitur Lengkap

### 🧠 Claude AI sebagai Otak

Claude Haiku 4.5 bukan sekadar wrapper indikator — dia menerima **context pasar yang kaya** setiap siklus dan menghasilkan keputusan terstruktur.

**Informasi yang dikirim ke Claude setiap siklus:**
```
- Harga BTC terkini + perubahan % 1h
- Indikator teknikal: RSI, MA50, MA200, MACD, Bollinger Bands, ATR, Volume
- HMM Market State (CRASH/BEAR/NEUTRAL/BULL/EUPHORIA) + confidence
- Market Regime (TRENDING_UP/DOWN/SIDEWAYS/VOLATILE)
- Ensemble Signal dari 3 model + score + konsensus
- Fear & Greed Index + tren 7 hari
- Portfolio saat ini (USDT, BTC, total value)
- Analisis per-timeframe (15m, 1h, 4h)
```

**Format respons Claude (JSON):**
```json
{
  "action": "BUY",
  "confidence": "HIGH",
  "size_pct": 15,
  "reason": "RSI oversold, MA50 > MA200, BULL state, Fear index rendah → sinyal akumulasi kuat",
  "stop_loss_pct": 3,
  "take_profit_pct": 6
}
```

Claude tidak pernah mengeksekusi order langsung — semua rekomendasi melalui validasi RiskManager dan PositionSizer sebelum dikirim ke Binance.

---

### 📊 HMM Market Classifier

**Analogi sederhana:** Bayangkan pasar punya "mood" tersembunyi. Kita tidak bisa melihat moodnya langsung, tapi bisa ditebak dari pergerakan harga. Hidden Markov Model (HMM) adalah algoritma yang "menebak mood pasar" berdasarkan pola historis.

**5 Market States:**

| State | Warna | Arti | Aksi Rekomendasi |
|-------|-------|------|-----------------|
| 🔴 CRASH | Merah | Penurunan tajam, panik jual | Block BUY, pertimbangkan SELL |
| 🟠 BEAR | Oranye | Tren turun bertahap | Kurangi posisi (40% normal size) |
| ⚫ NEUTRAL | Abu | Tidak ada tren jelas | Posisi normal (70% size) |
| 🟢 BULL | Hijau | Tren naik kuat | Full posisi (100% size) |
| 🔵 EUPHORIA | Biru | Kenaikan ekstrem, overheated | Hati-hati reversal (70% size) |

HMM di-train ulang setiap siklus dengan data terbaru (returns + volatility), sehingga selalu adaptif terhadap kondisi pasar.

---

### 🕐 Multi-Timeframe Analysis

**Kenapa 3 timeframe?** Satu timeframe bisa "menipu" — mungkin 15m kelihatan bullish tapi 4h sedang downtrend kuat. Dengan 3 timeframe, keputusan lebih berlandaskan gambaran besar.

```
15m (Short)  → Timing entry/exit yang tepat
1h  (Medium) → Konfirmasi tren utama
4h  (Long)   → Gambaran besar / filter arah
```

**Sistem Konfluensi:** Bot hanya memberikan sinyal kuat jika minimal 2 dari 3 timeframe sepakat (BULLISH/BEARISH). Jika 3/3 sepakat → konsensus STRONG.

---

### 🗳️ Ensemble Signals

Tiga model independen "voting" untuk menghasilkan sinyal final:

```
Model 1 — Rule-based  (bobot 0.3)
  └── RSI + MA crossover + MACD + MTF bias majority

Model 2 — HMM-based   (bobot 0.3)
  └── Translasi HMM state → sinyal arah

Model 3 — Momentum    (bobot 0.4)
  └── Price vs MA50/MA200 + RSI rising + 4h bias
      Score 0-5 → BUY jika ≥4, SELL jika ≤1
```

**Formula weighted voting:**
```
score = (rule × 0.3) + (hmm × 0.3) + (momentum × 0.4)
  → score > +0.3  : BUY
  → score < -0.3  : SELL
  → lainnya       : HOLD

Konsensus:
  STRONG = ketiga model sama
  WEAK   = 2/3 model sama
  SPLIT  = masing-masing beda pendapat
```

---

### 🌍 Market Regime Detection

Regime berbeda dari HMM state — ini mendeteksi **pola pergerakan pasar** menggunakan indikator:
- **ADX** (Average Directional Index) → kekuatan tren
- **Bollinger Bands width** → volatilitas relatif
- **ATR** → volatilitas absolut

| Regime | Kondisi | Strategi Bot |
|--------|---------|-------------|
| TRENDING_UP | ADX > 25, harga > BB tengah | Trend following, posisi diperbesar (1.2×) |
| TRENDING_DOWN | ADX > 25, harga < BB tengah | Proteksi modal, posisi dikecilkan (0.2×) |
| SIDEWAYS | ADX < 20 | Mean reversion, posisi sedang (0.7×) |
| VOLATILE | ATR sangat tinggi | Minimal exposure, posisi kecil (0.3×) |

---

### 😨 Fear & Greed Index

Data diambil dari **alternative.me** setiap siklus dan dikirim ke Claude sebagai context.

| Nilai | Label | Implikasi |
|-------|-------|-----------|
| 0–24 | Extreme Fear | Peluang beli (pasar oversold) |
| 25–39 | Fear | Pertimbangkan akumulasi |
| 40–59 | Neutral | Ikuti sinyal teknikal |
| 60–74 | Greed | Mulai kurangi posisi |
| 75–100 | Extreme Greed | Risiko reversal tinggi, hati-hati BUY |

---

### 📐 Position Sizing Dinamis

Claude merekomendasikan ukuran posisi (misalnya 15%), tapi `PositionSizer` menyesuaikan berdasarkan **4 faktor secara bersamaan:**

```
Final Size = base_pct × atr_ratio × hmm_mult × conf_mult ± fg_adj
  kemudian di-clamp ke [5%, 20%]
```

**Faktor 1 — ATR Volatility:**
```
atr_ratio = avg_atr / current_atr
  Pasar tenang (ATR rendah) → size lebih besar (max 1.5×)
  Pasar volatile (ATR tinggi) → size lebih kecil (min 0.5×)
```

**Faktor 2 — Confidence Claude:**
```
HIGH   → 100% size
MEDIUM → 60% size
LOW    → HOLD (tidak eksekusi)
```

**Faktor 3 — HMM State:**
```
BULL     → 100%  |  NEUTRAL  → 70%
EUPHORIA → 70%   |  BEAR     → 40%
CRASH    → 0% (tidak eksekusi BUY)
```

**Faktor 4 — Fear & Greed:**
```
Extreme Fear (<25) → +3% bonus (peluang beli langka)
Extreme Greed (>75) → -5% penalty (risiko reversal)
```

**Contoh nyata:**
```
Claude rekomendasikan: 15%
ATR saat ini tinggi → × 0.7 = 10.5%
HMM state BULL     → × 1.0 = 10.5%
Confidence HIGH    → × 1.0 = 10.5%
Fear & Greed = 80  → -5%  = 5.5%
Final: clamp ke MIN → 5%
```

---

### 🔒 Trailing Stop Loss

**Analogi sederhana:** Bayangkan kamu naik eskalator sambil memegang tali. Saat naik, tali ikut naik. Tapi saat turun, tali TIDAK ikut turun — jika harga turun sampai tali, posisi ditutup otomatis.

Trailing stop menggunakan **ATR (Average True Range)** sebagai satuan jarak:

```
Stop Price = Highest Price Since Entry - (ATR × TRAILING_STOP_ATR_MULTIPLIER)

Contoh (default multiplier = 2.0):
  ATR saat ini = $500
  BTC tertinggi sejak buy = $45,000
  Stop Price = $45,000 - ($500 × 2.0) = $44,000

  Jika BTC naik ke $46,000:
  Stop Price ikut naik = $46,000 - $1,000 = $45,000 ✅

  Jika BTC turun dari $46,000 ke $45,000:
  → SELL otomatis dieksekusi!
```

**Perbedaan vs Fixed Stop Loss:**
- Fixed stop: level tetap, tidak mengikuti profit
- Trailing stop: mengunci profit seiring harga naik

---

### 🛡️ Risk Management & Circuit Breaker

Tiga lapisan perlindungan:

**Lapisan 1 — Validasi per keputusan:**
- Confidence LOW → paksa HOLD
- Size > 20% → dipotong ke 20%
- USDT < $10 → block BUY

**Lapisan 2 — Circuit Breaker:**
```python
if drawdown >= MAX_DRAWDOWN_PCT:
    # Semua BUY diblokir
    # Status: "Circuit breaker aktif"
    # Telegram alert dikirim
```
Misalnya `MAX_DRAWDOWN_PCT=10` dan modal awal $10,000 → circuit breaker aktif jika portfolio turun ke $9,000.

**Lapisan 3 — Performance Monitor (otomatis):**
- Drawdown > 7% → switch ke profile Conservative
- 3 consecutive losses → pause trading 2 jam
- Win rate < 40% selama 3 hari → kurangi parameter agresivitas
- Win rate > 65% selama 3 hari → tingkatkan parameter

---

### 📱 Telegram Commands

Kirim command langsung ke bot Telegram kamu:

| Command | Contoh | Deskripsi |
|---------|--------|-----------|
| `/status` | `/status` | Status lengkap: bot, portfolio, BTC price, regime, ensemble, trailing stop, next session |
| `/balance` | `/balance` | Fetch balance live dari Binance |
| `/trades` | `/trades` | 5 keputusan trading terakhir |
| `/pause` | `/pause 60` | Jeda trading 60 menit (default: 30 menit) |
| `/start` | `/start` | Aktifkan kembali trading |
| `/stop` | `/stop` | Hentikan eksekusi (analisis tetap berjalan) |
| `/help` | `/help` | Tampilkan daftar semua command |

**Contoh output `/status`:**
```
✅ Bot: Running
📋 Profile: Conservative
💰 Portfolio: $10,250 (+2.5% dari awal)
📊 Regime: TRENDING_UP (78%)
🧠 Ensemble: BUY (STRONG)
😨 Fear & Greed: 42 — Fear
📈 BTC: $43,500 (+1.2% 1h)
🔒 Trailing Stop: $42,800 (jarak: 1.6%)
⏱ Sesi berikutnya: 12 menit lagi
```

---

### 📈 Dashboard & Config Panel

**Dashboard** (`localhost:8501`) — monitoring real-time:
- Status bot + profile aktif
- 5 metric cards: Portfolio, Trades Hari Ini, Win Rate, Drawdown, Last Action
- 4 intelligence cards: HMM State, Ensemble Signal, Market Regime, Fear & Greed
- Multi-TF badges + equity curve + drawdown chart
- Tabel keputusan 20 terakhir dengan color coding
- Performance Monitor dengan win rate gauge
- Tab Backtesting dengan equity curve interaktif

**Config Panel** (`localhost:8502`) — konfigurasi tanpa edit file:
- Pilih dan aktifkan profile (Conservative/Aggressive/Scalping)
- Edit semua parameter trading
- Test koneksi Binance, Anthropic, Telegram langsung dari browser
- Restart bot dengan satu klik

---

### 🔄 Auto-Maintenance

**Performance Monitor** (cek setiap 6 jam):
- Hitung win rate, drawdown, consecutive losses, Sharpe ratio (7 hari terakhir)
- Auto-adjust parameter berdasarkan kondisi
- Simpan state ke `logs/perf_state.json`

**Log Cleaner** (jalan tengah malam setiap hari):
- `decisions.json` > 1000 baris → arsipkan oldest, simpan 500 terbaru
- File `.log` > 10 MB → gzip + reset
- Arsip > 90 hari → hapus otomatis
- Kirim ringkasan ke Telegram jika ada yang dibersihkan

**Health Check** (cek setiap 1 jam):
- Ping Binance API, Anthropic API, Telegram
- Monitor CPU, RAM, disk usage
- Alert Telegram jika ada service DOWN
- Daily summary pukul 08:00 jika semua OK

---

## 🛠️ Instalasi

### Prerequisites

- **Python 3.10+** — [download](https://www.python.org/downloads/)
- **pip** (sudah termasuk di Python)
- **Akun Binance** (gunakan Testnet dulu untuk testing)
- **Anthropic API key** — [console.anthropic.com](https://console.anthropic.com)
- **Telegram Bot** (opsional tapi sangat direkomendasikan)

---

### Step 1: Download Project

```bash
# Jika pakai git:
git clone https://github.com/username/trading-bot.git
cd trading-bot

# Atau extract ZIP dan masuk ke foldernya
```

---

### Step 2: Install Dependencies

```bash
pip install -r requirements.txt
```

> ⚠️ **Note:** Proses ini bisa memakan waktu 2–5 menit karena menginstall library ML (scikit-learn, scipy, dll).

Jika ada error `pip not found`:
```bash
python -m pip install -r requirements.txt
```

---

### Step 3: Setup API Keys

#### 3a. Binance Testnet API Key

1. Buka [testnet.binance.vision](https://testnet.binance.vision)
2. Login dengan akun GitHub
3. Klik **"Generate HMAC_SHA256 Key"**
4. Salin **API Key** dan **Secret Key** — simpan di tempat aman, Secret Key hanya ditampilkan sekali!

> 💡 Testnet gratis dan pakai uang virtual. Aman untuk belajar.

#### 3b. Anthropic API Key

1. Buka [console.anthropic.com](https://console.anthropic.com)
2. Daftar/login → pilih menu **"API Keys"**
3. Klik **"Create Key"**
4. Salin API key yang dimulai dengan `sk-ant-...`

> 💡 Claude Haiku sangat murah (~$0.25 per 1 juta token). Untuk testing intensif, $5 kredit bisa bertahan berminggu-minggu.

#### 3c. Telegram Bot

1. Buka Telegram, cari **@BotFather**
2. Ketik `/newbot`
3. Masukkan nama bot (misal: `My Trading Bot`)
4. Masukkan username bot (harus diakhiri `bot`, misal: `mytradingbot_bot`)
5. Salin **Token** yang diberikan BotFather (format: `1234567890:ABCdefGHI...`)

**Cara dapat Chat ID:**
1. Kirim sembarang pesan ke bot kamu
2. Buka browser, akses URL berikut (ganti `TOKEN` dengan token bot kamu):
   ```
   https://api.telegram.org/botTOKEN/getUpdates
   ```
3. Cari `"chat":{"id":` — angka di sana adalah Chat ID kamu

---

### Step 4: Konfigurasi .env

Buat file `.env` di folder root project:

```bash
# ─── BINANCE ──────────────────────────────────────────────────────────────────
BINANCE_API_KEY=your_binance_testnet_api_key_here
BINANCE_SECRET_KEY=your_binance_testnet_secret_key_here

# Gunakan testnet untuk testing (ganti ke False untuk live trading)
USE_TESTNET=True

# ─── ANTHROPIC ────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY=sk-ant-your_api_key_here

# ─── TELEGRAM ─────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN=1234567890:ABCdefGHIjklmNOPqrstUVWxyz
TELEGRAM_CHAT_ID=987654321

# ─── TRADING PARAMETERS ───────────────────────────────────────────────────────
TRADING_PAIR=BTC/USDT

# Timeframes (Short/Medium/Long)
TIMEFRAME_SHORT=15m
TIMEFRAME_MEDIUM=1h
TIMEFRAME_LONG=4h

# Interval antar siklus (detik) — default mengikuti TIMEFRAME_SHORT
# INTERVAL_SECONDS=900

# ─── RISK MANAGEMENT ─────────────────────────────────────────────────────────
# Circuit breaker aktif jika drawdown melebihi persentase ini
MAX_DRAWDOWN_PCT=10

# Multiplier ATR untuk trailing stop (lebih besar = stop lebih longgar)
TRAILING_STOP_ATR_MULTIPLIER=2.0

# ─── POSITION SIZING ─────────────────────────────────────────────────────────
# Minimum dan maksimum ukuran posisi (% dari available capital)
MIN_POSITION_SIZE=5
MAX_POSITION_SIZE=20
```

---

### Step 5: Jalankan Bot

```bash
# Cara 1: Rekomendasi — auto-restart jika crash
python bot_runner.py

# Cara 2: Manual (tanpa auto-restart)
python main.py
```

---

## 📖 Cara Penggunaan

### Menjalankan Bot

```bash
# Dengan auto-restart (rekomendasi untuk production)
python bot_runner.py

# Manual (untuk development/debugging)
python main.py
```

`bot_runner.py` akan otomatis me-restart bot jika crash (maksimal 5 crash dalam 1 jam).
Log crash disimpan di `logs/crash.log` dan `logs/bot_runner.log`.

---

### Menjalankan Dashboard

```bash
# Windows
python -m streamlit run dashboard.py

# Linux/Mac
streamlit run dashboard.py
```

Buka browser: [http://localhost:8501](http://localhost:8501)

---

### Menjalankan Config Panel

```bash
# Windows
python -m streamlit run config_panel.py --server.port 8502

# Linux/Mac
streamlit run config_panel.py --server.port 8502
```

Buka browser: [http://localhost:8502](http://localhost:8502)

---

### Menjalankan Backtest

```bash
# Default (BTC/USDT, 1h, 500 candles)
python backtest.py

# Custom parameter
python backtest.py --candles 500 --timeframe 1h
python backtest.py --pair ETH/USDT
python backtest.py --capital 20000
python backtest.py --candles 1000 --timeframe 4h --warmup 200

# Tanpa generate chart (lebih cepat)
python backtest.py --no-chart
```

Hasil backtest tersimpan di `backtest_results.json` dan bisa dilihat di Dashboard tab "Backtesting".

---

## 🎛️ Config Profiles

Tiga profile siap pakai yang bisa diaktifkan dari Config Panel atau Telegram:

| Parameter | Conservative | **Default** | Aggressive | Scalping |
|-----------|:-----------:|:-----------:|:----------:|:--------:|
| Max Drawdown | 5% | 10% | 20% | 10% |
| Trailing Stop ATR | 3.0× | 2.0× | 1.5× | 1.0× |
| Timeframe Short | 1h | 15m | 5m | 1m |
| Timeframe Medium | 4h | 1h | 15m | 5m |
| Timeframe Long | 1d | 4h | 1h | 15m |
| Cocok untuk | Pasar bearish / pemula | Balanced | Bull market kuat | Expert only |

**Cara ganti profile:**

Via Config Panel:
1. Buka `localhost:8502`
2. Pilih profile di bagian "Config Profiles"
3. Klik "Aktifkan"

Via `.env` (manual):
```bash
# Edit .env sesuai tabel di atas
MAX_DRAWDOWN_PCT=5
TRAILING_STOP_ATR_MULTIPLIER=3.0
TIMEFRAME_SHORT=1h
TIMEFRAME_MEDIUM=4h
TIMEFRAME_LONG=1d
```

---

## ⚙️ Konfigurasi Parameter

Semua parameter dikonfigurasi di file `.env`:

| Parameter | Default | Range | Deskripsi |
|-----------|---------|-------|-----------|
| `TRADING_PAIR` | `BTC/USDT` | Any pair | Pair crypto yang ditrade di Binance |
| `USE_TESTNET` | `True` | True/False | `True` = uang virtual, `False` = live |
| `TIMEFRAME_SHORT` | `15m` | 1m/5m/15m/1h | Timeframe pendek untuk entry timing |
| `TIMEFRAME_MEDIUM` | `1h` | 15m/1h/4h | Timeframe menengah sebagai acuan utama |
| `TIMEFRAME_LONG` | `4h` | 1h/4h/1d | Timeframe panjang untuk filter tren |
| `INTERVAL_SECONDS` | auto | 60–86400 | Override interval antar siklus (detik) |
| `MAX_DRAWDOWN_PCT` | `10` | 3–20 | % penurunan dari modal awal sebelum circuit breaker aktif |
| `TRAILING_STOP_ATR_MULTIPLIER` | `2.0` | 1.0–5.0 | Makin besar = stop lebih jauh dari harga |
| `MIN_POSITION_SIZE` | `5` | 1–10 | Ukuran posisi minimum (% dari USDT available) |
| `MAX_POSITION_SIZE` | `20` | 10–30 | Hard cap ukuran posisi |

### Tips Tuning Berdasarkan Kondisi Pasar

**Pasar Bull (tren naik kuat):**
```bash
MAX_DRAWDOWN_PCT=15        # Beri ruang lebih
TRAILING_STOP_ATR_MULTIPLIER=1.5  # Stop lebih ketat, kunci profit cepat
TIMEFRAME_SHORT=15m        # Ambil peluang di timeframe kecil
```

**Pasar Bear (tren turun):**
```bash
MAX_DRAWDOWN_PCT=5         # Proteksi ketat
TRAILING_STOP_ATR_MULTIPLIER=3.0  # Stop longgar, hindari noise
TIMEFRAME_SHORT=1h         # Lebih konservatif
# Pertimbangkan aktifkan profile Conservative
```

**Pasar Sideways (ranging):**
```bash
TRAILING_STOP_ATR_MULTIPLIER=2.5  # Hindari stop terlalu dekat di range
TIMEFRAME_MEDIUM=4h        # Filter sinyal dengan timeframe lebih panjang
```

---

## 🏗️ Arsitektur Teknis

### Alur Eksekusi Per Siklus

```
Setiap INTERVAL_SECONDS (default: 15 menit)
│
├─ 1. Cek Trailing Stop
│     └── Jika harga turun ke stop price → SELL otomatis
│
├─ 2. Fetch Multi-Timeframe Data
│     └── OHLCV + indikator (RSI, MA, MACD, BB, ATR) untuk 15m, 1h, 4h
│
├─ 3. HMM Classifier
│     └── Train ulang model → predict state (CRASH/BEAR/NEUTRAL/BULL/EUPHORIA)
│
├─ 4. Regime Detector
│     └── Hitung ADX, BB width, ATR → label TRENDING_UP/DOWN/SIDEWAYS/VOLATILE
│
├─ 5. Ensemble Signal
│     └── Rule model (0.3) + HMM model (0.3) + Momentum model (0.4)
│         → signal BUY/SELL/HOLD + score + consensus
│
├─ 6. Fear & Greed Index
│     └── Fetch alternative.me (cache 1 jam)
│
├─ 7. Format Context → Claude API
│     └── Semua data di atas dikemas sebagai prompt
│
├─ 8. Parse Response Claude
│     └── action, confidence, size_pct, reason
│
├─ 9. RiskManager.validate_decision()
│     ├── Cek circuit breaker
│     ├── Cek confidence
│     └── Hard cap size
│
├─ 10. PositionSizer.calculate()
│      └── Adjust size berdasarkan ATR, HMM, F&G, Regime
│
├─ 11. Executor.execute()
│      └── Kirim order ke Binance (market order)
│
└─ 12. Log + Telegram
       ├── Tulis ke logs/decisions.json
       └── Kirim notifikasi Telegram
```

### Penjelasan File Core

| File | Class/Fungsi Utama | Fungsi |
|------|--------------------|--------|
| `claude_brain.py` | `ask_claude()` | Komunikasi dengan Anthropic API |
| `market_data.py` | `fetch_multi_timeframe()` | Fetch OHLCV + hitung indikator |
| `hmm_classifier.py` | `HMMClassifier` | Train & predict HMM market state |
| `regime_detector.py` | `RegimeDetector` | Deteksi market regime via ADX/BB/ATR |
| `ensemble.py` | `EnsembleSignal` | Gabungkan 3 model menjadi 1 sinyal |
| `position_sizer.py` | `PositionSizer` | Hitung ukuran posisi dinamis |
| `risk_manager.py` | `RiskManager` | Circuit breaker + validasi keputusan |
| `executor.py` | `Executor` | Eksekusi order + trailing stop |
| `sentiment.py` | `SentimentFetcher` | Fetch Fear & Greed Index |
| `telegram_notifier.py` | `TelegramNotifier`, `TelegramCommandHandler` | Kirim notif + handle command |
| `profile_manager.py` | `load_profile()`, `get_active_profile()` | Manajemen config profiles |
| `performance_monitor.py` | `PerformanceMonitor` | Auto-tune parameter berdasarkan performa |
| `log_cleaner.py` | `LogCleaner` | Pembersihan dan arsip log otomatis |
| `health_check.py` | `HealthChecker` | Monitor kesehatan semua service |

### Format decisions.json

Setiap baris adalah satu siklus trading (JSONL format):

```json
{
  "timestamp": "2024-01-15T10:30:00",
  "action": "BUY",
  "confidence": "HIGH",
  "size_pct": 12.5,
  "total_value": 10250.50,
  "usdt_available": 8500.00,
  "btc_held": 0.041200,
  "reason": "RSI oversold (28), MA50 > MA200, BULL state dengan confidence 82%",
  "market_state": "BULL",
  "hmm_confidence": 0.82,
  "regime": "TRENDING_UP",
  "regime_confidence": 0.76,
  "ensemble_signal": "BUY",
  "ensemble_score": 0.58,
  "ensemble_consensus": "STRONG",
  "tf_15m_state": "BULL",
  "tf_1h_state": "BULL",
  "tf_4h_state": "NEUTRAL",
  "confluence": "BULLISH",
  "claude_size_pct": 15
}
```

| Field | Arti |
|-------|------|
| `action` | Keputusan final: BUY / SELL / HOLD |
| `confidence` | Keyakinan Claude: HIGH / MEDIUM / LOW |
| `size_pct` | Ukuran posisi setelah PositionSizer |
| `claude_size_pct` | Rekomendasi awal Claude sebelum disesuaikan |
| `ensemble_score` | Score voting, range -1.0 (full SELL) hingga +1.0 (full BUY) |
| `ensemble_consensus` | STRONG / WEAK / SPLIT |
| `hmm_confidence` | Keyakinan HMM terhadap state saat ini (0–1) |
| `confluence` | Arah konfluensi multi-TF: BULLISH / BEARISH / NEUTRAL |

---

## 🔧 Troubleshooting

| Error / Masalah | Penyebab | Solusi |
|-----------------|----------|--------|
| `ModuleNotFoundError: ccxt` | Dependencies belum diinstall | `pip install -r requirements.txt` |
| `Binance API timeout` | IP Indonesia sering diblokir Binance | Gunakan VPN atau tambahkan `proxies` di config |
| `APIError: Invalid API key` | API key salah atau sudah expired | Regenerate key di Binance Testnet |
| `credit balance too low` | Kredit Anthropic habis | Top up di [console.anthropic.com/billing](https://console.anthropic.com/billing) |
| `Telegram getUpdates error 401` | Token bot salah | Cek `TELEGRAM_BOT_TOKEN` di `.env` |
| `Chat not found` | Chat ID salah | Kirim pesan ke bot dulu, lalu cek getUpdates |
| `Circuit breaker aktif` | Drawdown melebihi `MAX_DRAWDOWN_PCT` | Cek log, evaluasi kondisi pasar, naikkan `MAX_DRAWDOWN_PCT` jika perlu restart |
| `streamlit: command not found` | Streamlit tidak di PATH | Gunakan `python -m streamlit run dashboard.py` |
| `HMM: not enough data` | Data candle terlalu sedikit untuk warmup | Tunggu beberapa siklus, atau turunkan `warmup` di backtest |
| `USDT available < 10` | Saldo USDT habis | Tambah saldo di Binance Testnet (klik "Deposit" di testnet) |
| Bot jalan tapi tidak ada notif | Telegram tidak dikonfigurasi | Cek `TELEGRAM_BOT_TOKEN` dan `TELEGRAM_CHAT_ID` di `.env` |
| `Bot sudah crash 5x` | Terlalu banyak crash dalam 1 jam | `bot_runner.py` berhenti otomatis. Cek `logs/crash.log`, perbaiki error, restart manual |
| Dashboard tidak update | Cache Streamlit belum refresh | Klik tombol "↻ Refresh" di dashboard |
| Backtest sangat lambat | Terlalu banyak candle | Kurangi `--candles` atau gunakan `--no-chart` |

---

### Cara Lihat Log

```bash
# Log utama bot
tail -f logs/bot.log

# Log crash
cat logs/crash.log

# Log auto-runner
cat logs/bot_runner.log

# Log maintenance (cleanup)
cat logs/maintenance.log

# Keputusan trading (JSONL)
tail -20 logs/decisions.json | python -m json.tool
```

---

### Reset Circuit Breaker

Jika circuit breaker aktif dan kamu yakin ingin lanjutkan trading:

1. Stop bot: Ctrl+C atau `/stop` via Telegram
2. Evaluasi kondisi pasar
3. Sesuaikan `MAX_DRAWDOWN_PCT` di `.env` jika diperlukan
4. Restart: `python bot_runner.py`

> ⚠️ Circuit breaker aktif karena alasan — selalu evaluasi dulu sebelum di-reset.

---

## ⚠️ Disclaimer

```
╔══════════════════════════════════════════════════════════════════╗
║                    ⚠️  PERINGATAN PENTING                        ║
╠══════════════════════════════════════════════════════════════════╣
║                                                                  ║
║  • Bot ini dibuat untuk tujuan EDUKASI dan PENELITIAN            ║
║                                                                  ║
║  • Trading cryptocurrency mengandung RISIKO TINGGI               ║
║    dan dapat mengakibatkan kehilangan seluruh modal              ║
║                                                                  ║
║  • Hasil backtesting TIDAK MENJAMIN profit di live trading       ║
║    Kondisi pasar nyata berbeda dengan data historis              ║
║                                                                  ║
║  • SELALU mulai dengan Binance Testnet (uang virtual)            ║
║    sebelum berani mencoba live trading                           ║
║                                                                  ║
║  • Jangan invest lebih dari yang siap kamu KEHILANGAN            ║
║                                                                  ║
║  • Developer TIDAK BERTANGGUNG JAWAB atas kerugian apapun        ║
║    yang timbul dari penggunaan bot ini                           ║
║                                                                  ║
║  • Konsultasikan dengan ahli keuangan sebelum investasi nyata    ║
║                                                                  ║
╚══════════════════════════════════════════════════════════════════╝
```

---

## 📁 Struktur Project

```
trading-bot/
├── .env                    # Konfigurasi API keys & parameter
├── requirements.txt        # Python dependencies
├── config.py               # (reserved)
├── main.py                 # Entry point utama bot
├── dashboard.py            # Dashboard Streamlit (port 8501)
├── config_panel.py         # Config Panel Streamlit (port 8502)
├── bot_runner.py           # Wrapper auto-restart dengan crash protection
├── backtest.py             # CLI backtesting
│
├── profiles/               # Config profiles siap pakai
│   ├── aggressive.json
│   ├── conservative.json
│   └── scalping.json
│
├── logs/                   # Log files (auto-generated)
│   ├── bot.log             # Log utama
│   ├── decisions.json      # History keputusan (JSONL)
│   ├── health.json         # Status kesehatan service
│   ├── perf_state.json     # State performance monitor
│   ├── crash.log           # Log crash
│   ├── maintenance.log     # Log log-cleaner
│   └── archive/            # Arsip log lama (gzip)
│
└── core/                   # Modul inti
    ├── claude_brain.py     # Integrasi Anthropic API
    ├── market_data.py      # Fetch data & hitung indikator
    ├── hmm_classifier.py   # Hidden Markov Model classifier
    ├── regime_detector.py  # Market regime detection
    ├── ensemble.py         # Ensemble 3-model voting
    ├── position_sizer.py   # Dynamic position sizing
    ├── risk_manager.py     # Risk management & circuit breaker
    ├── executor.py         # Order execution & trailing stop
    ├── sentiment.py        # Fear & Greed Index fetcher
    ├── telegram_notifier.py# Telegram bot & command handler
    ├── profile_manager.py  # Config profile management
    ├── performance_monitor.py # Auto parameter tuning
    ├── log_cleaner.py      # Automated log maintenance
    └── health_check.py     # Service health monitoring
```

---

*Built with ❤️ using Python, Claude AI, Streamlit, CCXT, and scikit-learn.*
