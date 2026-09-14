import React, { useState, useEffect } from 'react';
import { Activity, Wallet, ShieldAlert, CheckCircle2, RefreshCw, Zap, TrendingUp, Award, Percent, AlertOctagon, Scale, Layers } from 'lucide-react';

export function MonitoringTab({ stateData, statsData, kanbanCards, openPositions = [], daemonData = null, onRefresh }) {
  const [activeSymbols, setActiveSymbols] = useState([
    { symbol: 'BTC/USDC', state: 'BASE_MODE', leverage: 3.0, is_active: true },
    { symbol: 'ETH/USDC', state: 'BASE_MODE', leverage: 3.0, is_active: true },
    { symbol: 'XAU/USDC', state: 'BASE_MODE', leverage: 3.0, is_active: true },
    { symbol: 'XAG/USDC', state: 'BASE_MODE', leverage: 3.0, is_active: true }
  ]);

  useEffect(() => {
    const fetchSymbols = async () => {
      try {
        const res = await fetch('/api/v1/symbols');
        if (res.ok) {
          const data = await res.json();
          if (data.symbols && Array.isArray(data.symbols)) {
            // Filter ONLY active symbols! Paused symbols (is_active === false) are excluded from active monitoring grid.
            const activeOnly = data.symbols.filter(s => s.is_active !== false);
            setActiveSymbols(activeOnly);
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

  // Live sizing resolved by the engine, per symbol. Leverage alone no longer describes
  // risk posture: SCOUT_MODE keeps leverage exchange-legal (>=1x) and cuts exposure
  // through the size multiplier instead, so both have to be shown together.
  const allocationPlan = daemonData?.allocation_plan || {};
  const addonLedger = daemonData?.addon_ledger || [];
  const addonsUsed = new Set(
    addonLedger.filter((r) => Number(r.addon_count) > 0).map((r) => `${r.symbol}|${r.side}`)
  );
  const scoutSymbols = Object.entries(allocationPlan).filter(([, p]) => p.state === 'SCOUT_MODE');

  const totalTrades = statsData?.total_trades || 0;
  const hasTraded = totalTrades > 0;

  const freeUsdc = statsData?.free_balance !== undefined ? Number(statsData.free_balance).toFixed(2) : '0.00';
  
  // Live calculations across active open positions
  const totalUnrealizedPnl = (openPositions || []).reduce((acc, p) => acc + Number(p.unrealized_pnl || 0), 0);
  const totalNotionalUsd = (openPositions || []).reduce((acc, p) => acc + Number(p.notional_usd || 0), 0);
  
  // KPI Metrics: Only display real values if total_trades > 0, else 0.0 / Brak Transakcji
  const winRate = hasTraded && statsData?.win_rate !== undefined ? Number(statsData.win_rate).toFixed(1) : '0.0';
  const profitFactor = hasTraded && statsData?.profit_factor !== undefined ? Number(statsData.profit_factor).toFixed(2) : '0.00';
  // The API reports drawdown in USD, with the ratio in its own field. Showing the
  // percentage requires a starting balance, so it is only rendered when present.
  const maxDdUsd = hasTraded && statsData?.max_drawdown_usd !== undefined
    ? Number(statsData.max_drawdown_usd).toFixed(2)
    : (hasTraded && statsData?.max_drawdown !== undefined ? Number(statsData.max_drawdown).toFixed(2) : '0.00');
  const maxDdPct = hasTraded && Number(statsData?.max_drawdown_pct || 0) > 0
    ? Number(statsData.max_drawdown_pct).toFixed(2)
    : null;
  const avgRr = hasTraded && statsData?.average_rr !== undefined ? Number(statsData.average_rr).toFixed(2) : '0.00';

  return (
    <div className="space-y-6">
      {/* SCOUT_MODE banner — a symbol only leaves this state on a take-profit or a
          manual release, so it must be visible rather than buried in a per-symbol chip. */}
      {scoutSymbols.length > 0 && (
        <div className="bg-amber-950/40 border border-amber-500/40 rounded-xl p-4">
          <div className="flex items-start gap-3">
            <ShieldAlert className="w-5 h-5 text-amber-400 shrink-0 mt-0.5" />
            <div className="space-y-1">
              <div className="font-bold text-amber-200 text-sm">
                Tryb obronny SCOUT aktywny na {scoutSymbols.length}{' '}
                {scoutSymbols.length === 1 ? 'parze' : 'parach'}
              </div>
              <div className="text-xs text-amber-100/80 leading-relaxed">
                Ekspozycja zredukowana rozmiarem pozycji, dźwignia pozostaje zgodna z wymogiem
                giełdy (≥1×). Para wraca do BASE_MODE po pierwszym Take Profit albo po ręcznym
                zwolnieniu w zakładce Ustawienia.
              </div>
              <div className="flex flex-wrap gap-2 pt-1">
                {scoutSymbols.map(([sym, p]) => (
                  <span
                    key={sym}
                    className="text-[10px] font-mono bg-amber-900/50 border border-amber-500/30 text-amber-200 px-2 py-0.5 rounded"
                  >
                    {sym} · ${Number(p.notional_base_usd).toFixed(2)} · ↓
                    {Number(p.notional_reduction_factor).toFixed(1)}×
                  </span>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}

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
          {activeSymbols.map((s) => {
            // Engine-resolved plan wins over the /symbols config: it reflects the CURRENT
            // risk state, including the SCOUT size reduction.
            const plan = allocationPlan[s.symbol];
            const symState = plan?.state || s.state || 'BASE_MODE';
            const symLeverage = plan?.leverage ?? s.leverage ?? leverage;
            const sizeMult = plan?.size_multiplier ?? 1.0;
            const isReduced = sizeMult !== 1.0;
            const hasAddon =
              addonsUsed.has(`${s.symbol}|long`) || addonsUsed.has(`${s.symbol}|short`);

            return (
              <div key={s.symbol} className="bg-slate-950 border border-slate-800 rounded-xl p-4 space-y-3">
                <div className="flex items-center justify-between border-b border-slate-800/80 pb-2">
                  <span className="font-extrabold text-gray-100 font-mono text-sm">{s.symbol}</span>
                  <div className="flex items-center gap-1.5">
                    {hasAddon && (
                      <span
                        title="Dokładka 30% już wykorzystana na tej pozycji"
                        className="px-2 py-0.5 text-[10px] rounded-full font-mono font-bold border bg-violet-950 text-violet-300 border-violet-500/40"
                      >
                        ADD ✓
                      </span>
                    )}
                    <span className={`px-2 py-0.5 text-[10px] rounded-full font-mono font-bold border ${
                      symState === 'SCOUT_MODE'
                        ? 'bg-amber-950 text-amber-300 border-amber-500/40'
                        : 'bg-emerald-950 text-emerald-300 border-emerald-500/40'
                    }`}>
                      {symState}
                    </span>
                  </div>
                </div>

                <div className="grid grid-cols-4 gap-1.5 text-xs font-mono">
                  <div>
                    <span className="text-slate-500 text-[9px] block">Alokacja ($):</span>
                    <span className="font-bold text-emerald-400">${Number(s.allocation_usd || 10.0).toFixed(2)}</span>
                  </div>
                  <div>
                    <span className="text-slate-500 text-[9px] block">Interwał:</span>
                    <span className="font-bold text-amber-400">{s.timeframe || '15m'}</span>
                  </div>
                  <div>
                    <span className="text-slate-500 text-[9px] block">Dźwignia:</span>
                    <span className="font-bold text-blue-400">{Number(symLeverage).toFixed(1)}x</span>
                  </div>
                  <div>
                    <span className="text-slate-500 text-[9px] block">Rozmiar:</span>
                    <span className={`font-bold ${isReduced ? 'text-amber-400' : 'text-slate-300'}`}>
                      {(Number(sizeMult) * 100).toFixed(0)}%
                    </span>
                  </div>
                </div>

                {plan && (
                  <div className="flex items-center justify-between text-[10px] font-mono border-t border-slate-800/80 pt-2">
                    <span className="text-slate-500">
                      Pozycja bazowa:{' '}
                      <span className="text-slate-300 font-bold">
                        ${Number(plan.notional_base_usd).toFixed(2)}
                      </span>
                      <span className="text-slate-600"> nominał</span>
                    </span>
                    {isReduced && (
                      <span className="text-amber-400 font-bold">
                        ↓ {Number(plan.notional_reduction_factor).toFixed(1)}× mniej
                      </span>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* Primary Account Balances Grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
          <div className="flex items-center justify-between text-slate-400 mb-2">
            <span className="text-xs font-medium">Wolny Dostępny Depozyt (OKX)</span>
            <div className="flex items-center gap-1.5">
              {statsData?.is_mock && (
                <span className="text-[10px] font-mono bg-amber-950/80 border border-amber-500/40 text-amber-300 px-1.5 py-0.5 rounded font-bold">
                  MOCK
                </span>
              )}
              <Wallet className={`w-4 h-4 ${statsData?.is_mock ? 'text-amber-400' : 'text-emerald-400'}`} />
            </div>
          </div>
          <div className="text-2xl font-bold text-gray-100 font-mono flex items-baseline gap-2">
            <span>${freeUsdc}</span>
          </div>
          <div className="text-xs text-slate-400 mt-1">
            {statsData?.is_mock
              ? 'Tryb symulacji (brak kluczy API w .env)'
              : 'Czysty wolny margin wg OKX API (availBal)'}
          </div>
        </div>

        <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
          <div className="flex items-center justify-between text-slate-400 mb-2">
            <span className="text-xs font-medium">Niezrealizowany PnL (uPnL)</span>
            <Activity className="w-4 h-4 text-blue-400" />
          </div>
          <div className={`text-2xl font-bold font-mono ${
            totalUnrealizedPnl >= 0 ? 'text-emerald-400' : 'text-red-400'
          }`}>
            {totalUnrealizedPnl >= 0 ? `+$${totalUnrealizedPnl.toFixed(2)}` : `-$${Math.abs(totalUnrealizedPnl).toFixed(2)}`}
          </div>
          <div className="text-xs text-slate-400 mt-1">
            Wycena pozycji (${totalNotionalUsd.toFixed(2)} Notional)
          </div>
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
              {hasTraded ? `$${maxDdUsd}` : '$0.00'}
            </div>
            <div className="text-[10px] text-slate-500">
              {maxDdPct ? `Maksymalne obsunięcie · ${maxDdPct}% kapitału` : 'Maksymalne obsunięcie (USD)'}
            </div>
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
