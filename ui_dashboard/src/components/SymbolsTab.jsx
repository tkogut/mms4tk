import React, { useState, useEffect, useRef } from 'react';
import { Layers, Plus, Trash2, Shield, Activity, Power, CheckCircle, PauseCircle, AlertTriangle, Search, Check, X, ShieldAlert } from 'lucide-react';

export function SymbolsTab({ apiKey }) {
  const [symbols, setSymbols] = useState([
    { symbol: 'BTC/USDC', state: 'BASE_MODE', leverage: 3.0, is_active: true, okx_verified: true, okx_status: 'VERIFIED', okx_price: 64250.0 },
    { symbol: 'ETH/USDC', state: 'BASE_MODE', leverage: 3.0, is_active: true, okx_verified: true, okx_status: 'VERIFIED', okx_price: 3450.0 },
    { symbol: 'SOL/USDC', state: 'BASE_MODE', leverage: 3.0, is_active: true, okx_verified: true, okx_status: 'VERIFIED', okx_price: 145.0 }
  ]);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [newSymbolInput, setNewSymbolInput] = useState('');
  const [verifyResult, setVerifyResult] = useState(null);
  const [verifying, setVerifying] = useState(false);
  const [confirmDeleteTarget, setConfirmDeleteTarget] = useState(null);
  const [msg, setMsg] = useState('');
  const [loading, setLoading] = useState(false);

  const fetchSymbols = async () => {
    try {
      const res = await fetch('/api/v1/symbols');
      if (res.ok) {
        const data = await res.json();
        if (data.symbols && Array.isArray(data.symbols)) {
          setSymbols(data.symbols);
        }
      }
    } catch (err) {
      console.warn('[SymbolsTab] Error fetching symbols:', err);
    }
  };

  useEffect(() => {
    fetchSymbols();
    const interval = setInterval(fetchSymbols, 10000);
    return () => clearInterval(interval);
  }, []);

  const handleLiveVerify = async () => {
    let raw = newSymbolInput.trim().toUpperCase();
    if (!raw) return;
    if (!raw.includes('/')) {
      raw = raw.endsWith('USDC') ? raw.replace('USDC', '/USDC') : raw + '/USDC';
    }
    setVerifying(true);
    setVerifyResult(null);
    try {
      const res = await fetch('/api/v1/symbols/verify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol: raw })
      });
      if (res.ok) {
        const data = await res.json();
        setVerifyResult(data);
      }
    } catch (err) {
      setVerifyResult({ okx_verified: false, okx_status: 'UNSUPPORTED', last_price: 0.0 });
    } finally {
      setVerifying(false);
    }
  };

  const handleAddSymbol = async (e) => {
    e.preventDefault();
    let raw = newSymbolInput.trim().toUpperCase();
    if (!raw) return;
    if (!raw.includes('/')) {
      raw = raw.endsWith('USDC') ? raw.replace('USDC', '/USDC') : raw + '/USDC';
    }
    setLoading(true);
    try {
      const currentKey = apiKey || localStorage.getItem('mms4_api_key') || 'test-api-auth-key-unit-test';
      const res = await fetch('/api/v1/symbols/add', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-MMS4-API-KEY': currentKey
        },
        body: JSON.stringify({ symbol: raw, is_active: true })
      });
      if (res.ok) {
        const data = await res.json();
        const priceStr = data.okx_price ? ` ($${Number(data.okx_price).toLocaleString()})` : '';
        setMsg(`✓ Para ${raw} zweryfikowana na OKX API i aktywowana!${priceStr}`);
        setNewSymbolInput('');
        setVerifyResult(null);
        setIsModalOpen(false);
        fetchSymbols();
      } else {
        const errData = await res.json().catch(() => ({}));
        setMsg(`❌ Błąd dodawania pary: ${errData.detail || 'Operacja odrzucona'}`);
      }
    } catch (err) {
      setMsg('❌ Błąd połączenia z API');
    } finally {
      setLoading(false);
      setTimeout(() => setMsg(''), 5000);
    }
  };

  const handleToggleSymbol = async (symObj) => {
    const currentKey = apiKey || localStorage.getItem('mms4_api_key') || 'test-api-auth-key-unit-test';
    const newActiveState = symObj.is_active === false; // Toggle
    try {
      const res = await fetch('/api/v1/symbols/update', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-MMS4-API-KEY': currentKey
        },
        body: JSON.stringify({
          symbol: symObj.symbol,
          is_active: newActiveState,
          allocation_usd: parseFloat(symObj.allocation_usd || 10.0),
          base_ratio: parseInt(symObj.base_ratio || 70),
          add_ratio: parseInt(symObj.add_ratio || 30),
          timeframe: symObj.timeframe || '15m'
        })
      });
      if (res.ok) {
        setMsg(newActiveState ? `▶️ Para ${symObj.symbol} została AKTYWOWANA!` : `⏸️ Para ${symObj.symbol} została WSTRZYMANA.`);
        fetchSymbols();
      } else {
        setMsg('❌ Błąd aktualizacji stanu pary');
      }
    } catch (err) {
      setMsg('❌ Błąd połączenia z API');
    } finally {
      setTimeout(() => setMsg(''), 4000);
    }
  };

  const handleDeleteSymbol = async (symbolStr) => {
    const currentKey = apiKey || localStorage.getItem('mms4_api_key') || 'test-api-auth-key-unit-test';
    try {
      const res = await fetch(`/api/v1/symbols/${encodeURIComponent(symbolStr)}`, {
        method: 'DELETE',
        headers: { 'X-MMS4-API-KEY': currentKey }
      });
      if (res.ok) {
        setMsg(`🗑️ Para ${symbolStr} została trwale usunięta z menedżera.`);
        setConfirmDeleteTarget(null);
        fetchSymbols();
      } else {
        const errData = await res.json().catch(() => ({}));
        setMsg(`❌ Błąd usuwania pary: ${errData.detail || 'Operacja odrzucona'}`);
      }
    } catch (err) {
      setMsg('❌ Błąd połączenia z API');
    } finally {
      setTimeout(() => setMsg(''), 5000);
    }
  };

  const handleUpdateSymbolSettings = async (symObj, newAlloc, newBaseRatio, newTimeframe, staleConfig = {}, trendFilterConfig = {}) => {
    const currentKey = apiKey || localStorage.getItem('mms4_api_key') || 'test-api-auth-key-unit-test';
    try {
      const res = await fetch('/api/v1/symbols/update', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-MMS4-API-KEY': currentKey
        },
        body: JSON.stringify({
          symbol: symObj.symbol,
          is_active: symObj.is_active !== false,
          allocation_usd: parseFloat(newAlloc),
          base_ratio: parseInt(newBaseRatio),
          add_ratio: 100 - parseInt(newBaseRatio),
          timeframe: newTimeframe || '15m',
          stale_candles_threshold: parseInt(staleConfig.stale_candles_threshold ?? 12),
          stale_atr_threshold: parseFloat(staleConfig.stale_atr_threshold ?? 0.5),
          impulse_negation_enabled: staleConfig.impulse_negation_enabled !== false,
          // PLAN-010 Phase 3: pass through the trend-filter fields untouched unless the
          // caller explicitly supplies new ones (SymbolCard's own toggle, or the
          // recalibration modal's "Zastosuj nowe parametry") — never silently reset
          // lookback/threshold to their schema defaults on an unrelated settings save.
          tma_trend_filter_enabled: trendFilterConfig.tma_trend_filter_enabled ?? symObj.tma_trend_filter_enabled ?? false,
          tma_slope_lookback: trendFilterConfig.tma_slope_lookback ?? symObj.tma_slope_lookback ?? 5,
          tma_slope_threshold: trendFilterConfig.tma_slope_threshold ?? symObj.tma_slope_threshold ?? 0.002,
        })
      });
      if (res.ok) {
        setMsg(`✓ Zapisano ustawienia dla ${symObj.symbol} ($${newAlloc} USD, Base/Add: ${newBaseRatio}%/${100 - newBaseRatio}%, Interwał: ${newTimeframe || '15m'})`);
        fetchSymbols();
        return true;
      }
      return false;
    } catch (err) {
      setMsg('❌ Błąd zapisu ustawień pary');
      return false;
    } finally {
      setTimeout(() => setMsg(''), 5000);
    }
  };

  return (
    <div className="space-y-6">
      {/* Page Header */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 bg-slate-900 border border-slate-800 rounded-xl p-5 shadow-lg">
        <div>
          <h2 className="font-extrabold text-xl text-gray-100 flex items-center gap-2">
            <Layers className="w-6 h-6 text-blue-400" />
            <span>Menedżer Par Rynkowych & Alokacji (Multi-Symbol Grid)</span>
          </h2>
          <p className="text-xs text-slate-400 mt-1 font-mono">
            Zarządzaj aktywnymi parami, indywidualną alokacją ($ USD), proporcjami Base/Addon oraz interwałami świec.
          </p>
        </div>

        <button
          onClick={() => setIsModalOpen(true)}
          className="flex items-center gap-2 px-4 py-2.5 bg-blue-600 hover:bg-blue-500 text-white rounded-lg text-xs font-semibold transition-colors shadow-md shadow-blue-500/20"
        >
          <Plus className="w-4 h-4" />
          <span>Dodaj Nową Parę</span>
        </button>
      </div>

      {msg && (
        <div className="p-3 bg-blue-950/80 border border-blue-500/40 rounded-xl text-xs text-blue-300 font-mono flex items-center gap-2 shadow-sm">
          <Activity className="w-4 h-4 text-blue-400" />
          <span>{msg}</span>
        </div>
      )}

      {/* Confirmation Modal for Deleting a Symbol */}
      {confirmDeleteTarget && (
        <div className="fixed inset-0 bg-slate-950/80 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-slate-900 border border-slate-700 rounded-2xl p-6 max-w-md w-full shadow-2xl space-y-5 animate-in fade-in zoom-in-95 duration-150">
            <div className="flex items-center gap-3 border-b border-slate-800 pb-4">
              <div className="p-3 bg-red-950/80 border border-red-500/30 rounded-xl text-red-400">
                <ShieldAlert className="w-6 h-6" />
              </div>
              <div>
                <h3 className="font-bold text-gray-100 text-lg">Potwierdź Usunięcie Pary</h3>
                <p className="text-xs text-slate-400">Trwałe usunięcie konfiguracji symbolu</p>
              </div>
            </div>

            <div className="bg-slate-950 p-4 rounded-xl border border-slate-800 text-xs font-mono space-y-2">
              <div className="flex justify-between items-center py-1">
                <span className="text-slate-400">Symbol do usunięcia:</span>
                <span className="font-bold text-red-400 text-sm">{confirmDeleteTarget.symbol}</span>
              </div>
              <div className="flex justify-between items-center py-1 border-t border-slate-800/60 pt-2">
                <span className="text-slate-400">Alokacja:</span>
                <span className="text-slate-200 font-bold">${confirmDeleteTarget.allocation_usd} USD</span>
              </div>
            </div>

            <p className="text-xs text-slate-300 bg-red-950/30 border border-red-500/30 p-3 rounded-lg leading-relaxed">
              ⚠️ <strong>Uwaga:</strong> Usunięcie pary wykasuje jej konfigurację z menedżera oraz z pliku <code>symbols.json</code>. Bot nie będzie więcej monitorować tego rynku.
            </p>

            <div className="flex items-center gap-3 pt-2">
              <button
                onClick={() => setConfirmDeleteTarget(null)}
                className="w-1/2 py-2.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-xl font-bold text-xs transition-colors"
              >
                Anuluj
              </button>
              <button
                onClick={() => handleDeleteSymbol(confirmDeleteTarget.symbol)}
                className="w-1/2 py-2.5 bg-red-600 hover:bg-red-500 text-white rounded-xl font-bold text-xs transition-colors flex items-center justify-center gap-1.5 shadow-lg shadow-red-600/30"
              >
                <Trash2 className="w-4 h-4" />
                <span>Trwale Usuń Parę</span>
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Grid of Symbol Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
        {symbols.map((s) => {
          const isAutotradeReady = s.autotrade_permitted !== false && s.okx_verified !== false && s.is_active !== false;
          return (
            <SymbolCard
              key={s.symbol}
              symbolObj={s}
              isAutotradeReady={isAutotradeReady}
              onToggle={handleToggleSymbol}
              onRequestDelete={(target) => setConfirmDeleteTarget(target)}
              onSaveSettings={handleUpdateSymbolSettings}
              apiKey={apiKey}
            />
          );
        })}
      </div>

      {/* Add Symbol Modal with OKX Verification & Allocation Setup */}
      {isModalOpen && (
        <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-slate-900 border border-slate-800 rounded-xl p-6 w-full max-w-md shadow-2xl space-y-4">
            <div className="flex items-center justify-between pb-3 border-b border-slate-800">
              <h3 className="font-bold text-gray-100 text-base flex items-center gap-2">
                <Plus className="w-5 h-5 text-blue-400" />
                <span>Dodaj Nową Parę z Weryfikacją OKX API</span>
              </h3>
              <button
                onClick={() => {
                  setIsModalOpen(false);
                  setVerifyResult(null);
                }}
                className="text-slate-400 hover:text-slate-200 text-sm font-bold"
              >
                ✕
              </button>
            </div>

            <form onSubmit={handleAddSymbol} className="space-y-4">
              <div>
                <label className="block text-xs font-semibold text-slate-300 mb-1.5">
                  Nazwa Symbolu (OKX Ticker):
                </label>
                <div className="flex gap-2">
                  <input
                    type="text"
                    placeholder="np. ETH/USDC, SOL/USDC, AVAX/USDC..."
                    value={newSymbolInput}
                    onChange={(e) => {
                      setNewSymbolInput(e.target.value);
                      setVerifyResult(null);
                    }}
                    className="flex-1 px-3 py-2 bg-slate-950 border border-slate-800 rounded-lg text-sm text-slate-200 focus:outline-none focus:border-blue-500 font-mono"
                    required
                  />
                  <button
                    type="button"
                    onClick={handleLiveVerify}
                    disabled={verifying || !newSymbolInput.trim()}
                    className="px-3 py-2 bg-slate-800 hover:bg-slate-700 text-blue-400 rounded-lg text-xs font-semibold border border-slate-700 flex items-center gap-1.5"
                  >
                    <Search className="w-3.5 h-3.5" />
                    <span>{verifying ? '...' : 'Sprawdź OKX'}</span>
                  </button>
                </div>
              </div>

              {verifyResult && (
                <div className={`p-3 rounded-lg border text-xs font-mono space-y-1 ${
                  verifyResult.okx_verified !== false
                    ? 'bg-emerald-950/60 border-emerald-500/40 text-emerald-300'
                    : 'bg-red-950/60 border-red-500/40 text-red-300'
                }`}>
                  <div className="flex items-center gap-1.5 font-bold">
                    {verifyResult.okx_verified !== false ? <Check className="w-4 h-4 text-emerald-400" /> : <X className="w-4 h-4 text-red-400" />}
                    <span>OKX Status: {verifyResult.okx_status || 'VERIFIED'}</span>
                  </div>
                  {verifyResult.last_price > 0 && (
                    <div className="text-[11px] text-slate-300">
                      Aktualna Cena Rynkowa: <span className="font-bold text-gray-100">${Number(verifyResult.last_price).toLocaleString()}</span>
                    </div>
                  )}
                </div>
              )}

              <div className="flex justify-end gap-3 pt-3 border-t border-slate-800">
                <button
                  type="button"
                  onClick={() => setIsModalOpen(false)}
                  className="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg text-xs font-semibold transition-colors"
                >
                  Anuluj
                </button>
                <button
                  type="submit"
                  disabled={loading}
                  className="px-5 py-2 bg-blue-600 hover:bg-blue-500 text-white rounded-lg text-xs font-bold transition-colors"
                >
                  {loading ? 'Dodawanie...' : 'Aktywuj Parę'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}

function SymbolCard({ symbolObj, isAutotradeReady, onToggle, onRequestDelete, onSaveSettings, apiKey }) {
  const [allocUsd, setAllocUsd] = useState(symbolObj.allocation_usd || 10.0);
  const [baseRatio, setBaseRatio] = useState(symbolObj.base_ratio || 70);
  const [timeframe, setTimeframe] = useState(symbolObj.timeframe || '15m');
  const [staleCandles, setStaleCandles] = useState(symbolObj.stale_candles_threshold ?? 12);
  const [staleAtr, setStaleAtr] = useState(symbolObj.stale_atr_threshold ?? 0.5);
  const [impulseNeg, setImpulseNeg] = useState(symbolObj.impulse_negation_enabled !== false);
  const [trendFilterEnabled, setTrendFilterEnabled] = useState(symbolObj.tma_trend_filter_enabled === true);

  useEffect(() => {
    setTrendFilterEnabled(symbolObj.tma_trend_filter_enabled === true);
  }, [symbolObj.tma_trend_filter_enabled]);

  useEffect(() => {
    setAllocUsd(symbolObj.allocation_usd || 10.0);
    setBaseRatio(symbolObj.base_ratio || 70);
    setTimeframe(symbolObj.timeframe || '15m');
    setStaleCandles(symbolObj.stale_candles_threshold ?? 12);
    setStaleAtr(symbolObj.stale_atr_threshold ?? 0.5);
    setImpulseNeg(symbolObj.impulse_negation_enabled !== false);
  }, [symbolObj]);

  const addRatio = 100 - baseRatio;

  return (
    <div
      className={`border rounded-xl p-5 shadow-lg space-y-4 transition-all ${
        symbolObj.is_active !== false
          ? 'bg-slate-900 border-slate-800'
          : 'bg-slate-950/60 border-slate-900 opacity-70'
      }`}
    >
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="relative flex items-center justify-center">
            <span
              className={`w-3.5 h-3.5 rounded-full shadow-md ${
                isAutotradeReady
                  ? 'bg-emerald-500 animate-pulse shadow-emerald-500/60'
                  : 'bg-red-500 shadow-red-500/60'
              }`}
            ></span>
          </div>

          <div>
            <h4 className="font-extrabold text-base text-gray-100 font-mono flex items-center gap-2">
              <span>{symbolObj.symbol}</span>
            </h4>
            <span className="text-[10px] text-slate-400 block font-mono">
              OKX Spot / Perpetuals
            </span>
          </div>
        </div>

        <button
          onClick={() => onToggle(symbolObj)}
          className={`flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-bold font-mono transition-all border ${
            symbolObj.is_active !== false
              ? 'bg-emerald-950 text-emerald-300 border-emerald-500/40 hover:bg-emerald-900'
              : 'bg-amber-950 text-amber-300 border-amber-500/40 hover:bg-amber-900'
          }`}
        >
          <Power className="w-3.5 h-3.5" />
          <span>{symbolObj.is_active !== false ? 'AKTYWNA' : 'WSTRZYMANA'}</span>
        </button>
      </div>

      <div
        className={`p-2.5 rounded-lg border text-[11px] font-mono space-y-1 ${
          isAutotradeReady
            ? 'bg-emerald-950/40 border-emerald-500/30 text-emerald-300'
            : 'bg-red-950/40 border-red-500/30 text-red-300'
        }`}
      >
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 font-bold">
            <span
              className={`w-2 h-2 rounded-full ${
                isAutotradeReady ? 'bg-emerald-400' : 'bg-red-400'
              }`}
            ></span>
            <span>
              {isAutotradeReady
                ? '🟢 HANDEL AUTOMATYCZNY: GOTOWY'
                : '🔴 HANDEL AUTOMATYCZNY: BLOKOWANY / WSTRZYMANY'}
            </span>
          </div>
          {symbolObj.okx_price > 0 && (
            <span className="font-bold text-gray-200">
              ${Number(symbolObj.okx_price).toLocaleString()}
            </span>
          )}
        </div>
      </div>

      {/* Per-Symbol Allocation, Base/Add Ratio & Timeframe Controls */}
      <div className="space-y-3 pt-1 border-t border-slate-800/80">
        <div className="grid grid-cols-3 gap-2 text-xs">
          <div>
            <label className="text-[10px] text-slate-400 block mb-1 font-mono">
              Alokacja ($):
            </label>
            <div className="relative">
              <span className="absolute left-2 top-1.5 text-slate-400 text-xs">$</span>
              <input
                type="number"
                step="1"
                min="1"
                value={allocUsd}
                onChange={(e) => setAllocUsd(e.target.value)}
                className="w-full pl-5 pr-1 py-1.5 bg-slate-950 border border-slate-800 rounded-lg text-xs font-mono font-bold text-emerald-400 focus:outline-none focus:border-blue-500"
              />
            </div>
          </div>

          <div>
            <label className="text-[10px] text-slate-400 block mb-1 font-mono">
              Base/Add (%):
            </label>
            <div className="flex items-center gap-1">
              <input
                type="number"
                step="5"
                min="10"
                max="90"
                value={baseRatio}
                onChange={(e) => setBaseRatio(Math.max(0, Math.min(100, parseInt(e.target.value) || 0)))}
                className="w-12 px-1 py-1.5 bg-slate-950 border border-slate-800 rounded-lg text-xs font-mono font-bold text-blue-400 focus:outline-none focus:border-blue-500 text-center"
              />
              <span className="text-slate-500 text-[10px] font-mono">/{addRatio}%</span>
            </div>
          </div>

          <div>
            <label className="text-[10px] text-slate-400 block mb-1 font-mono">
              Interwał:
            </label>
            <select
              value={timeframe}
              onChange={(e) => setTimeframe(e.target.value)}
              className="w-full py-1.5 px-1 bg-slate-950 border border-slate-800 rounded-lg text-xs font-mono font-bold text-amber-400 focus:outline-none focus:border-blue-500"
            >
              <option value="15m">15m ⭐</option>
              <option value="30m">30m</option>
              <option value="1h">1h ⭐</option>
              <option value="5m">5m</option>
              <option value="1m">1m</option>
            </select>
          </div>
        </div>

        {/* Stale Position Protection Config */}
        <div className="pt-2 border-t border-slate-800/50">
          <div className="flex items-center gap-2 mb-2">
            <Shield className="w-3.5 h-3.5 text-amber-400" />
            <span className="text-[10px] font-bold text-amber-400 font-mono tracking-wide">
              OCHRONA STAGNACJI (MMS4)
            </span>
          </div>
          <div className="grid grid-cols-3 gap-2 text-xs">
            <div>
              <label className="text-[10px] text-slate-400 block mb-1 font-mono">
                Świece max:
              </label>
              <input
                type="number"
                step="1"
                min="4"
                max="48"
                value={staleCandles}
                onChange={(e) => setStaleCandles(parseInt(e.target.value) || 12)}
                className="w-full px-2 py-1.5 bg-slate-950 border border-slate-800 rounded-lg text-xs font-mono font-bold text-amber-400 focus:outline-none focus:border-amber-500 text-center"
              />
            </div>
            <div>
              <label className="text-[10px] text-slate-400 block mb-1 font-mono">
                Próg ATR:
              </label>
              <input
                type="number"
                step="0.1"
                min="0.1"
                max="2.0"
                value={staleAtr}
                onChange={(e) => setStaleAtr(parseFloat(e.target.value) || 0.5)}
                className="w-full px-2 py-1.5 bg-slate-950 border border-slate-800 rounded-lg text-xs font-mono font-bold text-amber-400 focus:outline-none focus:border-amber-500 text-center"
              />
            </div>
            <div>
              <label className="text-[10px] text-slate-400 block mb-1 font-mono">
                Negacja 50%:
              </label>
              <button
                type="button"
                onClick={() => setImpulseNeg(!impulseNeg)}
                className={`w-full py-1.5 px-2 rounded-lg text-xs font-mono font-bold border transition-colors ${
                  impulseNeg
                    ? 'bg-emerald-950 text-emerald-300 border-emerald-500/40'
                    : 'bg-slate-800 text-slate-500 border-slate-700'
                }`}
              >
                {impulseNeg ? '✓ WŁ' : '✕ WYŁ'}
              </button>
            </div>
          </div>
        </div>

        {/* Action Buttons: Save Settings & Delete Pair */}
        <div className="flex items-center gap-2 pt-1">
          <button
            onClick={() => onSaveSettings(symbolObj, allocUsd, baseRatio, timeframe, {
              stale_candles_threshold: staleCandles,
              stale_atr_threshold: staleAtr,
              impulse_negation_enabled: impulseNeg
            }, {
              tma_trend_filter_enabled: trendFilterEnabled,
            })}
            className="flex-1 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-lg text-xs font-semibold font-mono border border-slate-700 transition-colors flex items-center justify-center gap-1.5"
          >
            <Check className="w-3.5 h-3.5 text-emerald-400" />
            <span>Zapisz Ustawienia</span>
          </button>

          <button
            onClick={() => onRequestDelete(symbolObj)}
            title="Usuń parę z menedżera"
            className="px-3 py-1.5 bg-red-950/60 hover:bg-red-900 border border-red-500/40 text-red-300 hover:text-white rounded-lg text-xs font-semibold font-mono transition-colors flex items-center justify-center gap-1"
          >
            <Trash2 className="w-3.5 h-3.5" />
            <span>Usuń</span>
          </button>
        </div>

        {/* PLAN-010 Phase 3: Trend Filter Recalibration panel */}
        <TrendFilterSection
          symbolObj={symbolObj}
          apiKey={apiKey}
          trendFilterEnabled={trendFilterEnabled}
          onToggleTrendFilter={() => setTrendFilterEnabled(v => !v)}
          onApplyParams={(lookback, threshold) =>
            onSaveSettings(symbolObj, allocUsd, baseRatio, timeframe, {
              stale_candles_threshold: staleCandles,
              stale_atr_threshold: staleAtr,
              impulse_negation_enabled: impulseNeg,
            }, {
              tma_trend_filter_enabled: true,
              tma_slope_lookback: lookback,
              tma_slope_threshold: threshold,
            })
          }
        />
      </div>
    </div>
  );
}

// PLAN-010 Phase 3: per-pair trend-filter recalibration panel — see
// .agents/plans/PLAN-010-trend-filter-recalibration-mechanism.md §5.
function TrendFilterSection({ symbolObj, apiKey, trendFilterEnabled, onToggleTrendFilter, onApplyParams }) {
  const [recalStatus, setRecalStatus] = useState(null);
  const [showModal, setShowModal] = useState(false);
  const [job, setJob] = useState(null); // {job_id, status, verdict, result, error}
  const [historyOpen, setHistoryOpen] = useState(false);
  const [history, setHistory] = useState([]);
  const pollRef = useRef(null);

  const fetchRecalStatus = async () => {
    try {
      const res = await fetch(`/api/v1/symbols/${encodeURIComponent(symbolObj.symbol)}/recalibration-status`);
      if (res.ok) setRecalStatus(await res.json());
    } catch (err) {
      console.warn('[TrendFilterSection] status fetch failed:', err);
    }
  };

  useEffect(() => {
    fetchRecalStatus();
    const interval = setInterval(fetchRecalStatus, 15000);
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbolObj.symbol]);

  // Stop polling a job's status if the component unmounts mid-run — this only
  // stops OUR polling, it never cancels the job itself (it keeps running
  // server-side; reopening "historia kalibracji" shows it once finished, per plan).
  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current); }, []);

  const pollJobStatus = (jobId) => {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      try {
        const res = await fetch(`/api/v1/symbols/${encodeURIComponent(symbolObj.symbol)}/recalibrate/status/${jobId}`);
        if (res.ok) {
          const data = await res.json();
          setJob(data);
          if (data.status === 'done' || data.status === 'failed') {
            clearInterval(pollRef.current);
            pollRef.current = null;
            fetchRecalStatus();
          }
        }
      } catch (err) {
        console.warn('[TrendFilterSection] status poll failed:', err);
      }
    }, 2500);
  };

  const handleStartRecalibration = async () => {
    setShowModal(true);
    setJob({ status: 'pending' });
    const currentKey = apiKey || localStorage.getItem('mms4_api_key') || 'test-api-auth-key-unit-test';
    try {
      const res = await fetch(`/api/v1/symbols/${encodeURIComponent(symbolObj.symbol)}/recalibrate`, {
        method: 'POST',
        headers: { 'X-MMS4-API-KEY': currentKey },
      });
      if (res.status === 202) {
        const data = await res.json();
        setJob({ job_id: data.job_id, status: data.status });
        pollJobStatus(data.job_id);
      } else {
        const errBody = await res.json().catch(() => ({}));
        setJob({ status: 'failed', error: errBody.detail?.message || 'Nie udało się uruchomić kalibracji.' });
      }
    } catch (err) {
      setJob({ status: 'failed', error: 'Błąd sieci podczas uruchamiania kalibracji.' });
    }
  };

  const handleApply = () => {
    const rec = job?.result?.recommended_params;
    if (!rec) return;
    onApplyParams(rec.lookback, rec.threshold);
    setShowModal(false);
  };

  const loadHistory = async () => {
    const opening = !historyOpen;
    setHistoryOpen(opening);
    if (opening) {
      try {
        const res = await fetch(`/api/v1/symbols/${encodeURIComponent(symbolObj.symbol)}/recalibrate/history`);
        if (res.ok) setHistory((await res.json()).runs || []);
      } catch (err) {
        console.warn('[TrendFilterSection] history fetch failed:', err);
      }
    }
  };

  const cleanCount = recalStatus?.clean_trade_count ?? 0;
  const threshold = recalStatus?.eligibility_threshold ?? 175;
  const eligible = recalStatus?.eligible === true;
  const lastVerdict = recalStatus?.last_calibration_verdict;

  return (
    <div className="pt-2 border-t border-slate-800/50">
      <div className="flex items-center gap-2 mb-2">
        <Search className="w-3.5 h-3.5 text-purple-400" />
        <span className="text-[10px] font-bold text-purple-400 font-mono tracking-wide">
          FILTR TRENDU (TMA SLOPE)
        </span>
      </div>

      <div className="flex items-center justify-between gap-2 text-xs mb-2">
        <button
          type="button"
          // Disable-only toggle, deliberately: re-enabling with unvalidated schema
          // defaults (lookback=5, threshold=0.002) via a plain click would reintroduce
          // exactly the ungrounded-parameter anti-pattern this whole mechanism exists
          // to prevent. Turning the filter back ON only happens via a validated
          // recalibration's "✅ Zastosuj nowe parametry" (which sets real params AND
          // enabled=true together, atomically).
          onClick={() => trendFilterEnabled && onToggleTrendFilter()}
          disabled={!trendFilterEnabled}
          title={trendFilterEnabled
            ? 'Wyłącz filtr trendu (kliknij, potem "Zapisz Ustawienia")'
            : 'Włącz tylko przez zatwierdzenie zwalidowanej kalibracji ("🔬 Rekalibruj" → "✅ Zastosuj nowe parametry")'}
          className={`py-1.5 px-2 rounded-lg text-xs font-mono font-bold border transition-colors ${
            trendFilterEnabled
              ? 'bg-emerald-950 text-emerald-300 border-emerald-500/40 hover:bg-emerald-900'
              : 'bg-slate-800 text-slate-500 border-slate-700 cursor-not-allowed'
          }`}
        >
          {trendFilterEnabled ? '✓ WŁ' : '✕ WYŁ'}
        </button>
        {trendFilterEnabled && (
          <span className="text-[10px] text-slate-400 font-mono">
            lookback={symbolObj.tma_slope_lookback ?? 5} · próg={symbolObj.tma_slope_threshold ?? 0.002}
          </span>
        )}
      </div>

      {lastVerdict && lastVerdict !== 'VALIDATED' && (
        <div className="mb-2 px-2 py-1 rounded-lg bg-amber-950/40 border border-amber-500/30 text-amber-300 text-[10px] font-mono flex items-center gap-1.5">
          <AlertTriangle className="w-3 h-3 shrink-0" />
          <span>ostatnia kalibracja: brak potwierdzenia ({lastVerdict})</span>
        </div>
      )}

      <div className="mb-2">
        <div className="flex justify-between text-[10px] text-slate-400 font-mono mb-1">
          <span>Dane do rekalibracji</span>
          <span>{cleanCount}/{threshold} czystych transakcji</span>
        </div>
        {recalStatus?.data_collection_epoch_start && (
          <div className="text-[9px] text-slate-500 font-mono mb-1">
            liczone od {new Date(recalStatus.data_collection_epoch_start).toLocaleDateString('pl-PL')} (zmiana reżimu handlu)
          </div>
        )}
        <div className="w-full h-1.5 bg-slate-800 rounded-full overflow-hidden">
          <div
            className={`h-full transition-all ${eligible ? 'bg-emerald-500' : 'bg-slate-600'}`}
            style={{ width: `${Math.min(100, (cleanCount / Math.max(1, threshold)) * 100)}%` }}
          />
        </div>
      </div>

      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={handleStartRecalibration}
          disabled={!eligible}
          title={eligible ? 'Uruchom rekalibrację' : `Wymagane ${threshold} czystych transakcji (obecnie ${cleanCount})`}
          className={`flex-1 py-1.5 rounded-lg text-xs font-semibold font-mono border transition-colors flex items-center justify-center gap-1.5 ${
            eligible
              ? 'bg-purple-950 hover:bg-purple-900 text-purple-300 border-purple-500/40'
              : 'bg-slate-800 text-slate-600 border-slate-700 cursor-not-allowed'
          }`}
        >
          <span>🔬 Rekalibruj</span>
        </button>
        <button
          type="button"
          onClick={loadHistory}
          className="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg text-xs font-mono border border-slate-700 transition-colors"
        >
          {historyOpen ? '▲' : '▼'} Historia
        </button>
      </div>

      {historyOpen && (
        <div className="mt-2 space-y-1 max-h-32 overflow-y-auto">
          {history.length === 0 && (
            <div className="text-[10px] text-slate-500 font-mono px-1">Brak zapisanych kalibracji.</div>
          )}
          {history.map(run => (
            <div key={run.job_id} className="flex justify-between text-[10px] font-mono px-2 py-1 bg-slate-950 border border-slate-800 rounded">
              <span className="text-slate-400">{new Date(run.created_at).toLocaleString('pl-PL')}</span>
              <span className={run.verdict === 'VALIDATED' ? 'text-emerald-400' : 'text-slate-500'}>
                {run.verdict || run.status}
              </span>
            </div>
          ))}
        </div>
      )}

      {showModal && (
        <RecalibrationModal
          symbol={symbolObj.symbol}
          job={job}
          onClose={() => setShowModal(false)}
          onApply={handleApply}
        />
      )}
    </div>
  );
}

function RecalibrationModal({ symbol, job, onClose, onApply }) {
  const status = job?.status;
  const verdict = job?.verdict || job?.result?.verdict;

  return (
    <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center p-4">
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-6 w-full max-w-lg shadow-2xl space-y-4 max-h-[85vh] overflow-y-auto">
        <div className="flex items-center justify-between pb-3 border-b border-slate-800">
          <h4 className="font-bold text-gray-100 font-mono text-sm">🔬 Kalibracja filtra trendu — {symbol}</h4>
          <button onClick={onClose} className="text-slate-400 hover:text-white">
            <X className="w-4 h-4" />
          </button>
        </div>

        {(status === 'pending' || status === 'running') && (
          <div className="text-center py-8 text-slate-300 font-mono text-sm">
            🔄 Kalibracja w toku… (może potrwać od kilkudziesięciu sekund do kilku minut)
          </div>
        )}

        {status === 'failed' && (
          <div className="px-3 py-2 rounded-lg bg-red-950/40 border border-red-500/30 text-red-300 text-xs font-mono">
            ❌ {job?.error || 'Kalibracja nie powiodła się.'}
          </div>
        )}

        {status === 'done' && job?.result && (
          <div className="space-y-3">
            <div className={`px-3 py-2 rounded-lg border text-xs font-mono ${
              verdict === 'VALIDATED'
                ? 'bg-emerald-950/40 border-emerald-500/30 text-emerald-300'
                : 'bg-amber-950/40 border-amber-500/30 text-amber-300'
            }`}>
              Werdykt: <strong>{verdict}</strong>
              {job.result.note && <div className="mt-1 text-slate-400">{job.result.note}</div>}
            </div>

            {['h1_to_h2', 'h2_to_h1'].map(dirKey => {
              const dir = job.result[dirKey];
              if (!dir) return null;
              return (
                <div key={dirKey} className="text-[11px] font-mono bg-slate-950 border border-slate-800 rounded-lg p-2">
                  <div className="text-slate-400 mb-1">Trening: {dir.train_window}</div>
                  {!dir.candidate_found ? (
                    <div className="text-slate-500">Brak konfiguracji lepszej od baseline.</div>
                  ) : (
                    <>
                      <div className="text-slate-300">lookback={dir.chosen_lookback}, próg={dir.chosen_threshold}</div>
                      <div className="text-slate-400">
                        test: R:R={dir.test_stats?.rr?.toFixed?.(2)} pnl={((dir.test_stats?.total_pnl ?? 0) * 100).toFixed(1)}%
                        {' '}vs baseline R:R={dir.test_baseline?.rr?.toFixed?.(2)}
                      </div>
                      <div className={dir.generalizes ? 'text-emerald-400' : 'text-red-400'}>
                        {dir.generalizes ? '✓ generalizuje na danych testowych' : '✗ NIE generalizuje'}
                      </div>
                    </>
                  )}
                </div>
              );
            })}
          </div>
        )}

        <div className="flex items-center gap-2 pt-2 border-t border-slate-800">
          {status === 'done' && verdict === 'VALIDATED' && job.result.recommended_params && (
            <button
              onClick={onApply}
              className="flex-1 py-1.5 bg-emerald-950 hover:bg-emerald-900 text-emerald-300 border border-emerald-500/40 rounded-lg text-xs font-semibold font-mono transition-colors"
            >
              ✅ Zastosuj nowe parametry
            </button>
          )}
          <button
            onClick={onClose}
            className="flex-1 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg text-xs font-semibold font-mono border border-slate-700 transition-colors"
          >
            Zamknij
          </button>
        </div>
      </div>
    </div>
  );
}
