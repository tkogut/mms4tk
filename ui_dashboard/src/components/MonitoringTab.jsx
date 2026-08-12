import React, { useState, useEffect } from 'react';
import { Activity, Wallet, ShieldAlert, CheckCircle2, RefreshCw, Zap, TrendingUp, Award, Percent, AlertOctagon, Scale, Layers } from 'lucide-react';

export function MonitoringTab({ stateData, statsData, kanbanCards, onRefresh }) {
  const [activeSymbols, setActiveSymbols] = useState([
    { symbol: 'BTC/USDC', state: 'BASE_MODE', leverage: 3.0, is_active: true },
    { symbol: 'ETH/USDC', state: 'BASE_MODE', leverage: 3.0, is_active: true },
    { symbol: 'SOL/USDC', state: 'BASE_MODE', leverage: 3.0, is_active: true }
  ]);

  useEffect(() => {
    const fetchSymbols = async () => {
      try {
        const res = await fetch('/api/v1/symbols');
        if (res.ok) {
          const data = await res.json();
          if (data.symbols && Array.isArray(data.symbols)) {
            setActiveSymbols(data.symbols);
          }
        }
      } catch (err) {
        console.warn('[MonitoringTab] Error fetching symbols:', err);
      }
    };
    fetchSymbols();
  }, []);

  const isScoutMode = stateData?.state === 'SCOUT_MODE' || stateData?.scout_mode || false;
  const activeMode = stateData?.state || (isScoutMode ? 'SCOUT_MODE' : 'BASE_MODE');
  const leverage = stateData?.leverage || 3.0;

  const totalTrades = statsData?.total_trades || 0;
  const hasTraded = totalTrades > 0;

  const freeUsdc = statsData?.free_balance !== undefined ? Number(statsData.free_balance).toFixed(2) : '0.00';
  const btcBalance = statsData?.btc_balance !== undefined ? Number(statsData.btc_balance).toFixed(5) : '0.00000';
  
  // KPI Metrics: Only display real values if total_trades > 0, else 0.0 / Brak Transakcji
  const winRate = hasTraded && statsData?.win_rate !== undefined ? Number(statsData.win_rate).toFixed(1) : '0.0';
  const profitFactor = hasTraded && statsData?.profit_factor !== undefined ? Number(statsData.profit_factor).toFixed(2) : '0.00';
  const maxDd = hasTraded && statsData?.max_drawdown !== undefined ? Number(statsData.max_drawdown).toFixed(2) : '0.00';
  const avgRr = hasTraded && statsData?.average_rr !== undefined ? Number(statsData.average_rr).toFixed(2) : '0.00';

  return (
    <div className="space-y-6">
      {/* Operating Mode Header Banner */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 shadow-lg">
        <div className="flex flex-col md:flex-row items-start md:items-center justify-between gap-4">
          <div className="flex items-center gap-4">
            <div className={`p-3 rounded-xl border ${
              isScoutMode 
                ? 'bg-amber-500/10 border-amber-500/30 text-amber-400' 
                : 'bg-emerald-500/10 border-emerald-500/30 text-emerald-400'
            }`}>
              {isScoutMode ? <ShieldAlert className="w-8 h-8" /> : <CheckCircle2 className="w-8 h-8" />}
            </div>
            <div>
              <div className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Tryb Operacyjny Bota</div>
              <div className="flex items-center gap-3 mt-1">
                <span className={`text-xl font-bold font-mono px-3 py-1 rounded-lg border ${
                  isScoutMode 
                    ? 'bg-amber-950/60 border-amber-500/40 text-amber-300' 
                    : 'bg-emerald-950/60 border-emerald-500/40 text-emerald-300'
                }`}>
                  {activeMode}
                </span>
                <span className="text-xs text-slate-400 hidden sm:inline">
                  {isScoutMode ? 'Ochrona kapitału po SL' : 'Pełny alokator alfy aktywny'}
                </span>
              </div>
            </div>
          </div>

          <button
            onClick={onRefresh}
            className="flex items-center gap-2 px-4 py-2.5 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-lg text-sm font-medium transition-colors border border-slate-700 shadow-sm"
          >
            <RefreshCw className="w-4 h-4 text-blue-400" />
            <span>Odśwież Dane</span>
          </button>
        </div>
      </div>

      {/* Multi-Symbol Active Pairs Grid Header */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 shadow-lg space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="font-bold text-gray-100 text-base flex items-center gap-2">
            <Layers className="w-5 h-5 text-blue-400" />
            <span>Stan Aktywnych Par Rynkowych ({activeSymbols.length} Aktywne)</span>
          </h3>
          <span className="text-xs bg-blue-950 border border-blue-500/30 text-blue-300 px-3 py-1 rounded-full font-mono">
            Multi-Symbol Active Grid
          </span>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {activeSymbols.map((s) => (
            <div key={s.symbol} className="bg-slate-950 border border-slate-800 rounded-xl p-4 space-y-3">
              <div className="flex items-center justify-between border-b border-slate-800/80 pb-2">
                <span className="font-extrabold text-gray-100 font-mono text-sm">{s.symbol}</span>
                <span className={`px-2 py-0.5 text-[10px] rounded-full font-mono font-bold border ${
                  s.state === 'SCOUT_MODE'
                    ? 'bg-amber-950 text-amber-300 border-amber-500/40'
                    : 'bg-emerald-950 text-emerald-300 border-emerald-500/40'
                }`}>
                  {s.state || 'BASE_MODE'}
                </span>
              </div>

              <div className="grid grid-cols-2 gap-2 text-xs font-mono">
                <div>
                  <span className="text-slate-500 text-[10px] block">Dźwignia:</span>
                  <span className="font-bold text-blue-400">{Number(s.leverage || leverage).toFixed(1)}x</span>
                </div>
                <div>
                  <span className="text-slate-500 text-[10px] block">Alokacja Base/Add:</span>
                  <span className="font-bold text-slate-300">70% / 30%</span>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Primary Account Balances Grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
          <div className="flex items-center justify-between text-slate-400 mb-2">
            <span className="text-xs font-medium">Kapitał Wolny (USDC)</span>
            <Wallet className="w-4 h-4 text-emerald-400" />
          </div>
          <div className="text-2xl font-bold text-gray-100 font-mono">
            ${freeUsdc}
          </div>
          <div className="text-xs text-slate-400 mt-1">Dostępny w portfelu SPOT</div>
        </div>

        <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
          <div className="flex items-center justify-between text-slate-400 mb-2">
            <span className="text-xs font-medium">Saldo BTC</span>
            <TrendingUp className="w-4 h-4 text-blue-400" />
          </div>
          <div className="text-2xl font-bold text-gray-100 font-mono">
            {btcBalance} BTC
          </div>
          <div className="text-xs text-slate-400 mt-1">Rynek OKX Spot BTC/USDC</div>
        </div>

        <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
          <div className="flex items-center justify-between text-slate-400 mb-2">
            <span className="text-xs font-medium">Liczba Zorganizowanych Transakcji</span>
            <Zap className="w-4 h-4 text-amber-400" />
          </div>
          <div className="text-2xl font-bold text-gray-100 font-mono">
            {totalTrades}
          </div>
          <div className="text-xs text-slate-400 mt-1">Zamknięte w SQLite logs.db</div>
        </div>

        <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
          <div className="flex items-center justify-between text-slate-400 mb-2">
            <span className="text-xs font-medium">Wygenerowane PnL ($)</span>
            <Award className="w-4 h-4 text-emerald-400" />
          </div>
          <div className={`text-2xl font-bold font-mono ${
            Number(statsData?.realized_pnl || 0) >= 0 ? 'text-emerald-400' : 'text-red-400'
          }`}>
            ${Number(statsData?.realized_pnl || 0).toFixed(2)}
          </div>
          <div className="text-xs text-slate-400 mt-1">Skumulowany zysk z SQLite</div>
        </div>
      </div>

      {/* KPI Summary Section (Win Rate, Profit Factor, MDD, R:R) */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 shadow-lg space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="font-semibold text-gray-200 text-sm flex items-center gap-2">
            <Award className="w-4 h-4 text-amber-400" />
            <span>Kafelki Wydajności Algorytmu (KPI Summary)</span>
          </h3>
          {!hasTraded && (
            <span className="text-[10px] text-amber-400 font-mono bg-amber-950/60 px-2 py-0.5 rounded border border-amber-500/30">
              Oczekiwanie na pierwsze zrealizowane transakcje z OKX
            </span>
          )}
        </div>

        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          <div className="bg-slate-950/80 border border-slate-800 rounded-lg p-3.5">
            <div className="flex items-center justify-between text-slate-400 mb-1">
              <span className="text-xs font-medium">Win Rate</span>
              <Percent className="w-3.5 h-3.5 text-emerald-400" />
            </div>
            <div className="text-xl font-bold text-emerald-400 font-mono">
              {hasTraded ? `${winRate}%` : '0.0%'}
            </div>
            <div className="text-[10px] text-slate-500">Skuteczność transakcji</div>
          </div>

          <div className="bg-slate-950/80 border border-slate-800 rounded-lg p-3.5">
            <div className="flex items-center justify-between text-slate-400 mb-1">
              <span className="text-xs font-medium">Profit Factor</span>
              <TrendingUp className="w-3.5 h-3.5 text-blue-400" />
            </div>
            <div className="text-xl font-bold text-blue-400 font-mono">
              {hasTraded ? profitFactor : '0.00'}
            </div>
            <div className="text-[10px] text-slate-500">Stosunek zysku do strat</div>
          </div>

          <div className="bg-slate-950/80 border border-slate-800 rounded-lg p-3.5">
            <div className="flex items-center justify-between text-slate-400 mb-1">
              <span className="text-xs font-medium">Max Drawdown</span>
              <AlertOctagon className="w-3.5 h-3.5 text-red-400" />
            </div>
            <div className="text-xl font-bold text-red-400 font-mono">
              {hasTraded ? `$${maxDd}` : '$0.00'}
            </div>
            <div className="text-[10px] text-slate-500">Maksymalne obsunięcie</div>
          </div>

          <div className="bg-slate-950/80 border border-slate-800 rounded-lg p-3.5">
            <div className="flex items-center justify-between text-slate-400 mb-1">
              <span className="text-xs font-medium">R:R Ratio</span>
              <Scale className="w-3.5 h-3.5 text-amber-400" />
            </div>
            <div className="text-xl font-bold text-amber-400 font-mono">
              {hasTraded ? avgRr : '0.00'}
            </div>
            <div className="text-[10px] text-slate-500">Zysk do ryzyka</div>
          </div>
        </div>
      </div>

      {/* Hermes Kanban Active Tasks */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <Activity className="w-5 h-5 text-blue-400" />
            <h3 className="font-semibold text-gray-200">🤖 Aktywne Zadania Agenta Hermes (Kanban)</h3>
          </div>
          <span className="text-xs bg-blue-950 border border-blue-500/30 text-blue-300 px-2.5 py-1 rounded-full font-mono">
            {kanbanCards?.length || 0} kart
          </span>
        </div>

        {kanbanCards && kanbanCards.length > 0 ? (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
            {kanbanCards.map((card, idx) => (
              <div key={idx} className="bg-slate-950/80 border border-slate-800 rounded-lg p-3.5 space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-mono text-blue-400 bg-blue-950/60 px-2 py-0.5 rounded border border-blue-900">
                    {card.status || 'IN_PROGRESS'}
                  </span>
                  <span className="text-[10px] text-slate-400">{card.created_at || 'Teraz'}</span>
                </div>
                <div className="text-sm font-medium text-slate-200">{card.title || card.name}</div>
                {card.description && (
                  <p className="text-xs text-slate-400 line-clamp-2">{card.description}</p>
                )}
              </div>
            ))}
          </div>
        ) : (
          <div className="text-center py-6 text-slate-500 text-xs font-mono bg-slate-950/40 rounded-lg border border-slate-800/60">
            Brak oczekujących zadań w systemie Hermes Kanban.
          </div>
        )}
      </div>
    </div>
  );
}
