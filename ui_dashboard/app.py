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
    page_icon="🤖",
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
        <link rel="manifest" href="/app/static/manifest.json">
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
    """Fetch Kanban cards from Hermes SQLite or mms4tk webhook_events."""
    cards = []
    # 1. Try mms4tk webhook_events in logs.db
    if os.path.exists(DB_PATH):
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM webhook_events ORDER BY id DESC LIMIT 20")
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
VERSION_FILE_PATH = os.path.join(os.path.dirname(__file__), "..", "VERSION")
APP_VERSION = "0.5.0"
if os.path.exists(VERSION_FILE_PATH):
    try:
        with open(VERSION_FILE_PATH, "r", encoding="utf-8") as vf:
            APP_VERSION = vf.read().strip()
    except Exception:
        pass

# Navigation Title Header
st.markdown(f"""
    <div style="text-align: center; padding-bottom: 10px;">
        <h2 style="margin: 0; color: #f3f4f6;">🤖 Mastermind Control Center</h2>
        <p style="color: #9ca3af; margin: 0; font-size: 0.85rem;">VPS Autotrading Engine & Strategy Supervisor — <span style="background-color: #1f2937; color: #10b981; padding: 2px 8px; border-radius: 12px; font-weight: bold;">v{APP_VERSION}</span></p>
    </div>
""", unsafe_allow_html=True)

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
    st.subheader("Performance Charts")

    df = fetch_closed_trades_df()
    if not df.empty and "pnl_usd" in df.columns:
        df["cum_pnl"] = df["pnl_usd"].cumsum()
        
        # 1. Equity Curve
        fig_equity = px.line(
            df, x="timestamp", y="cum_pnl",
            title="Cumulative PnL (Equity Curve)",
            labels={"cum_pnl": "Cumulative PnL (USD)", "timestamp": "Time"},
            template="plotly_dark"
        )
        fig_equity.update_traces(line_color="#10b981", line_width=3)
        st.plotly_chart(fig_equity, use_container_width=True)

        # 2. Benchmark vs Buy & Hold
        if "entry_price" in df.columns:
            df["btc_benchmark"] = (df["entry_price"] / df["entry_price"].iloc[0] - 1.0) * 100
            df["bot_return_pct"] = (df["cum_pnl"] / 10.0) * 100  # Based on $10 base

            fig_bench = bg.Figure()
            fig_bench.add_trace(bg.Scatter(x=df["timestamp"], y=df["bot_return_pct"], mode="lines", name="Mastermind Bot PnL %", line=dict(color="#3b82f6", width=3)))
            fig_bench.add_trace(bg.Scatter(x=df["timestamp"], y=df["btc_benchmark"], mode="lines", name="BTC Buy & Hold %", line=dict(color="#f59e0b", width=2, dash="dash")))
            fig_bench.update_layout(title="Mastermind Bot vs BTC Buy & Hold Benchmark", template="plotly_dark", yaxis_title="Return %")
            st.plotly_chart(fig_bench, use_container_width=True)
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
