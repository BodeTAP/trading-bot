"""
Web Config Panel for the Trading Bot.

Run with:
    streamlit run config_panel.py --server.port 8502
"""

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
import streamlit as st
from dotenv import dotenv_values

from core.profile_manager import (
    get_active_profile,
    list_profiles,
    load_profile,
    save_current_as_profile,
)

_ENV_FILE = Path(".env")

st.set_page_config(
    page_title="Bot Config Panel",
    page_icon="⚙️",
    layout="wide",
)

st.markdown("""
<style>
    .profile-card {
        background: #1e1e2e;
        border-radius: 12px;
        padding: 18px 20px;
        border: 2px solid #44475a;
    }
    .profile-card.active {
        border-color: #50fa7b;
    }
    .active-badge {
        display: inline-block;
        background: #50fa7b;
        color: #0a1a0a;
        font-weight: 700;
        font-size: 0.75rem;
        padding: 2px 10px;
        border-radius: 20px;
        margin-left: 8px;
        vertical-align: middle;
    }
    .section-divider { border-top: 1px solid #44475a; margin: 8px 0 16px; }
</style>
""", unsafe_allow_html=True)

st.title("⚙️ Bot Config Panel")
st.caption("Konfigurasi trading bot — perubahan disimpan ke .env dan aktif setelah restart.")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _read_env() -> dict:
    if not _ENV_FILE.exists():
        return {}
    return dict(dotenv_values(_ENV_FILE))


def _write_env(updates: dict) -> None:
    """Merge `updates` into .env, preserving comments and order."""
    lines = _ENV_FILE.read_text(encoding="utf-8").splitlines() if _ENV_FILE.exists() else []
    remaining = dict(updates)
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            new_lines.append(line)
            continue
        key = stripped.partition("=")[0].strip()
        if key in remaining:
            new_lines.append(f"{key}={remaining.pop(key)}")
        else:
            new_lines.append(line)
    for key, value in remaining.items():
        new_lines.append(f"{key}={value}")
    _ENV_FILE.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def _bot_is_running() -> bool:
    """Check if main.py or bot_runner.py is in the process list."""
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq python.exe", "/FO", "CSV"],
            capture_output=True, text=True, timeout=5,
        )
        return "python" in result.stdout.lower()
    except Exception:
        return False


def _send_telegram(text: str) -> bool:
    env = _read_env()
    token   = env.get("TELEGRAM_BOT_TOKEN")
    chat_id = env.get("TELEGRAM_CHAT_ID")
    if not (token and chat_id):
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
        return r.status_code == 200
    except Exception:
        return False


def _env_mtime() -> str:
    if _ENV_FILE.exists():
        ts = datetime.fromtimestamp(_ENV_FILE.stat().st_mtime)
        return ts.strftime("%d %b %Y, %H:%M:%S")
    return "—"


# ── Section: Config Profiles ───────────────────────────────────────────────────

st.subheader("🎛️ Config Profiles")
profiles    = list_profiles()
active_name = get_active_profile()

if not profiles:
    st.info("Belum ada profile. Buat profile pertama di bawah.")
