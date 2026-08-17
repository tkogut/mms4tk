import React, { useState, useEffect } from 'react';
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

  const handleUpdateSymbolSettings = async (symObj, newAlloc, newBaseRatio, newTimeframe, staleConfig = {}) => {
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
          impulse_negation_enabled: staleConfig.impulse_negation_enabled !== false
        })
      });
      if (res.ok) {
        setMsg(`✓ Zapisano ustawienia dla ${symObj.symbol} ($${newAlloc} USD, Base/Add: ${newBaseRatio}%/${100 - newBaseRatio}%, Interwał: ${newTimeframe || '15m'})`);
        fetchSymbols();
      }
    } catch (err) {
      setMsg('❌ Błąd zapisu ustawień pary');
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

function SymbolCard({ symbolObj, isAutotradeReady, onToggle, onRequestDelete, onSaveSettings }) {
  const [allocUsd, setAllocUsd] = useState(symbolObj.allocation_usd || 10.0);
  const [baseRatio, setBaseRatio] = useState(symbolObj.base_ratio || 70);
  const [timeframe, setTimeframe] = useState(symbolObj.timeframe || '15m');
  const [staleCandles, setStaleCandles] = useState(symbolObj.stale_candles_threshold ?? 12);
  const [staleAtr, setStaleAtr] = useState(symbolObj.stale_atr_threshold ?? 0.5);
  const [impulseNeg, setImpulseNeg] = useState(symbolObj.impulse_negation_enabled !== false);

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
      </div>
    </div>
  );
}
