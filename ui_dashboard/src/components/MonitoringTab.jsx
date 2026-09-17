import React, { useState, useEffect } from 'react';
import { Activity, ShieldAlert, Layers, Clock, AlertTriangle, XCircle, CheckCircle, ChevronRight, Zap } from 'lucide-react';
import { AggregatedPnLCard } from './AggregatedPnLCard';
import { formatSignedUsd } from '../utils/formatters';

// Symbol normalization helper
const normalizeSym = (sym) => {
  if (!sym) return '';
  let str = String(sym).toUpperCase().trim();
  str = str.split(':')[0];
  str = str.replace(/-\d{6,}$/, '');
  str = str.replace(/-\d+$/, '');
  str = str.replace('_UM_XPERP', '');
  str = str.replace('-USD_UM_XPERP', '');
  str = str.replace('_XPERP', '');
  str = str.replace('-SWAP', '');
  str = str.replace('-SPOT', '');

  if (str.includes('/')) {
    const parts = str.split('/');
    return `${parts[0]}/${parts[1] === 'USD' ? 'USDC' : parts[1] || 'USDC'}`;
  }
  if (str.includes('-')) {
    const parts = str.split('-');
    return `${parts[0]}/${parts[1] === 'USD' ? 'USDC' : parts[1] || 'USDC'}`;
  }
  return `${str}/USDC`;
};