else:
    cols = st.columns(len(profiles))
    for i, prof in enumerate(profiles):
        name  = prof.get("name", prof.get("_file", "?"))
        desc  = prof.get("description", "")
        is_active = (active_name == name)

        with cols[i]:
            badge = '<span class="active-badge">AKTIF</span>' if is_active else ""
            card_cls = "profile-card active" if is_active else "profile-card"
            st.markdown(
                f'<div class="{card_cls}">'
                f'<b>{name}</b>{badge}<br>'
                f'<small style="color:#8be9fd">{desc}</small>'
                f'</div>',
                unsafe_allow_html=True,
            )
            st.caption(
                f"Drawdown max: {prof.get('MAX_DRAWDOWN_PCT', '—')}%  |  "
                f"ATR mult: {prof.get('TRAILING_STOP_ATR_MULTIPLIER', '—')}  |  "
                f"TF: {prof.get('TIMEFRAME_SHORT','?')}/{prof.get('TIMEFRAME_MEDIUM','?')}/{prof.get('TIMEFRAME_LONG','?')}"
            )
            if not is_active:
                slug = prof.get("_file", name.lower())
                if st.button(f"Aktifkan {name}", key=f"activate_{slug}", use_container_width=True):
                    try:
                        load_profile(slug)
                        _send_telegram(f"🔧 <b>Config profile diubah ke <i>{name}</i>.</b>\nRestart bot untuk apply perubahan.")
                        st.success(f"Profile <b>{name}</b> diaktifkan. Restart bot untuk apply.", icon="✅")
                        st.cache_data.clear()
                        time.sleep(0.5)
                        st.rerun()
                    except Exception as e:
                        st.error(f"Gagal mengaktifkan profile: {e}")
            else:
                st.success("Profile ini sedang aktif", icon="✅")

st.markdown("<div class='section-divider'/>", unsafe_allow_html=True)

with st.expander("💾 Simpan Konfigurasi Saat Ini sebagai Profile Baru"):
    p_name = st.text_input("Nama Profile", placeholder="Contoh: My Custom")
    p_desc = st.text_input("Deskripsi", placeholder="Singkat tentang strategi ini")
    if st.button("Simpan sebagai Profile Baru", disabled=not p_name):
        if p_name:
            try:
                out = save_current_as_profile(p_name, p_desc)
                st.success(f"Profile '{p_name}' disimpan ke {out.name}")
                time.sleep(0.5)
                st.rerun()
            except Exception as e:
                st.error(f"Gagal menyimpan: {e}")

st.divider()


# ── Section: Trading Settings ──────────────────────────────────────────────────

env = _read_env()

st.subheader("📈 Trading Settings")

col1, col2 = st.columns(2)

with col1:
    trading_pair = st.selectbox(
        "TRADING_PAIR",
        options=["BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT"],
        index=["BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT"].index(
            env.get("TRADING_PAIR", "BTC/USDT")
        ) if env.get("TRADING_PAIR", "BTC/USDT") in ["BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT"] else 0,
    )

    tf_options = ["1m", "5m", "15m", "30m", "1h", "4h", "1d"]
    tf_short = st.selectbox(
        "TIMEFRAME_SHORT",
        options=tf_options,
        index=tf_options.index(env.get("TIMEFRAME_SHORT", "15m"))
        if env.get("TIMEFRAME_SHORT", "15m") in tf_options else 2,
    )
    tf_medium = st.selectbox(
        "TIMEFRAME_MEDIUM",
        options=tf_options,
        index=tf_options.index(env.get("TIMEFRAME_MEDIUM", "1h"))
        if env.get("TIMEFRAME_MEDIUM", "1h") in tf_options else 4,
    )
    tf_long = st.selectbox(
        "TIMEFRAME_LONG",
        options=tf_options,
        index=tf_options.index(env.get("TIMEFRAME_LONG", "4h"))
        if env.get("TIMEFRAME_LONG", "4h") in tf_options else 5,
    )

with col2:
    max_drawdown = st.slider(
        "MAX_DRAWDOWN_PCT (%)",
        min_value=5, max_value=30, step=1,
        value=int(env.get("MAX_DRAWDOWN_PCT", 10)),
    )
    trailing_mult = st.slider(
        "TRAILING_STOP_ATR_MULTIPLIER",
        min_value=1.0, max_value=4.0, step=0.1,
        value=float(env.get("TRAILING_STOP_ATR_MULTIPLIER", 2.0)),
    )

st.divider()


# ── Section: API Keys ──────────────────────────────────────────────────────────

st.subheader("🔑 API Keys")

col_k1, col_k2 = st.columns(2)

