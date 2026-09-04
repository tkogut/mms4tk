"""
Mastermind Control Center — Streamlit Web UI (ui_dashboard)
Mobile-First PWA Dashboard for MMS v4 Trading System
"""

import os
import sys
import json
import sqlite3
import requests
import pandas as pd
import plotly.express as px
import plotly.graph_objects as bg
import streamlit as st
import streamlit.components.v1 as components
from datetime import datetime, timezone

# Ensure src/ is in python path for importing mms4tk modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

try:
    from mms4tk.logger_db import LoggerDB
    from mms4tk.okx_executor import OKXExecutor
except ImportError:
    LoggerDB = None
    OKXExecutor = None

# Configuration & Paths
DB_PATH = os.environ.get("MMS4_DB_PATH", ".agents/swarm/logs.db")
HERMES_KANBAN_PATH = os.environ.get("HERMES_KANBAN_PATH", "/app/hermes_data/kanban.db")
MMS4_API_BASE = os.environ.get("MMS4_API_BASE", "http://antigravity_core:8000")
MMS4_API_KEY = os.environ.get("MMS4_API_KEY", "")
ENV_FILE_PATH = os.environ.get("MMS4_ENV_PATH", ".env")

# Page Config
st.set_page_config(
    page_title="Mastermind Control Center",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# Load Custom CSS & PWA Meta Injections
def load_mobile_pwa_assets():
    st.markdown("""
        <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
        <meta name="theme-color" content="#0e1117">
        <meta name="apple-mobile-web-app-capable" content="yes">
        <meta name="mobile-web-app-capable" content="yes">
        <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
        <link rel="manifest" href="/app/static/manifest.json">
        <link rel="icon" type="image/png" href="/app/static/icon.png">
        <link rel="apple-touch-icon" href="/app/static/icon.png">

        <!-- Service Worker Registration -->
        <script>
        if ('serviceWorker' in navigator) {
            window.addEventListener('load', () => {
                navigator.serviceWorker.register('/app/static/sw.js')
                    .then(reg => console.log('[PWA] ServiceWorker registered:', reg.scope))
                    .catch(err => console.log('[PWA] ServiceWorker registration failed:', err));
            });
        }
        </script>
    """, unsafe_allow_html=True)
    """, unsafe_allow_html=True)

    css_path = os.path.join(os.path.dirname(__file__), "style.css")
    if os.path.exists(css_path):
        with open(css_path, "r", encoding="utf-8") as f:
            st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)

load_mobile_pwa_assets()


# Helper Data Functions
def fetch_api_status():
    """Fetch status from FastAPI backend."""
    try:
        r = requests.get(f"{MMS4_API_BASE}/api/v1/daemon/status", timeout=3)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None

def fetch_api_state():
    """Fetch state metrics from FastAPI backend."""
    try:
        r = requests.get(f"{MMS4_API_BASE}/api/v1/state", timeout=3)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None

def fetch_strategy_profile():
    """Fetch strategy profile from FastAPI backend."""
    try:
        r = requests.get(f"{MMS4_API_BASE}/api/v1/strategy/profile", timeout=3)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None

def fetch_okx_positions():
    """Fetch active positions directly or via backend."""
    if OKXExecutor:
        try:
            ex = OKXExecutor()
            import asyncio
            asyncio.run(ex.initialize())
            return asyncio.run(ex.fetch_positions())
        except Exception:
            pass
    return []

def fetch_kanban_cards():
    """Fetch pending undispatched Kanban cards / HITL events from logs.db or Hermes CLI."""
    cards = []
    # 1. Try Hermes CLI / JSON if available inside environment
    try:
        import subprocess, json
        res = subprocess.run(["hermes", "kanban", "list", "--json"], capture_output=True, text=True, timeout=2)
        if res.returncode == 0 and res.stdout.strip():
            kanban_data = json.loads(res.stdout)
            for k in kanban_data:
                status = str(k.get("status", "")).lower()
                if status not in ["done", "archived", "closed"]:
                    cards.append({
                        "id": k.get("id"),
                        "title": k.get("title"),
                        "body": k.get("description", k.get("title")),
                        "event_type": status.upper(),
                        "dispatched": 1,
                        "timestamp": k.get("created_at", "")
                    })
            if cards:
                return cards
    except Exception:
        pass

    # 2. Fallback to undispatched (pending) webhook_events in logs.db
    if os.path.exists(DB_PATH):
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            # Only fetch undispatched (pending) events! Events already sent & completed by Hermes are ignored.
            cursor.execute("SELECT * FROM webhook_events WHERE dispatched = 0 ORDER BY id DESC LIMIT 20")
            rows = cursor.fetchall()
            for r in rows:
                cards.append({
                    "id": r["id"],
                    "title": r["title"],
                    "body": r["body"],
                    "event_type": r["event_type"],
                    "dispatched": r["dispatched"],
                    "timestamp": r["timestamp"]
                })
            conn.close()
        except Exception:
            pass
    return cards

def fetch_kpi_metrics():
    """Fetch KPI metrics from LoggerDB or compute directly from closed_trades table."""
    if LoggerDB and os.path.exists(DB_PATH):
        try:
            db = LoggerDB(db_path=DB_PATH)
            return db.get_kpi_summary()
        except Exception:
            pass
    return {
        "total_trades": 0, "win_rate": 0.0, "win_rate_long": 0.0, "win_rate_short": 0.0,
        "profit_factor": 0.0, "max_drawdown": 0.0, "average_rr": 0.0, "total_pnl_usd": 0.0
    }

def fetch_closed_trades_df():
    """Fetch closed trades as DataFrame for Plotly charts."""
    if os.path.exists(DB_PATH):
        try:
            conn = sqlite3.connect(DB_PATH)
            df = pd.read_sql_query("SELECT * FROM closed_trades ORDER BY timestamp ASC", conn)
            conn.close()
            return df
        except Exception:
            pass
    return pd.DataFrame()


# Version Reader
APP_VERSION = "0.44.1"
try:
    from mms4tk import __version__ as mms_ver
    APP_VERSION = mms_ver
except Exception:
    pass

for v_path in [
    os.path.join(os.path.dirname(__file__), "..", "VERSION"),
    os.path.join(os.path.dirname(__file__), "VERSION"),
    "/app/VERSION"
]:
    if os.path.exists(v_path):
        try:
            with open(v_path, "r", encoding="utf-8") as vf:
                APP_VERSION = vf.read().strip()
                break
        except Exception:
            pass

# Navigation Title Header
st.markdown(f"""
    <div style="text-align: center; padding-bottom: 10px;">
        <h2 style="margin: 0; color: #f3f4f6;">🧠 Mastermind Control Center</h2>
        <p style="color: #9ca3af; margin: 0; font-size: 0.85rem;">VPS Autotrading Engine & Strategy Supervisor — <span style="background-color: #1f2937; color: #10b981; padding: 2px 8px; border-radius: 12px; font-weight: bold;">v{APP_VERSION}</span></p>
    </div>
""", unsafe_allow_html=True)

# Interactive PWA Install Card
components.html("""
<!DOCTYPE html>
<html>
<head>
    <style>
        body { margin: 0; padding: 0; font-family: system-ui, -apple-system, sans-serif; background: transparent; }
        .pwa-card {
            background: #1e293b;
            border: 1px solid #3b82f6;
            border-radius: 12px;
            padding: 12px 16px;
            color: #ffffff;
            display: flex;
            justify-content: space-between;
            align-items: center;
            box-shadow: 0 4px 12px rgba(0,0,0,0.3);
        }
        .pwa-text { flex-grow: 1; }
        .pwa-title { font-weight: bold; color: #60a5fa; font-size: 0.95rem; }
        .pwa-sub { font-size: 0.8rem; color: #94a3b8; margin-top: 2px; }
        .pwa-btn {
            background: #2563eb;
            color: white;
            border: none;
            padding: 10px 18px;
            border-radius: 8px;
            font-weight: 600;
            font-size: 0.85rem;
            cursor: pointer;
            white-space: nowrap;
            margin-left: 12px;
        }
        .pwa-btn:hover { background: #1d4ed8; }
        .instructions-box {
            display: none;
            margin-top: 10px;
            padding: 10px;
            background: #0f172a;
            border-radius: 8px;
            border: 1px solid #334155;
            font-size: 0.8rem;
            color: #cbd5e1;
        }
    </style>
</head>
<body>
    <div class="pwa-card">
        <div class="pwa-text">
            <div class="pwa-title">📲 Zainstaluj Mastermind App na Pulpicie</div>
            <div class="pwa-sub">Szybki dostęp z pulpitu telefonu i komputera</div>
        </div>
        <button class="pwa-btn" onclick="handleClick()">📲 Dodaj / Zainstaluj</button>
    </div>
    <div id="inst-box" class="instructions-box">
        <p style="margin:0 0 6px 0; font-weight:bold; color:#60a5fa;">💡 Jak dodać do ekranu głównego:</p>
        <ul style="margin:0; padding-left:16px;">
            <li><strong>Android (Chrome)</strong>: Menu <code>⋮</code> w prawym górnym rogu &rarr; <em>Dodaj do ekranu głównego / Zainstaluj aplikację</em></li>
            <li><strong>iPhone (Safari)</strong>: Przygotuj <code>📤 Udostępnij</code> na dole paska &rarr; <em>Dodaj do ekranu początkowego</em></li>
            <li><strong>Komputer (Chrome / Edge / Brave)</strong>: Ikona <code>⊕</code> na pasku adresu obok zakładek</li>
        </ul>
    </div>

    <script>
    let promptEvent = null;
    window.addEventListener('beforeinstallprompt', (e) => {
        e.preventDefault();
        promptEvent = e;
    });
    if (window.parent) {
        window.parent.addEventListener('beforeinstallprompt', (e) => {
            e.preventDefault();
            promptEvent = e;
        });
    }

    async function handleClick() {
        const box = document.getElementById('inst-box');
        if (promptEvent) {
            try {
                promptEvent.prompt();
                const { outcome } = await promptEvent.userChoice;
                promptEvent = null;
            } catch(e) {
                box.style.display = (box.style.display === 'block') ? 'none' : 'block';
            }
        } else {
            box.style.display = (box.style.display === 'block') ? 'none' : 'block';
        }
    }
    </script>
</body>
</html>
""", height=130)

# 4 Core Tabs
tab_monitoring, tab_analytics, tab_settings, tab_symbols = st.tabs([
    "📊 Monitoring", "📈 Analytics", "⚙️ Settings", "💱 Symbols"
])


# ==========================================
# TAB 1: 📊 MONITORING (Read-Only & Live)
# ==========================================
with tab_monitoring:
    st.subheader("System & Market Status")

    daemon_status = fetch_api_status()
    state_metrics = fetch_api_state()
    profile = fetch_strategy_profile()

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        current_state = state_metrics.get("current_state", "BASE_MODE") if state_metrics else "BASE_MODE"
        state_color = "🟢" if current_state == "BASE_MODE" else "🟠"
        st.metric(label="Operating Mode", value=f"{state_color} {current_state}")

    with col2:
        leverage = state_metrics.get("leverage", 1.0) if state_metrics else 1.0
        st.metric(label="Active Leverage", value=f"{leverage:.1f}x")

    with col3:
        bias = profile.get("directional_bias", "SHORT_ONLY") if profile else "SHORT_ONLY"
        st.metric(label="Directional Bias", value=bias)

    with col4:
        margin_cap = daemon_status.get("max_margin_usd", 10.0) if daemon_status else 10.0
        st.metric(label="Max Margin Cap", value=f"${margin_cap:.2f} USD")

    st.markdown("---")
    st.subheader("OKX Active Positions")

    positions = fetch_okx_positions()
    if positions:
        for pos in positions:
            sym = pos.get("symbol", "N/A")
            side = pos.get("side", "N/A").upper()
            contracts = pos.get("contracts", 0.0)
            entry_px = pos.get("entryPrice", 0.0)
            liq_px = pos.get("liquidationPrice", 0.0)
            upnl = pos.get("unrealizedPnl", 0.0)
            
            pnl_color = "#10b981" if upnl >= 0 else "#ef4444"
            side_badge = "🔴 SHORT" if side == "SHORT" else "🟢 LONG"

            st.markdown(f"""
                <div class="pos-card {'pos-card-short' if side == 'SHORT' else 'pos-card-long'}">
                    <div style="display: flex; justify-content: space-between; align-items: center;">
                        <h4 style="margin: 0; color: #ffffff;">{sym} ({side_badge})</h4>
                        <span style="font-weight: bold; color: {pnl_color}; font-size: 1.1rem;">uPnL: ${upnl:+.4f} USDC</span>
                    </div>
                    <div style="display: flex; justify-content: space-between; margin-top: 8px; font-size: 0.85rem; color: #9ca3af;">
                        <span>Contracts: <b>{contracts}</b></span>
                        <span>Entry: <b>${entry_px:,.2f}</b></span>
                        <span>Liq Price: <b>${liq_px:,.1f}</b></span>
                    </div>
                </div>
            """, unsafe_allow_html=True)
    else:
        st.info("No active positions on OKX Europe.")

    st.markdown("---")
    st.subheader("Hermes Kanban & HITL Approvals")

    cards = fetch_kanban_cards()
    if cards:
        for c in cards:
            st.markdown(f"""
                <div class="metric-card">
                    <div class="metric-title">{c['event_type']} — {c['timestamp'][:19]}</div>
                    <div style="font-weight: 600; color: #ffffff; margin-top: 4px;">{c['title']}</div>
                    <div style="color: #9ca3af; font-size: 0.85rem; margin-top: 2px;">{c['body']}</div>
                </div>
            """, unsafe_allow_html=True)
    else:
        st.caption("No pending Kanban cards or HITL events.")


# ==========================================
# TAB 2: 📈 ANALYTICS (KPI & Plotly Charts)
# ==========================================
with tab_analytics:
    st.subheader("Key Performance Indicators (KPI)")

    kpi = fetch_kpi_metrics()

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric(label="Win Rate (Overall)", value=f"{kpi['win_rate']}%", delta=f"L: {kpi['win_rate_long']}% | S: {kpi['win_rate_short']}%")
    with c2:
        st.metric(label="Profit Factor", value=f"{kpi['profit_factor']:.2f}", delta="Target: >1.0")
    with c3:
        st.metric(label="Max Drawdown (MDD)", value=f"${kpi['max_drawdown']:.2f}")
    with c4:
        st.metric(label="Average R:R", value=f"{kpi['average_rr']:.2f}")

    st.markdown("---")
    st.subheader("Performance Charts & Trend Analytics")

    df = fetch_closed_trades_df()
    if not df.empty and "pnl_usd" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        df = df.sort_values("timestamp", ascending=True).reset_index(drop=True)
        df["cum_pnl"] = df["pnl_usd"].cumsum()

        # 1. Time Range Selector Bar
        st.markdown("<div style='margin-bottom: 8px; font-weight: 600; color: #94a3b8; font-size: 0.85rem;'>⏱️ Timeframe Filter:</div>", unsafe_allow_html=True)
        time_range = st.radio(
            "Time Range Filter",
            ["4h", "1d", "7d", "30d", "3m", "6m", "1y", "5y", "ALL"],
            index=8,
            horizontal=True,
            label_visibility="collapsed"
        )

        # Filter DataFrame by selected time_range safely with UTC Timestamps
        now = pd.Timestamp.now(tz="UTC")
        filtered_df = df.copy()
        cutoff_dt = None

        if time_range == "4h":
            cutoff_dt = now - pd.Timedelta(hours=4)
        elif time_range == "1d":
            cutoff_dt = now - pd.Timedelta(days=1)
        elif time_range == "7d":
            cutoff_dt = now - pd.Timedelta(days=7)
        elif time_range == "30d":
            cutoff_dt = now - pd.Timedelta(days=30)
        elif time_range == "3m":
            cutoff_dt = now - pd.Timedelta(days=90)
        elif time_range == "6m":
            cutoff_dt = now - pd.Timedelta(days=180)
        elif time_range == "1y":
            cutoff_dt = now - pd.Timedelta(days=365)
        elif time_range == "5y":
            cutoff_dt = now - pd.Timedelta(days=1825)

        if cutoff_dt is not None:
            filtered_df = df[df["timestamp"] >= cutoff_dt]

        if filtered_df.empty:
            st.info(f"ℹ️ Brak zamkniętych pozycji w wybranym oknie czasowym ({time_range}). Wyświetlanie pełnej dostępnej historii.")
            filtered_df = df

        # Dynamic X-Axis Range for Plotly
        xaxis_config = dict(showgrid=True, gridcolor="#1e293b")
        if cutoff_dt is not None:
            xaxis_config["range"] = [cutoff_dt, now]

        # 1. Equity Curve Chart
        st.markdown("#### 📈 Cumulative PnL (Equity Curve)")
        fig_equity = bg.Figure()
        fig_equity.add_trace(bg.Scatter(
            x=filtered_df["timestamp"],
            y=filtered_df["cum_pnl"],
            mode="lines",
            name="Cumulative PnL ($)",
            line=dict(color="#10b981", width=3),
            fill="tozeroy",
            fillcolor="rgba(16, 185, 129, 0.12)"
        ))
        fig_equity.update_layout(
            template="plotly_dark",
            paper_bgcolor="#0f172a",
            plot_bgcolor="#0f172a",
            hovermode="x unified",
            dragmode=False,  # Prevents accidental touch drag while scrolling on mobile
            margin=dict(l=15, r=15, t=35, b=25),
            showlegend=False,
            modebar=dict(orientation="h", bgcolor="rgba(0,0,0,0)", color="#94a3b8", activecolor="#60a5fa"),
            xaxis=xaxis_config,
            yaxis=dict(showgrid=True, gridcolor="#1e293b", title="PnL (USD)")
        )
        st.plotly_chart(fig_equity, use_container_width=True, config={"displayModeBar": True, "displaylogo": False, "scrollZoom": False})

        # 2. Benchmark vs Buy & Hold Chart
        st.markdown("#### ⚖️ Mastermind Bot vs BTC Buy & Hold Benchmark")
        bench_df = filtered_df[filtered_df.get("entry_price", pd.Series([0])) > 0].copy() if "entry_price" in filtered_df.columns else pd.DataFrame()
        
        if not bench_df.empty:
            base_price = bench_df["entry_price"].iloc[0]
            bench_df["btc_benchmark"] = ((bench_df["entry_price"] / base_price) - 1.0) * 100
            bench_df["bot_return_pct"] = (bench_df["cum_pnl"] / 10.0) * 100

            fig_bench = bg.Figure()
            fig_bench.add_trace(bg.Scatter(
                x=bench_df["timestamp"],
                y=bench_df["bot_return_pct"],
                mode="lines",
                name="Mastermind Bot Return %",
                line=dict(color="#3b82f6", width=3),
                fill="tozeroy",
                fillcolor="rgba(59, 130, 246, 0.12)"
            ))
            fig_bench.add_trace(bg.Scatter(
                x=bench_df["timestamp"],
                y=bench_df["btc_benchmark"],
                mode="lines",
                name="BTC Buy & Hold %",
                line=dict(color="#f59e0b", width=2, dash="dash")
            ))
            fig_bench.update_layout(
                template="plotly_dark",
                paper_bgcolor="#0f172a",
                plot_bgcolor="#0f172a",
                hovermode="x unified",
                dragmode=False,  # Prevents accidental touch drag while scrolling on mobile
                margin=dict(l=15, r=15, t=45, b=25),
                modebar=dict(orientation="h", bgcolor="rgba(0,0,0,0)", color="#94a3b8", activecolor="#60a5fa"),
                legend=dict(
                    orientation="h",
                    yanchor="bottom",
                    y=1.02,
                    xanchor="left",
                    x=0,
                    bgcolor="rgba(0,0,0,0)",
                    font=dict(size=11, color="#94a3b8")
                ),
                xaxis=xaxis_config,
                yaxis=dict(showgrid=True, gridcolor="#1e293b", title="Return %")
            )
            st.plotly_chart(fig_bench, use_container_width=True, config={"displayModeBar": True, "displaylogo": False, "scrollZoom": False})
    else:
        st.info("Insufficient trade history in SQLite (logs.db) to render Plotly charts. The 15-min sync worker runs automatically in background.")


# ==========================================
# TAB 3: ⚙️ SETTINGS (.env Editor)
# ==========================================
with tab_settings:
    st.subheader("Safe Configuration Editor (.env)")
    st.caption("Modify operational settings safely. API keys are preserved automatically.")

    current_max_margin = float(os.getenv("MMS4_MAX_MARGIN_USD", "10.0"))
    current_log_level = os.getenv("MMS4_LOG_LEVEL", "INFO")
    current_sandbox = os.getenv("OKX_IS_SANDBOX", "false").lower() == "true"

    with st.form("env_settings_form"):
        new_max_margin = st.number_input("Max Total Margin Cap (USD)", min_value=1.0, max_value=5000.0, value=current_max_margin, step=5.0)
        new_log_level = st.selectbox("Daemon Log Level", ["DEBUG", "INFO", "WARNING", "ERROR"], index=["DEBUG", "INFO", "WARNING", "ERROR"].index(current_log_level))
        new_sandbox = st.checkbox("OKX Sandbox Mode", value=current_sandbox)

        st.markdown("**Masked API Keys (Read-Only):**")
        st.text_input("OKX API Key", value="*" * 12 + os.getenv("OKX_API_KEY", "")[-4:], disabled=True)
        st.text_input("MMS4 Secret API Key", value="*" * 12 + os.getenv("MMS4_API_KEY", "")[-4:], disabled=True)

        st.markdown("---")
        st.markdown("**🔐 Change Web UI BasicAuth Credentials:**")
        new_ui_username = st.text_input("Web UI Username", value="admin")
        new_ui_password = st.text_input("New Web UI Password (leave empty to keep unchanged)", type="password")

        submit_btn = st.form_submit_button("💾 Save Settings to .env")

    if submit_btn:
        try:
            # Safe atomic write to .env
            env_lines = []
            if os.path.exists(ENV_FILE_PATH):
                with open(ENV_FILE_PATH, "r", encoding="utf-8") as f:
                    env_lines = f.readlines()
            
            # Backup
            with open(f"{ENV_FILE_PATH}.bak", "w", encoding="utf-8") as f:
                f.writelines(env_lines)

            new_lines = []
            keys_updated = set()
            new_auth_line = None
            if new_ui_password.strip():
                import hashlib
                import base64
                sha_digest = hashlib.sha1(new_ui_password.strip().encode('utf-8')).digest()
                b64_sha = base64.b64encode(sha_digest).decode('ascii')
                new_auth_line = f"MMS4_UI_BASIC_AUTH={new_ui_username.strip()}:{{SHA}}{b64_sha}\n"

            for line in env_lines:
                if line.startswith("MMS4_MAX_MARGIN_USD="):
                    new_lines.append(f"MMS4_MAX_MARGIN_USD={new_max_margin}\n")
                    keys_updated.add("MMS4_MAX_MARGIN_USD")
                elif line.startswith("MMS4_TRADE_AMOUNT_USD="):
                    new_lines.append(f"MMS4_TRADE_AMOUNT_USD={new_max_margin}\n")
                    keys_updated.add("MMS4_TRADE_AMOUNT_USD")
                elif line.startswith("MMS4_LOG_LEVEL="):
                    new_lines.append(f"MMS4_LOG_LEVEL={new_log_level}\n")
                    keys_updated.add("MMS4_LOG_LEVEL")
                elif line.startswith("OKX_IS_SANDBOX="):
                    new_lines.append(f"OKX_IS_SANDBOX={'true' if new_sandbox else 'false'}\n")
                    keys_updated.add("OKX_IS_SANDBOX")
                elif line.startswith("MMS4_UI_BASIC_AUTH="):
                    if new_auth_line:
                        new_lines.append(new_auth_line)
                        keys_updated.add("MMS4_UI_BASIC_AUTH")
                    else:
                        new_lines.append(line)
                        keys_updated.add("MMS4_UI_BASIC_AUTH")
                else:
                    new_lines.append(line)

            if "MMS4_MAX_MARGIN_USD" not in keys_updated:
                new_lines.append(f"MMS4_MAX_MARGIN_USD={new_max_margin}\n")
            if "MMS4_TRADE_AMOUNT_USD" not in keys_updated:
                new_lines.append(f"MMS4_TRADE_AMOUNT_USD={new_max_margin}\n")
            if new_auth_line and "MMS4_UI_BASIC_AUTH" not in keys_updated:
                new_lines.append(new_auth_line)

            with open(ENV_FILE_PATH, "w", encoding="utf-8") as f:
                f.writelines(new_lines)

            if new_auth_line:
                st.success(f"Settings and NEW Web UI Password saved to {ENV_FILE_PATH}! (Traefik will use new credentials after container restart)")
            else:
                st.success(f"Settings saved to {ENV_FILE_PATH} successfully! Backup created at .env.bak")
        except Exception as err:
            st.error(f"Failed to save settings: {err}")


# ==========================================
# TAB 4: 💱 SYMBOLS MANAGER
# ==========================================
with tab_symbols:
    st.subheader("Active Trading Symbols Manager")

    active_symbol = os.getenv("MMS4_SYMBOL", "BTC/USDC")
    st.info(f"Primary Active Symbol: **{active_symbol}**")

    st.markdown("---")
    st.subheader("Discover & Validate OKX EEA X-Perp Instruments")

    test_symbol = st.text_input("Enter Market Symbol to Validate (e.g. ETH/USDC, SOL/USDC)", value="ETH/USDC")
    if st.button("🔍 Validate OKX Europe X-Perp Availability"):
        if OKXExecutor:
            try:
                ex = OKXExecutor()
                import asyncio
                asyncio.run(ex.initialize())
                resolved = ex.discover_eea_xperp_symbol(test_symbol)
                if resolved:
                    st.success(f"Instrument Available! CCXT Symbol: `{resolved}`")
                else:
                    st.warning(f"No active EEA X-Perp found for `{test_symbol}` on OKX Europe.")
            except Exception as ex_err:
                st.error(f"Error checking symbol: {ex_err}")
        else:
            st.error("OKXExecutor not loaded.")
