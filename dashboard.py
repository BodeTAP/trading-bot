import json
import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from pathlib import Path
from datetime import datetime, timedelta
import time
import os
from dotenv import load_dotenv
from core.sentiment import SentimentFetcher

load_dotenv()

DECISIONS_LOG = Path("logs/decisions.json")
PAIR = os.getenv("TRADING_PAIR", "BTC/USDT")
BASE = PAIR.split("/")[0]   # "BTC", "ETH", "BNB", dll
INTERVAL_SECONDS = int(os.getenv("INTERVAL_SECONDS", 3600))
REFRESH_SECONDS = 60

st.set_page_config(
    page_title="Maffiso Trading Bot",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
/* ── Base ─────────────────────────────────────────────────── */
.stApp,[data-testid="stAppViewContainer"],[data-testid="stMain"] {
    background:#0F1117 !important;
}
[data-testid="stSidebar"] {
    background:#141720 !important;
    border-right:1px solid #1E2130 !important;
}
[data-testid="stSidebar"] section { padding:1rem 0.75rem !important; }

/* ── Typography ───────────────────────────────────────────── */
h1,h2,h3,h4 { color:#F1F5F9 !important; font-weight:700 !important; letter-spacing:-0.02em !important; }
p,[data-testid="stMarkdownContainer"] p { color:#94A3B8; }
label { color:#94A3B8 !important; font-size:0.82rem !important; }
.stCaption,[data-testid="stCaptionContainer"] { color:#4B5563 !important; }

/* ── Tabs ─────────────────────────────────────────────────── */
.stTabs [data-baseweb="tab-list"] {
    background:transparent !important;
    border-bottom:1px solid #1E2130 !important;
    gap:0 !important; padding:0 !important;
}
.stTabs [data-baseweb="tab"] {
    background:transparent !important; color:#64748B !important;
    border:none !important; border-bottom:2px solid transparent !important;
    padding:8px 20px !important; font-weight:500 !important; font-size:0.9rem !important;
}
.stTabs [aria-selected="true"] {
    color:#0EA5E9 !important; border-bottom:2px solid #0EA5E9 !important;
    background:transparent !important;
}
[data-testid="stTabContent"] { padding-top:20px !important; }

/* ── Buttons ──────────────────────────────────────────────── */
.stButton>button {
    border-radius:8px !important; font-weight:600 !important;
    font-size:0.85rem !important; transition:all 0.15s ease !important;
    padding:6px 16px !important;
}
.stButton>button[kind="primary"] {
    background:#0EA5E9 !important; border:none !important; color:#fff !important;
}
.stButton>button[kind="secondary"] {
    background:#1A1D27 !important; border:1px solid #2A2D3A !important; color:#94A3B8 !important;
}
.stButton>button:not([kind]):not([disabled]) {
    background:#1A1D27 !important; border:1px solid #2A2D3A !important; color:#94A3B8 !important;
}

/* ── Inputs ───────────────────────────────────────────────── */
.stTextInput>div>div>input,.stTextArea textarea {
    background:#1A1D27 !important; border:1px solid #2A2D3A !important;
    border-radius:8px !important; color:#F1F5F9 !important;
}
.stTextInput>div>div>input:focus { border-color:#0EA5E9 !important; box-shadow:0 0 0 1px #0EA5E9 !important; }
.stSelectbox [data-baseweb="select"]>div {
    background:#1A1D27 !important; border:1px solid #2A2D3A !important;
    border-radius:8px !important; color:#F1F5F9 !important;
}
.stSelectbox [data-baseweb="popover"] { background:#1A1D27 !important; border:1px solid #2A2D3A !important; }
[role="listbox"] li { background:#1A1D27 !important; color:#94A3B8 !important; }
[role="listbox"] li:hover { background:#252836 !important; }
[data-baseweb="slider"] [data-testid="stThumbValue"] { background:#0EA5E9 !important; }
[data-baseweb="slider"] div[role="slider"] { background:#0EA5E9 !important; border-color:#0EA5E9 !important; }

/* ── Expanders ────────────────────────────────────────────── */
[data-testid="stExpander"] details {
    background:#1A1D27 !important; border:1px solid #2A2D3A !important; border-radius:8px !important;
}
[data-testid="stExpander"] summary {
    color:#94A3B8 !important; font-weight:600 !important; font-size:0.88rem !important;
}

/* ── Metrics ──────────────────────────────────────────────── */
[data-testid="metric-container"] {
    background:#1A1D27; border:1px solid #2A2D3A; border-radius:12px; padding:16px 20px;
}
[data-testid="stMetricLabel"]  { color:#64748B !important; font-size:0.72rem !important; font-weight:700 !important; text-transform:uppercase; letter-spacing:0.07em; }
[data-testid="stMetricValue"]  { color:#F1F5F9 !important; font-size:1.5rem !important; font-weight:700 !important; }
[data-testid="stMetricDelta"]  { font-size:0.78rem !important; }

/* ── Alerts ───────────────────────────────────────────────── */
[data-testid="stAlert"][data-type="success"] { background:#0D2D1E !important; border-color:#10B981 !important; }
[data-testid="stAlert"][data-type="error"]   { background:#2D0D0D !important; border-color:#EF4444 !important; }
[data-testid="stAlert"][data-type="warning"] { background:#2D1A0D !important; border-color:#F59E0B !important; }
[data-testid="stAlert"][data-type="info"]    { background:#0D1E2D !important; border-color:#0EA5E9 !important; }

/* ── Dataframe ────────────────────────────────────────────── */
[data-testid="stDataFrameResizable"] { background:#1A1D27 !important; border-radius:12px !important; }

/* ── Progress bars ────────────────────────────────────────── */
[data-testid="stProgressBar"]>div { background:#1E2130 !important; border-radius:4px !important; }
[data-testid="stProgressBar"]>div>div { background:#0EA5E9 !important; border-radius:4px !important; }

/* ── Dividers ─────────────────────────────────────────────── */
hr { border-color:#1E2130 !important; margin:12px 0 !important; }

/* ── Toggle ───────────────────────────────────────────────── */
[data-testid="stToggle"] [role="switch"][aria-checked="true"] { background:#0EA5E9 !important; }

/* ── Scrollbar ────────────────────────────────────────────── */
::-webkit-scrollbar { width:5px; height:5px; }
::-webkit-scrollbar-track { background:#0F1117; }
::-webkit-scrollbar-thumb { background:#2A2D3A; border-radius:3px; }
::-webkit-scrollbar-thumb:hover { background:#3A4050; }
</style>
""", unsafe_allow_html=True)

# ── Palette ───────────────────────────────────────────────────────────────────

HMM_COLORS = {
    "CRASH":   "#EF4444", "BEAR":    "#F97316",
    "NEUTRAL": "#64748B", "BULL":    "#10B981", "EUPHORIA":"#0EA5E9",
}
HMM_TEXT_COLORS = {
    "CRASH":   "#fff", "BEAR":    "#fff",
    "NEUTRAL": "#fff", "BULL":    "#fff", "EUPHORIA":"#fff",
}
FNG_COLORS      = {}   # unused in new design — color computed inline
FNG_TEXT_COLORS = {}   # unused in new design


# ── Data loading ──────────────────────────────────────────────────────────────

@st.cache_data(ttl=30)
def load_decisions() -> pd.DataFrame:
    if not DECISIONS_LOG.exists() or DECISIONS_LOG.stat().st_size == 0:
        return pd.DataFrame()

    rows = []
    with open(DECISIONS_LOG, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    import json
                    rows.append(json.loads(line))
                except Exception:
                    continue

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)
    return df


# ── Derived metrics ───────────────────────────────────────────────────────────

def bot_status(df: pd.DataFrame) -> tuple[str, str]:
    """Return (label, css_class) based on last log timestamp."""
    if df.empty:
        return "No Data", "status-idle"
    last_ts = df["timestamp"].iloc[-1]
    age_seconds = (datetime.now() - last_ts.to_pydatetime()).total_seconds()
    if age_seconds < INTERVAL_SECONDS * 1.5:
        return "Running", "status-running"
    if age_seconds < INTERVAL_SECONDS * 3:
        return "Idle", "status-idle"
    return "Stopped", "status-stopped"


def trades_today(df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    today = datetime.now().date()
    mask = (df["timestamp"].dt.date == today) & (df["action"] != "HOLD")
    return int(mask.sum())


def win_rate(df: pd.DataFrame) -> str:
    """
    Win = non-HOLD decision where total_value in the NEXT cycle was higher
    than total_value at decision time.
    """
    if len(df) < 2:
        return "N/A"
    trades = df[df["action"] != "HOLD"].copy()
    if trades.empty:
        return "N/A"

    wins = 0
    for idx in trades.index:
        later = df[df.index > idx]
        if later.empty:
            continue
        next_value = later.iloc[0]["total_value"]
        current_value = df.loc[idx, "total_value"]
        if next_value > current_value:
            wins += 1

    total = len(trades[trades.index < df.index[-1]])
    if total == 0:
        return "N/A"
    return f"{wins / total * 100:.0f}%"


def drawdown(df: pd.DataFrame) -> tuple[str, float]:
    """Return (formatted string, raw pct) drawdown from starting balance."""
    if df.empty:
        return "N/A", 0.0
    start = df["total_value"].iloc[0]
    current = df["total_value"].iloc[-1]
    if start <= 0:
        return "N/A", 0.0
    dd = ((start - current) / start) * 100
    sign = "-" if dd > 0 else "+"
    return f"{sign}{abs(dd):.2f}%", dd


# ── Backtest helpers ──────────────────────────────────────────────────────────

@st.cache_data(ttl=60)
def load_health_status() -> dict | None:
    p = Path("logs/health.json")
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


@st.cache_data(ttl=60)
def load_perf_state() -> dict:
    p = Path("logs/perf_state.json")
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def log_storage_info() -> dict:
    logs_dir = Path("logs")
    if not logs_dir.exists():
        return {"total_mb": 0.0, "decisions_kb": 0.0, "archive_count": 0}
    total_bytes  = sum(f.stat().st_size for f in logs_dir.rglob("*") if f.is_file())
    dec          = Path("logs/decisions.json")
    decisions_kb = dec.stat().st_size / 1024 if dec.exists() else 0.0
    arch_dir     = Path("logs/archive")
    archive_count = len(list(arch_dir.rglob("*.*"))) if arch_dir.exists() else 0
    return {
        "total_mb":     round(total_bytes / 1_048_576, 1),
        "decisions_kb": round(decisions_kb, 1),
        "archive_count": archive_count,
    }


@st.cache_data(ttl=3600)
def load_sentiment() -> dict | None:
    try:
        return SentimentFetcher().fetch()
    except Exception:
        return None


@st.cache_data(ttl=10)
def load_backtest_results() -> dict | None:
    from core.backtester import Backtester
    return Backtester.load_results()


def _run_backtest_subprocess(capital: float, pair: str, timeframe: str,
                              candles: int, warmup: int) -> tuple[bool, str]:
    import subprocess
    cmd = [
        "python", "backtest.py",
        "--capital",   str(capital),
        "--pair",      pair,
        "--timeframe", timeframe,
        "--candles",   str(candles),
        "--warmup",    str(warmup),
        "--no-chart",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if proc.returncode == 0:
            return True, proc.stdout
        return False, proc.stderr or proc.stdout
    except subprocess.TimeoutExpired:
        return False, "Backtest timeout (> 5 menit)"
    except Exception as e:
        return False, str(e)


# ── Layout ────────────────────────────────────────────────────────────────────

# ── HTML / chart helpers ──────────────────────────────────────────────────────

_CS = "background:#1A1D27;border:1px solid #2A2D3A;border-radius:12px"
_TP = "rgba(0,0,0,0)"
_GR = "#1E2130"

def _card(body: str, pad: str = "16px 20px") -> str:
    return f'<div style="{_CS};padding:{pad}">{body}</div>'

def _lbl(text: str) -> str:
    return (f'<div style="color:#64748B;font-size:0.68rem;font-weight:700;'
            f'text-transform:uppercase;letter-spacing:0.08em;margin-bottom:8px">{text}</div>')

def _bdg(text: str, bg: str, fg: str = "#fff") -> str:
    return (f'<span style="display:inline-block;background:{bg};color:{fg};'
            f'padding:3px 12px;border-radius:20px;font-size:0.75rem;font-weight:700;'
            f'letter-spacing:0.04em">{text}</span>')

def _mc(label: str, value: str, delta: str = "", up: bool | None = None) -> str:
    dc = "#10B981" if up is True else ("#EF4444" if up is False else "#94A3B8")
    ar = "▲ " if up is True else ("▼ " if up is False else "")
    dt = (f'<div style="color:{dc};font-size:0.78rem;margin-top:4px">{ar}{delta}</div>'
          if delta else "")
    return (f'<div style="{_CS};padding:16px 20px">'
            f'{_lbl(label)}'
            f'<div style="color:#F1F5F9;font-size:1.5rem;font-weight:700;'
            f'letter-spacing:-0.02em;line-height:1.1">{value}</div>{dt}</div>')

def _pbar(pct: int, color: str = "#0EA5E9", h: int = 4) -> str:
    return (f'<div style="height:{h}px;background:#1E2130;border-radius:{h//2}px;margin-top:4px">'
            f'<div style="height:100%;width:{min(pct,100)}%;background:{color};'
            f'border-radius:{h//2}px"></div></div>')

def _fig_base(fig, height: int, margin=None, yformat: str | None = None,
              legend: bool = False) -> None:
    kw: dict = dict(
        template="plotly_dark", paper_bgcolor=_TP, plot_bgcolor=_TP, height=height,
        margin=margin or dict(l=0, r=0, t=0, b=0),
        xaxis=dict(showgrid=False, color="#64748B", tickfont=dict(size=10), zeroline=False),
        yaxis=dict(showgrid=True, gridcolor=_GR, color="#64748B",
                   tickfont=dict(size=10), zeroline=False),
        showlegend=legend,
    )
    if yformat:
        kw["yaxis"]["tickformat"] = yformat
    if legend:
        kw["legend"] = dict(orientation="h", yanchor="bottom", y=1.02, x=0,
                            font=dict(size=11, color="#94A3B8"))
    fig.update_layout(**kw)

_ACT_BG = {"BUY": "#0A2015", "SELL": "#1E0A0A", "HOLD": "#141720"}
_ACT_FG = {"BUY": "#10B981", "SELL": "#EF4444", "HOLD": "#475569"}
_DOT    = {"OK":"#10B981","SLOW":"#F59E0B","DOWN":"#EF4444",
           "LOW_CREDIT":"#F59E0B","WARNING":"#F59E0B","UNKNOWN":"#475569"}

# ── Data ──────────────────────────────────────────────────────────────────────

df          = load_decisions()
health      = load_health_status()
perf_state  = load_perf_state()

# ── Sidebar ───────────────────────────────────────────────────────────────────

_STATUS_ICON = {"OK": "🟢", "SLOW": "🟡", "DOWN": "🔴", "LOW_CREDIT": "🟡", "WARNING": "🟡", "UNKNOWN": "⚪"}

with st.sidebar:
    st.markdown("### 🏥 System Health")
    if health:
        ts = health.get("timestamp", "")[:16].replace("T", " ")
        st.caption(f"Cek terakhir: {ts}")
        for svc, label in [("binance","Binance"), ("anthropic","Anthropic"), ("telegram","Telegram")]:
            s    = health.get(svc, {})
            stat = s.get("status", "UNKNOWN")
            lat  = s.get("latency_ms")
            lat_str = f" · {lat} ms" if lat else ""
            icon = _STATUS_ICON.get(stat, "⚪")
            st.markdown(f"{icon} **{label}**: {stat}{lat_str}")
        sys_r = health.get("system", {})
        if sys_r:
            cpu  = sys_r.get("cpu_pct", "?")
            mem  = sys_r.get("mem_pct", "?")
            disk = sys_r.get("disk_mb", "?")
            st.caption(f"CPU: {cpu}%  |  RAM: {mem}%  |  Disk: {disk} MB")
            if sys_r.get("cpu_pct") is not None:
                st.progress(min(100, int(sys_r["cpu_pct"])) / 100, text=f"CPU {cpu}%")
            if sys_r.get("mem_pct") is not None:
                st.progress(min(100, int(sys_r["mem_pct"])) / 100, text=f"RAM {mem}%")
    else:
        st.caption("Health data belum tersedia")

    st.divider()

    st.markdown("### 🗂️ Log Storage")
    storage = log_storage_info()
    st.metric("Total logs/", f"{storage['total_mb']} MB")
    st.metric("decisions.json", f"{storage['decisions_kb']} KB")
    st.metric("Arsip", f"{storage['archive_count']} file")

    if st.button("🧹 Cleanup Sekarang", use_container_width=True):
        with st.spinner("Membersihkan log..."):
            try:
                from core.log_cleaner import LogCleaner as _LC
                class _FakeNotifier:
                    def _send(self, *_): pass
                summary = _LC(_FakeNotifier()).run_now()
                d, c, mb = summary["decisions_archived"], summary["logs_compressed"], summary["space_freed_mb"]
                st.success(f"Selesai: {d} decisions diarsipkan, {c} log dikompres ({mb:.1f} MB freed)")
                st.cache_data.clear()
            except Exception as e:
                st.error(f"Gagal: {e}")

    st.divider()
    st.caption("⚙️ [Buka Config Panel](http://localhost:8502)")

# ── Health banner (shown if any service DOWN) ─────────────────────────────────

if health:
    down_svcs = [
        svc.capitalize()
        for svc in ("binance", "anthropic", "telegram")
        if health.get(svc, {}).get("status") == "DOWN"
    ]
    if down_svcs:
        st.error(
            f"🔴 **Service DOWN:** {', '.join(down_svcs)} — "
            "Periksa koneksi atau API key di Config Panel.",
            icon="🚨",
        )

# ── Header ────────────────────────────────────────────────────────────────────

col_title, col_mid, col_refresh = st.columns([3, 2, 1])
with col_title:
    try:
        from core.profile_manager import get_active_profile as _get_profile
        _active_profile = _get_profile()
    except Exception:
        _active_profile = None
    _profile_html = (
        f' {_bdg(_active_profile, "#1E3A5F", "#0EA5E9")}'
        if _active_profile else ""
    )
    st.markdown(
        f'<div style="padding:8px 0 4px">'
        f'<span style="font-size:1.6rem;font-weight:800;color:#F1F5F9;letter-spacing:-0.03em">'
        f'📈 Trading Bot</span>{_profile_html}</div>',
        unsafe_allow_html=True,
    )
    if not df.empty:
        st.caption(f"Data terakhir: {df['timestamp'].iloc[-1].strftime('%d %b %Y, %H:%M:%S')}")
    else:
        st.caption("Belum ada data")

with col_mid:
    st.markdown("<br>", unsafe_allow_html=True)
    _lbl_txt, _ = bot_status(df)
    _dot_c = "#10B981" if _lbl_txt == "Running" else ("#F59E0B" if _lbl_txt == "Idle" else "#EF4444")
    st.markdown(
        f'<div style="display:flex;align-items:center;gap:8px;padding:6px 0">'
        f'<div style="width:8px;height:8px;border-radius:50%;background:{_dot_c};'
        f'box-shadow:0 0 8px {_dot_c}40"></div>'
        f'<span style="color:#94A3B8;font-size:0.9rem">{_lbl_txt}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

with col_refresh:
    st.markdown("<br>", unsafe_allow_html=True)
    if st.button("↻ Refresh", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

tab_live, tab_chart, tab_bt = st.tabs(["📊 Live Trading", "📈 Chart", "🔬 Backtesting"])

# ═══════════════════════════════════════════════════════ TAB: Live Trading ════

with tab_live:

    # ── 5 metric cards ────────────────────────────────────────────────────────
    _cv     = df["total_value"].iloc[-1] if not df.empty else 0.0
    _pv     = df["total_value"].iloc[-2] if len(df) >= 2  else _cv
    _vd     = _cv - _pv
    _dd_str, _dd_raw = drawdown(df)
    _la     = df.iloc[-1]["action"] if not df.empty else "—"
    _wr     = win_rate(df)
    _wr_num = float(_wr.rstrip("%")) if _wr not in ("N/A", "—") else None

    mc1, mc2, mc3, mc4, mc5 = st.columns(5)
    with mc1:
        st.markdown(_mc("Total Portfolio",
                        f"${_cv:,.2f}" if not df.empty else "—",
                        f"${_vd:+,.2f}" if not df.empty else "",
                        up=(_vd > 0) if not df.empty else None),
                    unsafe_allow_html=True)
    with mc2:
        st.markdown(_mc("Trades Hari Ini", str(trades_today(df))), unsafe_allow_html=True)
    with mc3:
        st.markdown(_mc("Win Rate", _wr,
                        up=(_wr_num >= 50) if _wr_num is not None else None),
                    unsafe_allow_html=True)
    with mc4:
        st.markdown(_mc("Drawdown", _dd_str,
                        up=(_dd_raw <= 0) if _dd_str != "N/A" else None),
                    unsafe_allow_html=True)
    with mc5:
        _la_fg = _ACT_FG.get(_la, "#94A3B8")
        st.markdown(_mc("Last Action", f'<span style="color:{_la_fg}">{_la}</span>'),
                    unsafe_allow_html=True)

    st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)

    # ── 4 market intelligence cards ───────────────────────────────────────────
    ci1, ci2, ci3, ci4 = st.columns(4)

    # HMM State
    _hmm_state = _hmm_conf = None
    if not df.empty and "market_state" in df.columns:
        _hmm_rows = df[df["market_state"].notna()]
        if not _hmm_rows.empty:
            _hmm_state = _hmm_rows.iloc[-1]["market_state"]
            _hmm_conf  = _hmm_rows.iloc[-1].get("hmm_confidence")
    with ci1:
        if _hmm_state:
            _hc   = HMM_COLORS.get(_hmm_state, "#64748B")
            _conf = f" {_hmm_conf*100:.0f}%" if _hmm_conf else ""
            _body = (_lbl("HMM State") + _bdg(f"{_hmm_state}{_conf}", _hc) +
                     '<div style="color:#4B5563;font-size:0.7rem;margin-top:8px">Multi-TF classification</div>')
        else:
            _body = _lbl("HMM State") + '<span style="color:#4B5563">No data</span>'
        st.markdown(_card(_body), unsafe_allow_html=True)

    # Ensemble Signal
    _ens_sig = _ens_score = _ens_cons = None
    if not df.empty:
        _lr = df.iloc[-1]
        _ens_sig   = _lr.get("ensemble_signal")    if "ensemble_signal"    in df.columns else None
        _ens_score = _lr.get("ensemble_score")     if "ensemble_score"     in df.columns else None
        _ens_cons  = _lr.get("ensemble_consensus") if "ensemble_consensus" in df.columns else None
    with ci2:
        if _ens_sig and pd.notna(_ens_sig):
            _ef = _ACT_FG.get(str(_ens_sig), "#94A3B8")
            _eb = _ACT_BG.get(str(_ens_sig), "#141720")
            _sc = f" · {float(_ens_score):+.2f}" if _ens_score is not None and pd.notna(_ens_score) else ""
            _cc = f" ({_ens_cons})" if _ens_cons and pd.notna(_ens_cons) else ""
            _body = (_lbl("Ensemble Signal") + _bdg(str(_ens_sig), _eb, _ef) +
                     f'<div style="color:#4B5563;font-size:0.7rem;margin-top:8px">Score{_sc}{_cc}</div>')
        else:
            _body = _lbl("Ensemble Signal") + '<span style="color:#4B5563">No data</span>'
        st.markdown(_card(_body), unsafe_allow_html=True)

    # Market Regime
    _reg = _reg_conf = None
    if not df.empty and "regime" in df.columns:
        _reg_rows = df[df["regime"].notna()]
        if not _reg_rows.empty:
            _reg      = _reg_rows.iloc[-1]["regime"]
            _reg_conf = _reg_rows.iloc[-1].get("regime_confidence")
    _REGIME_C = {"TRENDING_UP":"#10B981","TRENDING_DOWN":"#EF4444","SIDEWAYS":"#0EA5E9","VOLATILE":"#F59E0B"}
    _REGIME_H = {"TRENDING_UP":"Trend following","TRENDING_DOWN":"Proteksi modal",
                 "SIDEWAYS":"Mean reversion","VOLATILE":"Tunggu konfirmasi"}
    with ci3:
        if _reg:
            _rc    = _REGIME_C.get(_reg, "#64748B")
            _rconf = f" {_reg_conf:.0%}" if _reg_conf else ""
            _body  = (_lbl("Market Regime") +
                      _bdg(f"{_reg.replace('_',' ')}{_rconf}", _rc) +
                      f'<div style="color:#4B5563;font-size:0.7rem;margin-top:8px">{_REGIME_H.get(_reg,"")}</div>')
        else:
            _body = _lbl("Market Regime") + '<span style="color:#4B5563">No data</span>'
        st.markdown(_card(_body), unsafe_allow_html=True)

    # Fear & Greed
    fng_data = load_sentiment()
    with ci4:
        if fng_data:
            _fv  = fng_data["current_value"]
            _fl  = fng_data["current_label"]
            _fgc = ("#EF4444" if _fv < 25 else "#F97316" if _fv < 40 else
                    "#F59E0B" if _fv < 60 else "#10B981" if _fv < 75 else "#0EA5E9")
            _fd  = _fv - fng_data.get("yesterday_value", _fv)
            _body = (_lbl("Fear & Greed") + _bdg(f"{_fv} · {_fl}", _fgc) +
                     f'<div style="color:#4B5563;font-size:0.7rem;margin-top:8px">'
                     f'vs yesterday: {"+" if _fd >= 0 else ""}{_fd:.0f}</div>')
        else:
            _body = _lbl("Fear & Greed") + '<span style="color:#4B5563">Unavailable</span>'
        st.markdown(_card(_body), unsafe_allow_html=True)

    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

    # ── Multi-TF panel + charts + allocation ──────────────────────────────────
    chart_col, alloc_col = st.columns([3, 1])

    with chart_col:
        # TF badges
        _tf_states: dict[str, str] = {}
        if not df.empty:
            _lr2 = df.iloc[-1]
            for _tfc, _tfl in [("tf_15m_state","15m"),("tf_1h_state","1h"),("tf_4h_state","4h")]:
                if _tfc in df.columns and pd.notna(_lr2.get(_tfc)):
                    _tf_states[_tfl] = _lr2[_tfc]
        _tf_html = " ".join(
            _bdg(f"{tfl} · {tfs}", HMM_COLORS.get(tfs, "#64748B"))
            for tfl, tfs in _tf_states.items()
        ) or '<span style="color:#4B5563">No TF data</span>'
        st.markdown(_card(_lbl("Multi-Timeframe State") + _tf_html), unsafe_allow_html=True)
        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

        # Equity curve
        if not df.empty:
            _ec_live  = "#0EA5E9" if (_cv >= df["total_value"].iloc[0]) else "#EF4444"
            _ef_live  = "rgba(14,165,233,0.08)" if _ec_live == "#0EA5E9" else "rgba(239,68,68,0.08)"
            fig_eq = go.Figure()
            fig_eq.add_trace(go.Scatter(
                x=df["timestamp"], y=df["total_value"],
                mode="lines", name="Portfolio",
                line=dict(color=_ec_live, width=2),
                fill="tozeroy", fillcolor=_ef_live,
                hovertemplate="<b>%{x|%d %b %H:%M}</b><br>$%{y:,.2f}<extra></extra>",
            ))
            for _act, _ac, _sym in [("BUY","#10B981","triangle-up"),("SELL","#EF4444","triangle-down")]:
                _sub = df[df["action"] == _act]
                if not _sub.empty:
                    fig_eq.add_trace(go.Scatter(
                        x=_sub["timestamp"], y=_sub["total_value"],
                        mode="markers", name=_act,
                        marker=dict(color=_ac, size=9, symbol=_sym),
                        hovertemplate=f"<b>{_act}</b><br>%{{x|%d %b %H:%M}}<br>$%{{y:,.2f}}<extra></extra>",
                    ))
            _fig_base(fig_eq, 260, legend=True, yformat="$,.0f")
            st.plotly_chart(fig_eq, use_container_width=True)

            # Drawdown sparkline
            _vals = df["total_value"].tolist()
            _peak = _vals[0]
            _dds  = []
            for _v in _vals:
                if _v > _peak: _peak = _v
                _dds.append(-(_peak - _v) / _peak * 100 if _peak > 0 else 0)
            fig_dd = go.Figure(go.Scatter(
                x=df["timestamp"], y=_dds,
                mode="lines", fill="tozeroy",
                line=dict(color="#EF4444", width=1),
                fillcolor="rgba(239,68,68,0.15)",
                hovertemplate="<b>%{x|%d %b %H:%M}</b><br>%{y:.2f}%<extra></extra>",
                name="Drawdown",
            ))
            _fig_base(fig_dd, 110, yformat=".1f")
            st.plotly_chart(fig_dd, use_container_width=True)
        else:
            st.info("Belum ada data portofolio.")

    with alloc_col:
        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
        # Decision mix donut
        if not df.empty:
            _cnts = df["action"].value_counts().reset_index()
            _cnts.columns = ["action", "count"]
            fig_pie = go.Figure(go.Pie(
                labels=_cnts["action"], values=_cnts["count"],
                hole=0.55,
                marker=dict(
                    colors=[_ACT_FG.get(a, "#94A3B8") for a in _cnts["action"]],
                    line=dict(color="#0F1117", width=2),
                ),
                textinfo="label+percent",
                hovertemplate="<b>%{label}</b><br>%{value} (%{percent})<extra></extra>",
            ))
            _fig_base(fig_pie, 240, margin=dict(l=0,r=0,t=20,b=0))
            fig_pie.update_layout(
                showlegend=False,
                annotations=[dict(text="Mix", x=0.5, y=0.5, showarrow=False,
                                  font=dict(size=13, color="#64748B"))],
            )
            st.plotly_chart(fig_pie, use_container_width=True)
        else:
            st.info("No data")

        # Trailing stop card
        _open_pos = False
        _buys  = df[df["action"] == "BUY"]  if not df.empty else pd.DataFrame()
        _sells = df[df["action"] == "SELL"] if not df.empty else pd.DataFrame()
        if not _buys.empty:
            _lbts = _buys.iloc[-1]["timestamp"]
            _open_pos = _sells.empty or (_sells.iloc[-1]["timestamp"] < _lbts)
        if _open_pos:
            _ts_card = (_lbl("Trailing Stop") + _bdg("AKTIF", "#10B981") +
                        f'<div style="color:#4B5563;font-size:0.7rem;margin-top:8px">'
                        f'Entry: {_buys.iloc[-1]["timestamp"].strftime("%d %b %H:%M")}</div>')
        else:
            _ts_card = _lbl("Trailing Stop") + '<span style="color:#4B5563">No open position</span>'
        st.markdown(_card(_ts_card), unsafe_allow_html=True)

    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

    # ── Decision table ────────────────────────────────────────────────────────
    st.markdown(_lbl("Riwayat Keputusan — 20 Terakhir"), unsafe_allow_html=True)

    if not df.empty:
        _base = ["timestamp","action","confidence","size_pct","total_value",
                 "usdt_available","btc_held","reason"]
        _xcols = [c for c in ["market_state","hmm_confidence","confluence",
                               "tf_15m_state","tf_1h_state","tf_4h_state",
                               "claude_size_pct","ensemble_signal","ensemble_score",
                               "ensemble_consensus","regime","regime_confidence"]
                  if c in df.columns]
        _disp  = df[_base + _xcols].tail(20).iloc[::-1].copy()

        _disp["timestamp"]      = _disp["timestamp"].dt.strftime("%d %b %Y %H:%M")
        _disp["total_value"]    = _disp["total_value"].apply(lambda v: f"${v:,.2f}")
        _disp["usdt_available"] = _disp["usdt_available"].apply(lambda v: f"${v:,.2f}")
        _disp["btc_held"]       = _disp["btc_held"].apply(lambda v: f"{v:.6f}")
        _disp["size_pct"]       = _disp["size_pct"].apply(lambda v: f"{v:.0f}%")
        if "hmm_confidence"  in _disp.columns:
            _disp["hmm_confidence"]  = _disp["hmm_confidence"].apply(lambda v: f"{v*100:.0f}%" if pd.notna(v) else "—")
        if "claude_size_pct" in _disp.columns:
            _disp["claude_size_pct"] = _disp["claude_size_pct"].apply(lambda v: f"{v:.0f}%" if pd.notna(v) else "—")
        if "ensemble_score"  in _disp.columns:
            _disp["ensemble_score"]  = _disp["ensemble_score"].apply(lambda v: f"{float(v):+.3f}" if pd.notna(v) else "—")
        if "regime_confidence" in _disp.columns:
            _disp["regime_confidence"] = _disp["regime_confidence"].apply(lambda v: f"{float(v):.0%}" if pd.notna(v) else "—")
        for _fc in ["tf_15m_state","tf_1h_state","tf_4h_state","confluence"]:
            if _fc in _disp.columns:
                _disp[_fc] = _disp[_fc].fillna("—")

        _disp.rename(columns={
            "timestamp":"Waktu","action":"Aksi","confidence":"Conf","size_pct":"Size",
            "total_value":"Portfolio","usdt_available":"USDT","btc_held":BASE,"reason":"Alasan",
            "market_state":"HMM","hmm_confidence":"HMM%","confluence":"Konfl",
            "tf_15m_state":"15m","tf_1h_state":"1h","tf_4h_state":"4h",
            "claude_size_pct":"Claude%","ensemble_signal":"Signal",
            "ensemble_score":"Score","ensemble_consensus":"Konsensus",
            "regime":"Regime","regime_confidence":"Reg%",
        }, inplace=True)

        _raw = df[_base + _xcols].tail(20).iloc[::-1].copy()
        _raw["_ts"] = _raw["timestamp"].dt.strftime("%d %b %Y %H:%M")
        _act_lkp = dict(zip(_raw["_ts"], _raw["action"]))
        _hmm_lkp = dict(zip(_raw["_ts"], _raw["market_state"] if "market_state" in _raw.columns
                             else [""] * len(_raw)))
        _dcols = list(_disp.columns)

        def _style_row(row):
            _act  = _act_lkp.get(row["Waktu"], "HOLD")
            _st   = _hmm_lkp.get(row["Waktu"])
            _abg  = _ACT_BG.get(_act, "#141720")
            _afg  = _ACT_FG.get(_act, "#94A3B8")
            _sbg  = HMM_COLORS.get(_st, _abg) if _st else _abg
            stys  = [f"background:{_abg};color:#94A3B8"] * len(row)
            stys[0] = f"background:{_abg};color:#94A3B8;border-left:3px solid {_afg}"
            if "Aksi" in _dcols:
                stys[_dcols.index("Aksi")] = f"background:{_abg};color:{_afg};font-weight:700"
            if "HMM" in _dcols:
                stys[_dcols.index("HMM")] = f"background:{_sbg};color:#fff;font-weight:700"
            return stys

        st.dataframe(
            _disp.style.apply(_style_row, axis=1),
            use_container_width=True,
            height=min(40 + len(_disp) * 38, 760),
            column_config={
                "Alasan":  st.column_config.TextColumn(width="large"),
                "Waktu":   st.column_config.TextColumn(width="medium"),
                "HMM":     st.column_config.TextColumn(width="small"),
                "HMM%":    st.column_config.TextColumn(width="small"),
                "Konfl":   st.column_config.TextColumn(width="medium"),
                "15m":     st.column_config.TextColumn(width="small"),
                "1h":      st.column_config.TextColumn(width="small"),
                "4h":      st.column_config.TextColumn(width="small"),
                "Claude%": st.column_config.TextColumn(width="small"),
                "Signal":  st.column_config.TextColumn(width="small"),
                "Score":   st.column_config.TextColumn(width="small"),
                "Konsensus": st.column_config.TextColumn(width="small"),
                "Regime":  st.column_config.TextColumn(width="medium"),
                "Reg%":    st.column_config.TextColumn(width="small"),
            },
        )
    else:
        st.info("Belum ada keputusan. Jalankan bot terlebih dahulu.")

    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

    # ── Performance Monitor ───────────────────────────────────────────────────
    st.markdown(_lbl("Performance Monitor"), unsafe_allow_html=True)

    _pm      = perf_state.get("metrics", {})
    _pm_chk  = perf_state.get("last_check", "")
    _pm_adj  = perf_state.get("last_adjustment", "")
    _pm_msg  = perf_state.get("last_adjustment_reason", "—")
    _pm_auto = perf_state.get("auto_adjust_enabled", True)

    pm1, pm2, pm3 = st.columns([2, 1, 2])

    with pm1:
        _wr_pm = _pm.get("win_rate")
        if _wr_pm is not None:
            fig_gauge = go.Figure(go.Indicator(
                mode="gauge+number",
                value=_wr_pm,
                title={"text": "Win Rate 7 Hari", "font": {"size": 13, "color": "#64748B"}},
                number={"suffix": "%", "font": {"size": 22, "color": "#F1F5F9"}},
                gauge={
                    "axis": {"range": [0, 100], "tickcolor": "#4B5563"},
                    "bar":  {"color": "#10B981" if _wr_pm >= 50 else ("#F59E0B" if _wr_pm >= 40 else "#EF4444")},
                    "bgcolor": "#1E2130",
                    "steps": [
                        {"range": [0, 40],   "color": "#2D0F0F"},
                        {"range": [40, 65],  "color": "#2D2200"},
                        {"range": [65, 100], "color": "#0D2D1E"},
                    ],
                    "threshold": {"line": {"color": "#F59E0B", "width": 2}, "value": 50},
                },
            ))
            fig_gauge.update_layout(
                paper_bgcolor=_TP, plot_bgcolor=_TP,
                height=200, margin=dict(l=10, r=10, t=30, b=10),
            )
            st.plotly_chart(fig_gauge, use_container_width=True)
        else:
            st.info("Belum ada data metrik.")

    with pm2:
        _cl  = _pm.get("consecutive_losses", 0)
        _clc = "#EF4444" if _cl >= 3 else ("#F59E0B" if _cl >= 2 else "#10B981")
        _shr = _pm.get("sharpe")
        _shc = "#10B981" if (_shr and _shr > 1) else ("#F59E0B" if (_shr and _shr > 0) else "#EF4444")
        st.markdown(
            f'<div style="text-align:center;padding:16px 0">'
            f'<div style="font-size:0.68rem;font-weight:700;text-transform:uppercase;'
            f'letter-spacing:0.08em;color:#64748B;margin-bottom:4px">Consec. Losses</div>'
            f'<div style="font-size:2.8rem;font-weight:800;color:{_clc};line-height:1">{_cl}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
        if _shr is not None:
            st.markdown(
                f'<div style="text-align:center">'
                f'<div style="font-size:0.68rem;font-weight:700;text-transform:uppercase;'
                f'letter-spacing:0.08em;color:#64748B;margin-bottom:4px">Sharpe</div>'
                f'<div style="font-size:1.6rem;font-weight:700;color:{_shc}">{_shr:.3f}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
        st.caption(f"Trades 7d: {_pm.get('total_trades_7d', 0)}")

    with pm3:
        if _pm_chk:
            st.caption(f"Cek terakhir: {_pm_chk[:16].replace('T',' ')}")
        if _pm_adj:
            st.caption(f"Penyesuaian: {_pm_adj[:16].replace('T',' ')}")
        if _pm_msg and _pm_msg != "—":
            st.info(_pm_msg[:250], icon="⚙️")
        else:
            st.success("Tidak ada penyesuaian baru-baru ini.", icon="✅")

        def _on_toggle():
            _nv = st.session_state.get("auto_adj_toggle", True)
            try:
                _sp = Path("logs/perf_state.json")
                _ss = json.loads(_sp.read_text(encoding="utf-8")) if _sp.exists() else {}
                _ss["auto_adjust_enabled"] = _nv
                _sp.write_text(json.dumps(_ss, indent=2), encoding="utf-8")
            except Exception:
                pass

        st.toggle("Auto-Adjustment", value=_pm_auto, key="auto_adj_toggle",
                  on_change=_on_toggle,
                  help="Matikan untuk mencegah parameter diubah otomatis")

# ═══════════════════════════════════════════════════════ TAB: Chart ══════════

with tab_chart:
    import streamlit.components.v1 as _components

    # Convert pair format: "ETH/USDT" → "BINANCE:ETHUSD" for TradingView
    _tv_pair = PAIR.replace("/", "")
    _tv_symbol = f"BINANCE:{_tv_pair}"

    # Timeframe mapping ke TradingView interval
    _tf_env = os.getenv("TIMEFRAME_MEDIUM", os.getenv("TIMEFRAME", "1h"))
    _tv_interval_map = {
        "1m": "1", "3m": "3", "5m": "5", "15m": "15", "30m": "30",
        "1h": "60", "2h": "120", "4h": "240", "6h": "360", "12h": "720",
        "1d": "D", "1w": "W", "1M": "M",
    }
    _tv_interval = _tv_interval_map.get(_tf_env, "60")

    st.markdown(
        f"<div style='color:#94A3B8;font-size:0.85rem;margin-bottom:8px'>"
        f"Symbol: <b style='color:#F1F5F9'>{_tv_symbol}</b> &nbsp;|&nbsp; "
        f"Timeframe: <b style='color:#F1F5F9'>{_tf_env}</b> &nbsp;|&nbsp; "
        f"Data: TradingView (real-time)"
        f"</div>",
        unsafe_allow_html=True,
    )

    _tv_html = f"""
    <div id="tv_chart_container" style="height:620px;border-radius:8px;overflow:hidden;">
    <script type="text/javascript" src="https://s3.tradingview.com/tv.js"></script>
    <script type="text/javascript">
    new TradingView.widget({{
        "autosize": true,
        "symbol": "{_tv_symbol}",
        "interval": "{_tv_interval}",
        "timezone": "Asia/Jakarta",
        "theme": "dark",
        "style": "1",
        "locale": "id",
        "toolbar_bg": "#141720",
        "enable_publishing": false,
        "hide_side_toolbar": false,
        "allow_symbol_change": true,
        "save_image": true,
        "container_id": "tv_chart_container",
        "studies": [
            "RSI@tv-basicstudies",
            "MASimple@tv-basicstudies",
            "MACD@tv-basicstudies"
        ],
        "show_popup_button": true,
        "popup_width": "1000",
        "popup_height": "650"
    }});
    </script>
    </div>
    """
    _components.html(_tv_html, height=640, scrolling=False)

    st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
    st.caption(
        "Chart disediakan oleh TradingView. Indikator aktif: RSI, MA, MACD. "
        "Klik ikon fullscreen di pojok kanan chart untuk tampilan lebih besar."
    )

# ═══════════════════════════════════════════════════════ TAB: Backtesting ═════

with tab_bt:
    with st.expander("⚙️ Parameter Backtest", expanded=True):
        pc1, pc2, pc3, pc4, pc5 = st.columns(5)
        with pc1:
            bt_capital   = st.number_input("Modal Awal (USDT)", value=10_000, min_value=100, step=1000)
        with pc2:
            bt_pair      = st.selectbox("Pair", ["BTC/USDT","ETH/USDT","BNB/USDT"], index=0)
        with pc3:
            bt_timeframe = st.selectbox("Timeframe", ["15m","1h","4h","1d"], index=1)
        with pc4:
            bt_candles   = st.number_input("Jumlah Candle", value=500, min_value=100, max_value=1000, step=50)
        with pc5:
            bt_warmup    = st.number_input("Warmup HMM", value=200, min_value=50, max_value=400, step=25)

        if st.button("🚀 Jalankan Backtest", use_container_width=True, type="primary"):
            if bt_candles <= bt_warmup + 50:
                st.error(f"Jumlah candle ({bt_candles}) harus > warmup ({bt_warmup}) + 50")
            else:
                with st.spinner(f"Menjalankan backtest ({bt_candles} candles)…"):
                    _ok, _out = _run_backtest_subprocess(
                        bt_capital, bt_pair, bt_timeframe, bt_candles, bt_warmup)
                if _ok:
                    st.success("Backtest selesai!")
                    st.cache_data.clear()
                    st.rerun()
                else:
                    st.error("Backtest gagal.")
                    st.code(_out[:2000])

    bt_result = load_backtest_results()
    if bt_result is None:
        st.info("Belum ada hasil backtest. Jalankan backtest terlebih dahulu.")
    else:
        _bp  = bt_result.get("params", {})
        _bpe = bt_result.get("period", {})
        _bm  = bt_result.get("metrics", {})
        _bra = bt_result.get("run_at", "")[:19]

        st.caption(
            f"Hasil: {_bp.get('pair')} {_bp.get('timeframe')}  |  "
            f"{_bpe.get('start','')[:10]} → {_bpe.get('end','')[:10]}  |  Run: {_bra}"
        )

        # Metric cards row
        bm1, bm2, bm3, bm4, bm5, bm6 = st.columns(6)
        _ret  = _bm.get("total_return_pct", 0)
        _fcap = _bm.get("final_capital", _bp.get("initial_capital", 0))
        _icap = _bp.get("initial_capital", 0)
        _wbt  = _bm.get("win_rate_pct", 0)
        _dbt  = _bm.get("max_drawdown_pct", 0)
        _sbt  = _bm.get("sharpe_ratio", 0)
        _pfbt = _bm.get("profit_factor")

        with bm1:
            st.markdown(_mc("Total Return", f"{'+'if _ret>=0 else ''}{_ret:.2f}%",
                            f"${_fcap-_icap:+,.0f}", up=(_ret >= 0)),
                        unsafe_allow_html=True)
        with bm2:
            st.markdown(_mc("Win Rate", f"{_wbt:.1f}%",
                            f"{_bm.get('sell_trades',0)} sells", up=(_wbt >= 50)),
                        unsafe_allow_html=True)
        with bm3:
            st.markdown(_mc("Max Drawdown", f"-{_dbt:.2f}%", up=False),
                        unsafe_allow_html=True)
        with bm4:
            st.markdown(_mc("Sharpe Ratio", f"{_sbt:.3f}",
                            up=(_sbt >= 1) if _sbt else None),
                        unsafe_allow_html=True)
        with bm5:
            st.markdown(_mc("Profit Factor",
                            f"{_pfbt:.2f}" if _pfbt is not None else "∞",
                            up=(_pfbt >= 1.5) if _pfbt else None),
                        unsafe_allow_html=True)
        with bm6:
            st.markdown(_mc("Total Trades", str(_bm.get("total_trades", 0)),
                            f"fees ${_bm.get('total_fees_usdt',0):.2f}"),
                        unsafe_allow_html=True)

        st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)

        # Equity curve
        _eq_data = bt_result.get("equity_curve", [])
        _ts_data = bt_result.get("timestamps", [])
        if _eq_data and len(_eq_data) == len(_ts_data):
            _eq_df  = pd.DataFrame({"timestamp": pd.to_datetime(_ts_data), "equity": _eq_data})
            _init   = _bp.get("initial_capital", _eq_data[0])
            _ec_bt  = "#10B981" if _eq_data[-1] >= _init else "#EF4444"
            _ef_bt  = "rgba(16,185,129,0.08)" if _ec_bt == "#10B981" else "rgba(239,68,68,0.08)"

            _pk = _eq_data[0]
            _dds_bt = []
            for _v in _eq_data:
                if _v > _pk: _pk = _v
                _dds_bt.append(-(_pk - _v) / _pk * 100 if _pk > 0 else 0)

            fig_eq_bt = go.Figure()
            fig_eq_bt.add_trace(go.Scatter(
                x=_eq_df["timestamp"], y=_eq_df["equity"],
                mode="lines", name="Portfolio",
                line=dict(color=_ec_bt, width=1.8),
                fill="tozeroy", fillcolor=_ef_bt,
                hovertemplate="<b>%{x|%d %b %H:%M}</b><br>$%{y:,.2f}<extra></extra>",
            ))
            fig_eq_bt.add_hline(y=_init, line_dash="dash",
                                line_color="#F59E0B", line_width=0.8, opacity=0.7,
                                annotation_text=f"Initial ${_init:,.0f}",
                                annotation_font_color="#F59E0B")
            _trades_bt = bt_result.get("trades", [])
            for _a, _ac, _sym in [("BUY","#10B981","triangle-up"),("SELL","#EF4444","triangle-down")]:
                _t_ts = [pd.Timestamp(t["timestamp"]) for t in _trades_bt if t["action"] == _a]
                _t_eq = [_eq_df.iloc[(_eq_df["timestamp"] - _ts).abs().argmin()]["equity"]
                         for _ts in _t_ts]
                if _t_ts:
                    fig_eq_bt.add_trace(go.Scatter(
                        x=_t_ts, y=_t_eq, mode="markers", name=_a,
                        marker=dict(color=_ac, size=9, symbol=_sym),
                        hovertemplate=f"<b>{_a}</b><br>%{{x|%d %b %H:%M}}<br>$%{{y:,.2f}}<extra></extra>",
                    ))
            _fig_base(fig_eq_bt, 320, legend=True, yformat="$,.0f")
            st.plotly_chart(fig_eq_bt, use_container_width=True)

            fig_dd_bt = go.Figure(go.Scatter(
                x=_eq_df["timestamp"], y=_dds_bt,
                mode="lines", fill="tozeroy",
                line=dict(color="#EF4444", width=1),
                fillcolor="rgba(239,68,68,0.15)",
                hovertemplate="<b>%{x|%d %b %H:%M}</b><br>%{y:.2f}%<extra></extra>",
                name="Drawdown",
            ))
            _fig_base(fig_dd_bt, 140, yformat=".1f")
            st.plotly_chart(fig_dd_bt, use_container_width=True)

        # Trade table
        st.markdown("<div style='height:4px'></div>", unsafe_allow_html=True)
        st.markdown(_lbl("Riwayat Trade"), unsafe_allow_html=True)
        _tlist = bt_result.get("trades", [])
        if _tlist:
            _td = pd.DataFrame(_tlist)
            _td["timestamp"]   = pd.to_datetime(_td["timestamp"]).dt.strftime("%d %b %Y %H:%M")
            _td["price"]       = _td["price"].apply(lambda v: f"${v:,.2f}")
            _td["btc_amount"]  = _td["btc_amount"].apply(lambda v: f"{v:.6f}")
            _td["usdt_amount"] = _td["usdt_amount"].apply(lambda v: f"${v:,.2f}")
            _td["fee"]         = _td["fee"].apply(lambda v: f"${v:.4f}")
            _td["pnl"]         = _td["pnl"].apply(
                lambda v: f"${v:+.2f}" if v is not None and not (isinstance(v, float) and pd.isna(v)) else "—")
            _td.rename(columns={"timestamp":"Waktu","action":"Aksi","price":"Harga",
                                 "btc_amount":BASE,"usdt_amount":"USDT","fee":"Fee","pnl":"PnL"},
                       inplace=True)
            _td = _td[["Waktu","Aksi","Harga",BASE,"USDT","Fee","PnL"]].iloc[::-1].reset_index(drop=True)
            _raw_acts_bt = [t["action"] for t in reversed(_tlist)]
            _td_cols     = list(_td.columns)

            def _style_td(row):
                _idx  = row.name
                _a    = _raw_acts_bt[_idx] if _idx < len(_raw_acts_bt) else "BUY"
                _bg   = _ACT_BG.get(_a, "#141720")
                _fg   = _ACT_FG.get(_a, "#94A3B8")
                _stys = [f"background:{_bg};color:#94A3B8"] * len(row)
                _stys[0] = f"background:{_bg};color:#94A3B8;border-left:3px solid {_fg}"
                _stys[_td_cols.index("Aksi")] = f"background:{_bg};color:{_fg};font-weight:700"
                _pv = row.get("PnL", "—")
                if isinstance(_pv, str) and _pv.startswith("$+"):
                    _stys[_td_cols.index("PnL")] = f"background:{_bg};color:#10B981;font-weight:700"
                elif isinstance(_pv, str) and _pv.startswith("$-"):
                    _stys[_td_cols.index("PnL")] = f"background:{_bg};color:#EF4444"
                return _stys

            st.dataframe(
                _td.style.apply(_style_td, axis=1),
                use_container_width=True,
                height=min(40 + len(_td) * 38, 520),
            )
        else:
            st.info("Tidak ada trade yang dieksekusi.")

# ── Auto-refresh ──────────────────────────────────────────────────────────────

st.divider()
st.caption(f"Auto-refresh setiap {REFRESH_SECONDS} detik")
_cdown = st.empty()
for _rem in range(REFRESH_SECONDS, 0, -1):
    _cdown.caption(f"⏱ Refresh dalam {_rem} detik...")
    time.sleep(1)

st.cache_data.clear()
st.rerun()