with col_k1:
    binance_key    = st.text_input("BINANCE_API_KEY",    value=env.get("BINANCE_API_KEY", ""),    type="password")
    binance_secret = st.text_input("BINANCE_SECRET_KEY", value=env.get("BINANCE_SECRET_KEY", ""), type="password")
    anthropic_key  = st.text_input("ANTHROPIC_API_KEY",  value=env.get("ANTHROPIC_API_KEY", ""),  type="password")

with col_k2:
    tg_token   = st.text_input("TELEGRAM_BOT_TOKEN", value=env.get("TELEGRAM_BOT_TOKEN", ""), type="password")
    tg_chat_id = st.text_input("TELEGRAM_CHAT_ID",   value=env.get("TELEGRAM_CHAT_ID", ""))

# Test connections
conn_col1, conn_col2, conn_col3, conn_col4 = st.columns(4)

with conn_col1:
    if st.button("Test Binance", use_container_width=True):
        try:
            import ccxt
            exchange = ccxt.binance({"apiKey": binance_key, "secret": binance_secret})
            exchange.set_sandbox_mode(True)
            balance = exchange.fetch_balance()
            st.success("Binance OK ✓")
        except Exception as e:
            st.error(f"Binance gagal: {str(e)[:120]}")

with conn_col2:
    if st.button("Test Anthropic", use_container_width=True):
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=anthropic_key)
            client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=10,
                messages=[{"role": "user", "content": "hi"}],
            )
            st.success("Anthropic OK ✓")
        except Exception as e:
            st.error(f"Anthropic gagal: {str(e)[:120]}")

with conn_col3:
    if st.button("Test Telegram", use_container_width=True):
        try:
            r = requests.get(
                f"https://api.telegram.org/bot{tg_token}/getMe",
                timeout=8,
            )
            if r.status_code == 200:
                bot_name = r.json().get("result", {}).get("username", "?")
                st.success(f"Telegram OK — @{bot_name}")
            else:
                st.error(f"Telegram: HTTP {r.status_code}")
        except Exception as e:
            st.error(f"Telegram gagal: {str(e)[:120]}")

with conn_col4:
    if st.button("Kirim Test Pesan", use_container_width=True):
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{tg_token}/sendMessage",
                json={"chat_id": tg_chat_id, "text": "✅ Test dari Config Panel berhasil!"},
                timeout=8,
            )
            if r.status_code == 200:
                st.success("Pesan terkirim ✓")
            else:
                st.error(f"HTTP {r.status_code}: {r.text[:100]}")
        except Exception as e:
            st.error(f"Gagal: {str(e)[:120]}")

st.divider()


# ── Section: Risk Management ───────────────────────────────────────────────────

st.subheader("🛡️ Risk Management")

risk_col1, risk_col2 = st.columns(2)

with risk_col1:
    max_position = st.slider(
        "MAX_POSITION_SIZE_PCT (%)",
        min_value=5, max_value=20, step=1,
        value=int(env.get("MAX_POSITION_SIZE_PCT", 20)),
    )

with risk_col2:
    ens_buy = st.slider(
        "ENSEMBLE_BUY_THRESHOLD",
        min_value=0.10, max_value=0.50, step=0.05,
        value=float(env.get("ENSEMBLE_BUY_THRESHOLD", 0.30)),
        format="%.2f",
    )
    ens_sell = st.slider(
        "ENSEMBLE_SELL_THRESHOLD",
        min_value=-0.50, max_value=-0.10, step=0.05,
        value=float(env.get("ENSEMBLE_SELL_THRESHOLD", -0.30)),
        format="%.2f",
    )

st.divider()


# ── Action buttons ─────────────────────────────────────────────────────────────