export function MonitoringTab({ stateData, statsData, kanbanCards = [], openPositions = [], daemonData = null, tradesHistory = [], apiKey = '', onRefresh }) {
  const [activeSymbols, setActiveSymbols] = useState([
    { symbol: 'BTC/USDC', state: 'BASE_MODE', leverage: 3.0, is_active: true, allocation_usd: 100 },
    { symbol: 'ETH/USDC', state: 'BASE_MODE', leverage: 3.0, is_active: true, allocation_usd: 100 },
    { symbol: 'XAU/USDC', state: 'BASE_MODE', leverage: 3.0, is_active: true, allocation_usd: 100 },
    { symbol: 'XAG/USDC', state: 'BASE_MODE', leverage: 3.0, is_active: true, allocation_usd: 100 }
  ]);

  // Position Double Confirmation Close State
  const [confirmCloseTarget, setConfirmCloseTarget] = useState(null);
  const [closingSym, setClosingSym] = useState(null);
  const [actionMsg, setActionMsg] = useState('');

  useEffect(() => {
    const fetchSymbols = async () => {
      try {
        const res = await fetch('/api/v1/symbols');
        if (res.ok) {
          const data = await res.json();
          if (data.symbols && Array.isArray(data.symbols)) {
            const activeOnly = data.symbols.filter((s) => s.is_active !== false);
            setActiveSymbols(activeOnly);
          }
        }
      } catch (err) {
        console.warn('[MonitoringTab] Error fetching symbols:', err);
      }
    };
    fetchSymbols();
  }, []);

  const allocationPlan = daemonData?.allocation_plan || {};
  const addonLedger = daemonData?.addon_ledger || [];
  const addonsUsed = new Set(
    addonLedger.filter((r) => Number(r.addon_count) > 0).map((r) => `${r.symbol}|${r.side}`)
  );
  const scoutSymbols = Object.entries(allocationPlan).filter(([, p]) => p.state === 'SCOUT_MODE');

  // Map open positions by normalized symbol for fast lookup in symbol cards
  const positionsBySym = React.useMemo(() => {
    const map = {};
    (openPositions || []).forEach((pos) => {
      const norm = normalizeSym(pos.symbol || pos.raw_symbol);
      map[norm] = pos;
    });
    return map;
  }, [openPositions]);

  // Close position execution
  const executeClosePosition = async (cleanSym, side) => {
    setClosingSym(cleanSym);
    setActionMsg('');
    try {
      const currentKey = apiKey || localStorage.getItem('mms4_api_key') || 'test-api-auth-key-unit-test';
      const res = await fetch('/api/v1/positions/close', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-MMS4-API-KEY': currentKey
        },
        body: JSON.stringify({ symbol: cleanSym, side: side ? side.toLowerCase() : null })
      });
      if (res.ok) {
        setActionMsg(`✓ Pozycja ${cleanSym} (${side || 'ALL'}) zamknięta na giełdzie OKX.`);
        setConfirmCloseTarget(null);
        if (onRefresh) onRefresh();
      } else {
        const errData = await res.json().catch(() => ({}));
        setActionMsg(`❌ Błąd: ${errData.detail || errData.message || 'Odrzucono przez API'}`);
      }
    } catch (err) {
      setActionMsg('❌ Błąd połączenia z serwerem API');
    } finally {
      setClosingSym(null);
      setTimeout(() => setActionMsg(''), 5000);
    }
  };

  return (
    <div className="space-y-3">
      {/* Toast Notification */}
      {actionMsg && (
        <div className={`p-2.5 rounded-lg border text-xs font-mono font-bold flex items-center justify-between shadow-md transition-all ${
          actionMsg.includes('✓')
            ? 'bg-emerald-950/90 border-emerald-500/50 text-emerald-300'
            : 'bg-rose-950/90 border-rose-500/50 text-rose-300'
        }`}>
          <div className="flex items-center gap-2">
            {actionMsg.includes('✓') ? <CheckCircle className="w-4 h-4 text-emerald-400" /> : <AlertTriangle className="w-4 h-4 text-rose-400" />}
            <span>{actionMsg}</span>
          </div>
          <button onClick={() => setActionMsg('')} className="text-slate-400 hover:text-white text-xs">✕</button>
        </div>
      )}

      {/* 1. HERO SECTION: AggregatedPnLCard (Top Dominant Metric) */}
      <AggregatedPnLCard
        statsData={statsData}
        openPositions={openPositions}
        tradesHistory={tradesHistory}
      />

      {/* SCOUT_MODE Warning Banner (Compact Margin Guard) */}
      {scoutSymbols.length > 0 && (
        <div className="bg-amber-950/30 border border-amber-500/30 rounded-lg p-2.5 flex items-center justify-between text-xs">
          <div className="flex items-center gap-2">
            <ShieldAlert className="w-4 h-4 text-amber-400 shrink-0" />
            <span className="font-bold text-amber-300">
              Tryb obronny SCOUT aktywny ({scoutSymbols.length} {scoutSymbols.length === 1 ? 'para' : 'pary'}):
            </span>
            <div className="flex flex-wrap gap-1.5">
              {scoutSymbols.map(([sym, p]) => (
                <span
                  key={sym}
                  className="text-[10px] font-mono tabular-nums bg-amber-900/40 border border-amber-500/30 text-amber-200 px-1.5 py-0.2 rounded"
                >
                  {sym} · ${Number(p.notional_base_usd).toFixed(2)} · ↓{Number(p.notional_reduction_factor).toFixed(1)}×
                </span>
              ))}
            </div>
          </div>
          <span className="text-[10px] text-amber-300/70 hidden md:inline">
            Powrót do BASE_MODE po pierwszym TP
          </span>
        </div>
      )}

      {/* 2. MULTI-DIMENSIONAL SYMBOL CARDS (Bento Grid on Desktop / Snap Ribbon on Mobile) */}
      <div className="space-y-1.5">
        <div className="flex items-center justify-between px-1">
          <span className="text-xs font-mono font-bold text-slate-300 flex items-center gap-1.5 uppercase tracking-wider">
            <Layers className="w-3.5 h-3.5 text-blue-400" />
            Aktywne Pary Rynkowe ({activeSymbols.length})
          </span>
          <span className="text-[10px] font-mono text-slate-500">
            Multi-Symbol Bento Grid · 30m/1h Sync
          </span>
        </div>

        {/* Responsive Bento Grid / Mobile Horizontal Snap Ribbon */}
        <div className="flex overflow-x-auto snap-x snap-mandatory gap-2 pb-1 scrollbar-none sm:grid sm:grid-cols-2 lg:grid-cols-4 sm:overflow-visible">
          {activeSymbols.map((s) => {
            const plan = allocationPlan[s.symbol];
            const symState = plan?.state || s.state || 'BASE_MODE';
            const symLeverage = plan?.leverage ?? s.leverage ?? 3.0;
            const sizeMult = plan?.size_multiplier ?? 1.0;
            const isReduced = sizeMult !== 1.0;
            const hasAddon =
              addonsUsed.has(`${s.symbol}|long`) || addonsUsed.has(`${s.symbol}|short`);

            const livePos = positionsBySym[s.symbol];
            const hasPosition = Boolean(livePos);
            const side = livePos?.side ? String(livePos.side).toUpperCase() : null;
            const isLong = side === 'BUY' || side === 'LONG';
            const posUpnl = Number(livePos?.unrealized_pnl || 0);
            const markPx = Number(livePos?.mark_price || 0);
            const entryPx = Number(livePos?.entry_price || 0);

            // Progress towards TP/SL estimation
            let progressPct = 50;
            if (hasPosition && entryPx > 0 && markPx > 0) {
              const diffPct = ((markPx - entryPx) / entryPx) * 100 * (isLong ? 1 : -1);
              // Scale +-2% movement to 0-100% bar
              progressPct = Math.max(5, Math.min(95, 50 + diffPct * 25));
            }

            return (
              <div
                key={s.symbol}
                className="snap-start shrink-0 w-[260px] sm:w-auto bg-slate-950/80 border border-slate-800 rounded-lg p-2.5 space-y-2 hover:border-slate-700 transition-colors shadow-sm"
              >
                {/* Header Row: Symbol + Status Badge */}
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-1.5">
                    <span className={`w-2 h-2 rounded-full ${hasPosition ? 'bg-emerald-400 animate-pulse' : 'bg-slate-600'}`} />
                    <span className="font-mono font-black text-sm text-gray-100 tracking-tight">{s.symbol}</span>
                  </div>

                  <div className="flex items-center gap-1">
                    {hasAddon && (
                      <span className="px-1.5 py-0.2 text-[9px] rounded font-mono font-bold bg-violet-950 text-violet-300 border border-violet-500/40">
                        ADD ✓
                      </span>
                    )}
                    <span className={`px-1.5 py-0.2 text-[9px] rounded font-mono font-bold border ${
                      symState === 'SCOUT_MODE'
                        ? 'bg-amber-950 text-amber-300 border-amber-500/40'
                        : 'bg-emerald-950 text-emerald-300 border-emerald-500/40'
                    }`}>
                      {symState === 'SCOUT_MODE' ? 'SCOUT' : 'BASE'}
                    </span>
                  </div>
                </div>

                {/* Metric Telemetry Row */}
                <div className="grid grid-cols-3 gap-1 text-[11px] font-mono tabular-nums pt-0.5 border-t border-slate-800/60">
                  <div>
                    <span className="text-[9px] text-slate-500 block">Dźwignia</span>
                    <span className="font-bold text-blue-400">{Number(symLeverage).toFixed(1)}x</span>
                  </div>
                  <div>
                    <span className="text-[9px] text-slate-500 block">Alokacja</span>
                    <span className="font-bold text-gray-200">${Number(s.allocation_usd || 100).toFixed(0)}</span>
                  </div>
                  <div className="text-right">
                    <span className="text-[9px] text-slate-500 block">Mnożnik</span>
                    <span className={`font-bold ${isReduced ? 'text-amber-400' : 'text-slate-300'}`}>
                      {(Number(sizeMult) * 100).toFixed(0)}%
                    </span>
                  </div>
                </div>

                {/* Local Position Status & uPnL Indicator */}
                <div className="bg-slate-900/70 border border-slate-800/60 rounded p-1.5 space-y-1">
                  <div className="flex items-center justify-between text-[10px] font-mono tabular-nums">
                    <span className="text-slate-400">
                      {hasPosition ? (
                        <span className={`font-bold ${isLong ? 'text-emerald-400' : 'text-rose-400'}`}>
                          {side} · ${Number(livePos?.notional_usd || 0).toFixed(0)}
                        </span>
                      ) : (
                        <span className="text-slate-500">BRAK POZYCJI</span>
                      )}
                    </span>
                    <span className={`font-bold ${posUpnl >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                      {hasPosition ? formatSignedUsd(posUpnl) : '$0.00'}
                    </span>
                  </div>

                  {/* Dynamic TP / SL Progress Meter */}
                  <div className="space-y-0.5">
                    <div className="w-full h-1 bg-slate-800 rounded-full overflow-hidden flex">
                      <div
                        className={`h-full transition-all duration-300 ${
                          posUpnl >= 0 ? 'bg-emerald-400' : 'bg-rose-500'
                        }`}
                        style={{ width: `${hasPosition ? progressPct : 0}%` }}
                      />
                    </div>
                    <div className="flex justify-between text-[8px] font-mono text-slate-500">
                      <span>SL (-2%)</span>
                      <span>TP (Midline)</span>
                    </div>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* 3. TABELA ZLECEŃ NETTING (Execution Log / Order Book) */}
      <div className="bg-slate-900/95 border border-slate-800 rounded-xl p-3 sm:p-4 shadow-lg space-y-2.5">
        <div className="flex items-center justify-between border-b border-slate-800/80 pb-2">
          <div className="flex items-center gap-2">
            <Activity className="w-4 h-4 text-emerald-400" />
            <h3 className="font-mono font-bold text-gray-100 text-xs sm:text-sm uppercase tracking-wider">
              Tabela Zleceń Netting (Execution Order Book)
            </h3>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-[10px] font-mono bg-emerald-950/80 text-emerald-400 border border-emerald-500/30 px-2 py-0.5 rounded-full font-bold">
              {openPositions.length} Otwarte
            </span>
          </div>
        </div>

        <div className="overflow-x-auto">
          {openPositions && openPositions.length > 0 ? (
            <table className="w-full text-xs text-left text-slate-300">
              <thead className="bg-slate-950 text-slate-400 uppercase font-mono text-[10px] border-b border-slate-800 tracking-wider">
                <tr>
                  <th className="px-3 py-2 text-left">Para</th>
                  <th className="px-3 py-2 text-left">Strona</th>
                  <th className="px-3 py-2 text-right">Dźwignia</th>
                  <th className="px-3 py-2 text-right">Notional</th>
                  <th className="px-3 py-2 text-right">Margin</th>
                  <th className="px-3 py-2 text-right">Wejście</th>
                  <th className="px-3 py-2 text-right">Mark Px</th>
                  <th className="px-3 py-2 text-right">uPnL</th>
                  <th className="px-3 py-2 text-right">Inactivity</th>
                  <th className="px-3 py-2 text-right">Akcja</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/60 font-mono tabular-nums text-[11px]">
                {openPositions.map((p, idx) => {
                  const rawSym = p.symbol || p.raw_symbol || 'N/A';
                  const cleanSym = normalizeSym(rawSym);
                  const side = String(p.side || 'long').toUpperCase();
                  const isLong = side === 'BUY' || side === 'LONG';
                  const leverage = Number(p.leverage || 1.0);
                  const notional = Number(p.notional_usd || 0.0);
                  const margin = leverage > 0 ? notional / leverage : notional;
                  const entryPx = Number(p.entry_price || 0.0);
                  const markPx = Number(p.mark_price || 0.0);
                  const upnl = Number(p.unrealized_pnl || 0.0);
                  const isClosing = closingSym === cleanSym;

                  // Inactivity simulation check (stagnation highlight if applicable)
                  const isStagnant = p.is_stagnant || false;

                  return (
                    <tr
                      key={idx}
                      className={`hover:bg-slate-800/40 transition-colors ${
                        isStagnant ? 'bg-amber-950/25 border-l-2 border-amber-500' : ''
                      }`}
                    >
                      <td className="px-3 py-2.5 font-bold text-gray-100 text-left">{cleanSym}</td>
                      <td className="px-3 py-2.5 text-left">
                        <span className={`px-1.5 py-0.5 rounded font-bold text-[10px] border ${
                          isLong
                            ? 'bg-emerald-950 text-emerald-400 border-emerald-500/40'
                            : 'bg-rose-950 text-rose-400 border-rose-500/40'
                        }`}>
                          {side}
                        </span>
                      </td>
                      <td className="px-3 py-2.5 text-right text-blue-400 font-bold">{leverage.toFixed(0)}x</td>
                      <td className="px-3 py-2.5 text-right text-gray-200 font-bold">${notional.toFixed(2)}</td>
                      <td className="px-3 py-2.5 text-right text-amber-400 font-bold">${margin.toFixed(2)}</td>
                      <td className="px-3 py-2.5 text-right text-slate-300">
                        ${entryPx > 0 ? entryPx.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '---'}
                      </td>
                      <td className="px-3 py-2.5 text-right text-slate-200 font-bold">
                        ${markPx > 0 ? markPx.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '---'}
                      </td>
                      <td className="px-3 py-2.5 text-right">
                        <span className={`font-bold px-1.5 py-0.5 rounded text-[10px] border ${
                          upnl >= 0
                            ? 'bg-emerald-950/80 text-emerald-400 border-emerald-500/30'
                            : 'bg-rose-950/80 text-rose-400 border-rose-500/30'
                        }`}>
                          {formatSignedUsd(upnl)}
                        </span>
                      </td>
                      <td className="px-3 py-2.5 text-right">
                        {isStagnant ? (
                          <span className="text-amber-400 font-bold flex items-center justify-end gap-1 text-[10px]" title="Inactivity Manager: Stagnacja pozycji">
                            <Clock className="w-3 h-3" /> STAGNANT
                          </span>
                        ) : (
                          <span className="text-slate-500 text-[10px]">ACTIVE</span>
                        )}
                      </td>
                      <td className="px-3 py-2.5 text-right">
                        <button
                          onClick={() => setConfirmCloseTarget({ cleanSym, side, notional, margin, entryPx, markPx, upnl, isLong })}
                          disabled={isClosing}
                          className="px-2 py-1 bg-rose-600 hover:bg-rose-500 disabled:opacity-50 text-white rounded font-bold text-[10px] transition-colors inline-flex items-center gap-1 shadow-sm ml-auto"
                        >
                          <XCircle className="w-3 h-3" />
                          <span>{isClosing ? '...' : 'Zamknij'}</span>
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          ) : (
            <div className="text-center py-5 text-slate-500 text-xs bg-slate-950/40 rounded-lg border border-slate-800/60 font-mono">
              Brak otwartych pozycji netto w bieżącym cyklu giełdowym OKX.
            </div>
          )}
        </div>
      </div>

      {/* 4. HERMES KANBAN ACTIVE TASKS (Dense Stream) */}
      <div className="bg-slate-900/90 border border-slate-800 rounded-xl p-3 sm:p-4 shadow-sm space-y-2">
        <div className="flex items-center justify-between border-b border-slate-800/70 pb-2">
          <div className="flex items-center gap-2">
            <Zap className="w-4 h-4 text-amber-400" />
            <h4 className="font-mono font-bold text-xs text-gray-200 uppercase tracking-wider">
              Agent Hermes Kanban Stream
            </h4>
          </div>
          <span className="text-[10px] font-mono bg-blue-950 text-blue-300 border border-blue-500/30 px-2 py-0.5 rounded-full">
            {kanbanCards?.length || 0} zadań
          </span>
        </div>

        {kanbanCards && kanbanCards.length > 0 ? (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-2">
            {kanbanCards.map((card, idx) => (
              <div key={idx} className="bg-slate-950/70 border border-slate-800/80 rounded p-2.5 space-y-1 text-xs">
                <div className="flex items-center justify-between">
                  <span className="text-[9px] font-mono font-bold text-blue-400 bg-blue-950/60 px-1.5 py-0.2 rounded border border-blue-900">
                    {card.status || 'IN_PROGRESS'}
                  </span>
                  <span className="text-[9px] text-slate-500 font-mono">{card.created_at || 'Teraz'}</span>
                </div>
                <div className="text-xs font-semibold text-slate-200 truncate">{card.title || card.name}</div>
                {card.description && (
                  <p className="text-[10px] text-slate-400 line-clamp-1">{card.description}</p>
                )}
              </div>
            ))}
          </div>
        ) : (
          <div className="text-center py-3 text-slate-500 text-[11px] font-mono bg-slate-950/40 rounded border border-slate-800/40">
            Wszystkie zadania agenta Hermes zrealizowane.
          </div>
        )}
      </div>

      {/* Double Confirmation Close Modal */}
      {confirmCloseTarget && (
        <div className="fixed inset-0 bg-slate-950/80 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-slate-900 border border-slate-700 rounded-2xl p-5 max-w-md w-full shadow-2xl space-y-4 animate-in fade-in zoom-in-95 duration-150">
            <div className="flex items-center gap-3 border-b border-slate-800 pb-3">
              <div className="p-2.5 bg-rose-950/80 border border-rose-500/30 rounded-xl text-rose-400">
                <ShieldAlert className="w-5 h-5" />
              </div>
              <div>
                <h3 className="font-bold text-gray-100 text-base">Autoryzacja Zamknięcia Pozycji</h3>
                <p className="text-xs text-slate-400">Natychmiastowe zlecenie rynkowe do giełdy OKX</p>
              </div>
            </div>

            <div className="bg-slate-950 p-3 rounded-xl border border-slate-800 space-y-1.5 text-xs font-mono tabular-nums">
              <div className="flex justify-between items-center py-0.5 border-b border-slate-800/60">
                <span className="text-slate-400">Para:</span>
                <span className="font-bold text-gray-100">{confirmCloseTarget.cleanSym}</span>
              </div>
              <div className="flex justify-between items-center py-0.5 border-b border-slate-800/60">
                <span className="text-slate-400">Strona:</span>
                <span className={`px-1.5 py-0.2 rounded font-bold text-[10px] ${
                  confirmCloseTarget.isLong ? 'bg-emerald-950 text-emerald-400' : 'bg-rose-950 text-rose-400'
                }`}>
                  {confirmCloseTarget.side}
                </span>
              </div>
              <div className="flex justify-between items-center py-0.5 border-b border-slate-800/60">
                <span className="text-slate-400">Notional:</span>
                <span className="font-bold text-gray-200">${confirmCloseTarget.notional?.toFixed(2)} USD</span>
              </div>
              <div className="flex justify-between items-center py-0.5">
                <span className="text-slate-400">uPnL:</span>
                <span className={`font-bold ${confirmCloseTarget.upnl >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                  {formatSignedUsd(confirmCloseTarget.upnl)}
                </span>
              </div>
            </div>

            <div className="flex items-center gap-2 pt-1">
              <button
                onClick={() => setConfirmCloseTarget(null)}
                className="w-1/2 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg font-bold text-xs transition-colors"
              >
                Anuluj
              </button>
              <button
                onClick={() => executeClosePosition(confirmCloseTarget.cleanSym, confirmCloseTarget.side)}
                disabled={closingSym === confirmCloseTarget.cleanSym}
                className="w-1/2 py-2 bg-rose-600 hover:bg-rose-500 disabled:opacity-50 text-white rounded-lg font-bold text-xs transition-colors flex items-center justify-center gap-1 shadow-lg shadow-rose-600/30"
              >
                <XCircle className="w-4 h-4" />
                <span>{closingSym === confirmCloseTarget.cleanSym ? 'Zamykam...' : 'Potwierdzam'}</span>
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
