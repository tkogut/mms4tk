import React, { useState, useEffect } from 'react';
import { Layers, Plus, Trash2, Shield, Activity, Power, CheckCircle, PauseCircle, AlertTriangle } from 'lucide-react';

export function SymbolsTab({ apiKey }) {
  const [symbols, setSymbols] = useState([
    { symbol: 'BTC/USDC', state: 'BASE_MODE', leverage: 3.0, is_active: true, risk_profile: 'MODERATE' },
    { symbol: 'ETH/USDC', state: 'BASE_MODE', leverage: 3.0, is_active: true, risk_profile: 'MODERATE' },
    { symbol: 'SOL/USDC', state: 'BASE_MODE', leverage: 3.0, is_active: true, risk_profile: 'MODERATE' }
  ]);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [newSymbolInput, setNewSymbolInput] = useState('');
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

  const handleAddSymbol = async (e) => {
    e.preventDefault();
    let raw = newSymbolInput.trim().toUpperCase();
    if (!raw) return;
    if (!raw.includes('/')) {
      if (raw.endsWith('USDC')) {
        raw = raw.replace('USDC', '/USDC');
      } else {
        raw = raw + '/USDC';
      }
    }
    setLoading(true);
    try {
      const currentKey = apiKey || 'test-api-auth-key-unit-test';
      const res = await fetch('/api/v1/symbols/add', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-MMS4-API-KEY': currentKey
        },
        body: JSON.stringify({ symbol: raw, is_active: true })
      });
      if (res.ok) {
        setMsg(`✓ Para ${raw} została pomyślnie aktywowana!`);
        setNewSymbolInput('');
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
    const currentKey = apiKey || 'test-api-auth-key-unit-test';
    try {
      if (symObj.is_active) {
        // Deactivate symbol
        const res = await fetch(`/api/v1/symbols/${encodeURIComponent(symObj.symbol)}`, {
          method: 'DELETE',
          headers: { 'X-MMS4-API-KEY': currentKey }
        });
        if (res.ok) {
          setMsg(`⏸️ Para ${symObj.symbol} została wstrzymana przez Użytkownika.`);
          fetchSymbols();
        }
      } else {
        // Activate symbol
        const res = await fetch('/api/v1/symbols/add', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-MMS4-API-KEY': currentKey
          },
          body: JSON.stringify({ symbol: symObj.symbol, is_active: true })
        });
        if (res.ok) {
          setMsg(`▶️ Para ${symObj.symbol} została włączona do tradingu!`);
          fetchSymbols();
        }
      }
    } catch (err) {
      setMsg('❌ Błąd przełączania stanu pary');
    } finally {
      setTimeout(() => setMsg(''), 4000);
    }
  };

  return (
    <div className="space-y-6">
      {/* Header & Add Pair Button */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 shadow-lg flex flex-wrap items-center justify-between gap-4">
        <div>
          <h3 className="text-lg font-bold text-gray-100 flex items-center gap-2">
            <Layers className="w-5 h-5 text-blue-400" />
            <span>Zarządzanie Parami Rynkowymi (Multi-Symbol Manager)</span>
          </h3>
          <p className="text-xs text-slate-400 mt-1">
            Użytkownik ma pełną kontrolę nad aktywacją i pauzowaniem poszczególnych par tradingowych
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
        <div className="p-3 bg-blue-950/80 border border-blue-500/40 rounded-xl text-xs text-blue-300 font-mono flex items-center gap-2">
          <Activity className="w-4 h-4 text-blue-400" />
          <span>{msg}</span>
        </div>
      )}

      {/* Grid of Symbol Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
        {symbols.map((s) => (
          <div
            key={s.symbol}
            className={`border rounded-xl p-5 shadow-lg space-y-4 transition-all ${
              s.is_active !== false
                ? 'bg-slate-900 border-slate-800'
                : 'bg-slate-950/60 border-slate-900 opacity-60'
            }`}
          >
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <div className={`p-2 rounded-lg ${s.is_active !== false ? 'bg-blue-500/10 text-blue-400' : 'bg-slate-800 text-slate-500'}`}>
                  <Activity className="w-5 h-5" />
                </div>
                <div>
                  <h4 className="font-extrabold text-base text-gray-100 font-mono">{s.symbol}</h4>
                  <span className="text-[10px] text-slate-400 block font-mono">OKX EEA Spot Perp</span>
                </div>
              </div>

              {/* User Toggle Switch: Active / Paused */}
              <button
                onClick={() => handleToggleSymbol(s)}
                className={`flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-bold font-mono transition-all border ${
                  s.is_active !== false
                    ? 'bg-emerald-950 text-emerald-300 border-emerald-500/40 hover:bg-emerald-900'
                    : 'bg-slate-800 text-slate-400 border-slate-700 hover:bg-slate-700'
                }`}
              >
                <Power className="w-3.5 h-3.5" />
                <span>{s.is_active !== false ? 'AKTYWNA' : 'WSTRZYMANA'}</span>
              </button>
            </div>

            {/* Per-Symbol State Badges & Leverage */}
            <div className="grid grid-cols-2 gap-2 pt-2 border-t border-slate-800 text-xs font-mono">
              <div className="bg-slate-950 p-2.5 rounded-lg border border-slate-800/80">
                <span className="text-[10px] text-slate-400 block mb-0.5">Stan Strategii:</span>
                <span className={`font-bold text-xs ${s.state === 'SCOUT_MODE' ? 'text-amber-400' : 'text-emerald-400'}`}>
                  {s.state || 'BASE_MODE'}
                </span>
              </div>

              <div className="bg-slate-950 p-2.5 rounded-lg border border-slate-800/80">
                <span className="text-[10px] text-slate-400 block mb-0.5">Aktywna Dźwignia:</span>
                <span className="font-bold text-xs text-blue-400">
                  {Number(s.leverage || 3.0).toFixed(1)}x
                </span>
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* Add Symbol Modal */}
      {isModalOpen && (
        <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-slate-900 border border-slate-800 rounded-xl p-6 w-full max-w-md shadow-2xl space-y-4">
            <div className="flex items-center justify-between pb-3 border-b border-slate-800">
              <h3 className="font-bold text-gray-100 text-base flex items-center gap-2">
                <Plus className="w-5 h-5 text-blue-400" />
                <span>Dodaj Nową Parę Rynkową</span>
              </h3>
              <button
                onClick={() => setIsModalOpen(false)}
                className="text-slate-400 hover:text-slate-200 text-sm font-bold"
              >
                ✕
              </button>
            </div>

            <form onSubmit={handleAddSymbol} className="space-y-4">
              <div>
                <label className="block text-xs font-semibold text-slate-300 mb-1.5">
                  Nazwa Symbolu (OKX Pair):
                </label>
                <input
                  type="text"
                  placeholder="np. ETH/USDC, SOL/USDC, AVAX/USDC..."
                  value={newSymbolInput}
                  onChange={(e) => setNewSymbolInput(e.target.value)}
                  className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-lg text-sm text-slate-200 focus:outline-none focus:border-blue-500 font-mono"
                  required
                />
              </div>

              <div className="flex justify-end gap-3 pt-2">
                <button
                  type="button"
                  onClick={() => setIsModalOpen(false)}
                  className="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg text-xs font-semibold"
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