_DEFAULTS = {
    "TRADING_PAIR":                "BTC/USDT",
    "TIMEFRAME_SHORT":             "15m",
    "TIMEFRAME_MEDIUM":            "1h",
    "TIMEFRAME_LONG":              "4h",
    "MAX_DRAWDOWN_PCT":            "10",
    "TRAILING_STOP_ATR_MULTIPLIER":"2.0",
    "MAX_POSITION_SIZE_PCT":       "20",
    "ENSEMBLE_BUY_THRESHOLD":      "0.30",
    "ENSEMBLE_SELL_THRESHOLD":     "-0.30",
}

act_col1, act_col2, act_col3 = st.columns(3)

with act_col1:
    if st.button("💾 Simpan Konfigurasi", type="primary", use_container_width=True):
        updates = {
            "TRADING_PAIR":                 trading_pair,
            "TIMEFRAME_SHORT":              tf_short,
            "TIMEFRAME_MEDIUM":             tf_medium,
            "TIMEFRAME_LONG":               tf_long,
            "MAX_DRAWDOWN_PCT":             str(max_drawdown),
            "TRAILING_STOP_ATR_MULTIPLIER": f"{trailing_mult:.1f}",
            "BINANCE_API_KEY":              binance_key,
            "BINANCE_SECRET_KEY":           binance_secret,
            "ANTHROPIC_API_KEY":            anthropic_key,
            "TELEGRAM_BOT_TOKEN":           tg_token,
            "TELEGRAM_CHAT_ID":             tg_chat_id,
            "MAX_POSITION_SIZE_PCT":        str(max_position),
            "ENSEMBLE_BUY_THRESHOLD":       f"{ens_buy:.2f}",
            "ENSEMBLE_SELL_THRESHOLD":      f"{ens_sell:.2f}",
        }
        _write_env(updates)
        st.success("✅ Konfigurasi disimpan ke .env. Restart bot untuk apply.", icon="💾")

with act_col2:
    if st.button("🔄 Reset ke Default", use_container_width=True):
        _write_env(_DEFAULTS)
        st.info("Konfigurasi direset ke default. Halaman akan direfresh.", icon="🔄")
        time.sleep(0.5)
        st.rerun()

with act_col3:
    if st.button("🔁 Restart Bot", use_container_width=True, type="secondary"):
        st.warning("Fitur restart memerlukan bot berjalan via bot_runner.py.")
        # If bot_runner is the parent, just signal main.py to exit (exit code 0
        # won't trigger a restart — we want a restart so we kill it differently).
        # As a best-effort: find and terminate main.py process, bot_runner restarts it.
        try:
            result = subprocess.run(
                ["taskkill", "/F", "/IM", "python.exe", "/FI", "WINDOWTITLE eq main*"],
                capture_output=True, text=True, timeout=5,
            )
            st.info("Signal restart dikirim ke proses python. bot_runner akan restart dalam 30 detik.")
        except Exception as e:
            st.error(f"Gagal: {e}")


st.divider()


# ── Status bar ─────────────────────────────────────────────────────────────────

st.subheader("📡 Status")
stat_col1, stat_col2, stat_col3 = st.columns(3)

with stat_col1:
    active_prof = get_active_profile()
    st.metric("Profile Aktif", active_prof or "—")

with stat_col2:
    st.metric(".env Terakhir Diubah", _env_mtime())

with stat_col3:
    # Simple running check: look for recent log activity
    log_file = Path("logs/bot.log")
    if log_file.exists():
        age = time.time() - log_file.stat().st_mtime
        if age < 300:
            st.markdown("**Status Bot:** <span style='color:#50fa7b'>● Running</span>", unsafe_allow_html=True)
        elif age < 3600:
            st.markdown("**Status Bot:** <span style='color:#f1fa8c'>● Idle</span>", unsafe_allow_html=True)
        else:
            st.markdown("**Status Bot:** <span style='color:#ff5555'>● Stopped</span>", unsafe_allow_html=True)
    else:
        st.markdown("**Status Bot:** <span style='color:#6272a4'>● Unknown</span>", unsafe_allow_html=True)

if st.button("🔄 Refresh Status"):
    st.rerun()
