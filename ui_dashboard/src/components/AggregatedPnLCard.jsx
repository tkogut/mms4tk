import React, { useMemo } from 'react';
import { TrendingUp, TrendingDown, Activity, Wallet, Percent, AlertOctagon, Scale, Zap, Award } from 'lucide-react';
import { formatSignedUsd } from '../utils/formatters';

export function AggregatedPnLCard({ statsData, openPositions = [], tradesHistory = [] }) {
  // Financial computations
  const realizedPnl = Number(statsData?.realized_pnl ?? statsData?.realized_gross_pnl_usd ?? 0);
  const netRealizedPnl = Number(statsData?.realized_net_pnl_usd ?? (realizedPnl - Number(statsData?.total_fees_usd || 0)));
  const totalFees = Number(statsData?.total_fees_usd || 0);

  const totalUnrealizedPnl = (openPositions || []).reduce((acc, p) => acc + Number(p.unrealized_pnl || 0), 0);
  const totalNotionalUsd = (openPositions || []).reduce((acc, p) => acc + Number(p.notional_usd || 0), 0);
  const totalMarginUsd = (openPositions || []).reduce((acc, p) => {
    const lev = Number(p.leverage) || 1;
    return acc + (Number(p.notional_usd || 0) / lev);
  }, 0);

  // Net of fees: the hero number must be what the account actually earned.
  const totalCombinedPnl = netRealizedPnl + totalUnrealizedPnl;
  const freeBalance = Number(statsData?.free_balance ?? statsData?.free_margin ?? 0);
  const totalEquity = freeBalance + totalMarginUsd + totalUnrealizedPnl;

  const totalTrades = statsData?.total_trades || 0;
  const hasTraded = totalTrades > 0;
  const winRateSource = statsData?.net_win_rate_pct ?? statsData?.win_rate;
  const winRate = hasTraded && winRateSource !== undefined ? Number(winRateSource).toFixed(1) : '0.0';
  const profitFactorSource = statsData?.profit_factor_net ?? statsData?.profit_factor;
  const profitFactor = hasTraded && profitFactorSource !== undefined ? Number(profitFactorSource).toFixed(2) : '0.00';
  const maxDdSource = statsData?.max_drawdown_net_usd ?? statsData?.max_drawdown_usd ?? statsData?.max_drawdown;
  const maxDdUsd = hasTraded && maxDdSource !== undefined ? Number(maxDdSource).toFixed(2) : '0.00';
  const maxDdPct = hasTraded && Number(statsData?.max_drawdown_pct || 0) > 0
    ? Number(statsData.max_drawdown_pct).toFixed(2)
    : null;
  const avgRrSource = statsData?.average_rr_net ?? statsData?.average_rr;
  const avgRr = hasTraded && avgRrSource !== undefined ? Number(avgRrSource).toFixed(2) : '0.00';
  const isMock = Boolean(statsData?.is_mock);

  // Return on capital % estimation
  const returnPct = totalEquity > 0
    ? ((totalCombinedPnl / (totalEquity - totalCombinedPnl > 0 ? (totalEquity - totalCombinedPnl) : totalEquity)) * 100)
    : 0;

  const isProfitable = totalCombinedPnl >= 0;
  const pnlColor = isProfitable ? 'text-emerald-400' : 'text-rose-400';
  const pnlGlow = isProfitable ? 'drop-shadow-[0_0_15px_rgba(16,185,129,0.25)]' : 'drop-shadow-[0_0_15px_rgba(244,63,94,0.25)]';
  const badgeBg = isProfitable ? 'bg-emerald-950/80 border-emerald-500/40 text-emerald-300' : 'bg-rose-950/80 border-rose-500/40 text-rose-300';

  // Mini Sparkline Generator (SVG Path)
  const sparklineData = useMemo(() => {
    let points = [];
    if (tradesHistory && tradesHistory.length > 0) {
      // Sort chronological ASC
      const sorted = [...tradesHistory].sort((a, b) => (new Date(a.timestamp).getTime() || 0) - (new Date(b.timestamp).getTime() || 0));
      let cum = 0;
      points = sorted.map((t) => {
        const net = t.pnl_net_usd !== undefined && t.pnl_net_usd !== null ? Number(t.pnl_net_usd) : Number(t.pnl_usd || 0);
        cum += net;
        return cum;
      });
    }

    if (points.length < 2) {
      // Synthesize 4 anchor points if trades history is scarce
      points = [0, netRealizedPnl * 0.4, netRealizedPnl * 0.8, totalCombinedPnl];
    }

    const min = Math.min(...points, 0);
    const max = Math.max(...points, 0);
    const range = max - min || 1;
    const width = 140;
    const height = 40;

    const coords = points.map((val, idx) => {
      const x = (idx / (points.length - 1)) * (width - 6) + 3;
      const y = height - 4 - ((val - min) / range) * (height - 8);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    });

    const pathD = coords.length > 0 ? `M ${coords.join(' L ')}` : '';
    const areaD = coords.length > 0 ? `M ${coords.join(' L ')} L ${width - 3},${height} L 3,${height} Z` : '';

    return { pathD, areaD, isUp: (points[points.length - 1] >= points[0]) };
  }, [tradesHistory, netRealizedPnl, totalCombinedPnl]);

  return (
    <div className="bg-slate-900/95 border border-slate-800 rounded-xl p-3 sm:p-4 shadow-xl backdrop-blur-md">
      {/* Top Hero Row: Main PnL + Sparkline + Live Exposure Box */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-3 items-center">
        {/* Left Column (Main PnL + Dynamics): 7 cols on desktop */}
        <div className="lg:col-span-7 flex flex-col sm:flex-row sm:items-center justify-between gap-3 bg-slate-950/70 border border-slate-800/80 rounded-lg p-3 sm:p-3.5">
          <div className="space-y-0.5">
            <div className="flex items-center gap-2">
              <span className="text-[10px] sm:text-xs uppercase tracking-wider text-slate-400 font-mono font-semibold flex items-center gap-1.5">
                <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
                Aggregated Portfolio PnL (Netto + uPnL)
              </span>
              {isMock && (
                <span className="text-[9px] font-mono bg-amber-950 text-amber-300 border border-amber-500/40 px-1.5 py-0.2 rounded font-bold">
                  MOCK
                </span>
              )}
            </div>

            {/* Dominant Hero Value */}
            <div className="flex items-baseline gap-3 flex-wrap">
              <div className={`text-3xl sm:text-5xl font-black font-mono tabular-nums tracking-tight ${pnlColor} ${pnlGlow}`}>
                {formatSignedUsd(totalCombinedPnl)}
              </div>

              {/* Dynamic Percent Badge */}
              <div className={`flex items-center gap-1 px-2 py-0.5 rounded text-xs font-mono font-bold border ${badgeBg}`}>
                {isProfitable ? <TrendingUp className="w-3.5 h-3.5" /> : <TrendingDown className="w-3.5 h-3.5" />}
                <span>{returnPct >= 0 ? `+${returnPct.toFixed(2)}%` : `${returnPct.toFixed(2)}%`}</span>
              </div>
            </div>

            <div className="text-[10px] text-slate-400 font-mono flex items-center gap-2 pt-0.5">
              <span>Realized netto: <b className="text-slate-200">${netRealizedPnl.toFixed(2)}</b></span>
              <span>·</span>
              <span>Brutto: <b className="text-slate-400">${realizedPnl.toFixed(2)}</b></span>
              <span>·</span>
              <span>Prowizje: <b className="text-slate-400">-${totalFees.toFixed(2)}</b></span>
              <span>·</span>
              <span>Kapitał: <b className="text-slate-200">${totalEquity.toFixed(2)}</b></span>
            </div>
          </div>

          {/* Sparkline Graphic SVG */}
          <div className="shrink-0 flex sm:flex-col items-center sm:items-end justify-between sm:justify-center border-t sm:border-t-0 sm:border-l border-slate-800/80 pt-2 sm:pt-0 sm:pl-3">
            <span className="text-[9px] text-slate-500 font-mono uppercase tracking-widest sm:mb-1">
              Equity Trend
            </span>
            <svg width="130" height="38" className="overflow-visible">
              <defs>
                <linearGradient id="pnlSparklineGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={sparklineData.isUp ? '#10b981' : '#f43f5e'} stopOpacity="0.4" />
                  <stop offset="100%" stopColor={sparklineData.isUp ? '#10b981' : '#f43f5e'} stopOpacity="0.0" />
                </linearGradient>
              </defs>
              {sparklineData.areaD && (
                <path d={sparklineData.areaD} fill="url(#pnlSparklineGrad)" />
              )}
              {sparklineData.pathD && (
                <path
                  d={sparklineData.pathD}
                  fill="none"
                  stroke={sparklineData.isUp ? '#10b981' : '#f43f5e'}
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              )}
            </svg>
          </div>
        </div>

        {/* Right Column (Live Open Positions Exposure Spotlight): 5 cols on desktop */}
        <div className="lg:col-span-5 bg-slate-950/70 border border-slate-800/80 rounded-lg p-3 sm:p-3.5 space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-[10px] sm:text-xs uppercase tracking-wider text-slate-400 font-mono font-semibold flex items-center gap-1.5">
              <Activity className="w-3.5 h-3.5 text-blue-400" />
              Live Market Exposure
            </span>
            <span className="text-[10px] font-mono bg-blue-950 text-blue-300 border border-blue-500/30 px-2 py-0.5 rounded-full font-bold">
              {openPositions.length} {openPositions.length === 1 ? 'POZYCJA' : 'POZYCJE'}
            </span>
          </div>

          <div className="flex items-baseline justify-between">
            <div>
              <span className="text-[10px] text-slate-500 font-mono block">Niezrealizowany uPnL:</span>
              <span className={`text-xl sm:text-2xl font-bold font-mono tabular-nums tracking-tight ${
                totalUnrealizedPnl >= 0 ? 'text-emerald-400' : 'text-rose-400'
              }`}>
                {formatSignedUsd(totalUnrealizedPnl)}
              </span>
            </div>
            <div className="text-right">
              <span className="text-[10px] text-slate-500 font-mono block">Wycena / Notional:</span>
              <span className="text-sm sm:text-base font-bold font-mono tabular-nums text-slate-200">
                ${totalNotionalUsd.toFixed(2)} <span className="text-[10px] text-slate-400 font-normal">USD</span>
              </span>
            </div>
          </div>

          {/* Visual Exposure Bar: Used Margin vs Available Margin */}
          <div className="space-y-1 pt-1 border-t border-slate-800/60">
            <div className="flex justify-between text-[10px] font-mono tabular-nums">
              <span className="text-slate-400">
                Margin: <b className="text-amber-400">${totalMarginUsd.toFixed(2)}</b>
              </span>
              <span className="text-slate-400">
                Wolny Depozyt: <b className="text-emerald-400">${freeBalance.toFixed(2)}</b>
              </span>
            </div>
            <div className="w-full h-1.5 bg-slate-800 rounded-full overflow-hidden flex">
              <div
                className="bg-amber-400 transition-all duration-300"
                style={{
                  width: `${Math.min(100, totalEquity > 0 ? (totalMarginUsd / totalEquity) * 100 : 0)}%`
                }}
              />
              <div
                className="bg-emerald-500 transition-all duration-300"
                style={{
                  width: `${Math.max(0, Math.min(100, totalEquity > 0 ? (freeBalance / totalEquity) * 100 : 100))}%`
                }}
              />
            </div>
          </div>
        </div>
      </div>

      {/* Integrated Compressed Secondary Metrics Ribbon (Zero-Gutter) */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2 pt-2.5 mt-2.5 border-t border-slate-800/80">
        <div className="bg-slate-950/60 border border-slate-800/60 rounded px-2.5 py-1.5 flex items-center justify-between">
          <div className="text-[10px] text-slate-400 font-mono">Wolny Depozyt</div>
          <div className="text-xs font-bold font-mono tabular-nums text-emerald-400 text-right">
            ${freeBalance.toFixed(2)}
          </div>
        </div>

        <div className="bg-slate-950/60 border border-slate-800/60 rounded px-2.5 py-1.5 flex items-center justify-between">
          <div className="text-[10px] text-slate-400 font-mono">Realized PnL (netto)</div>
          <div className={`text-xs font-bold font-mono tabular-nums text-right ${netRealizedPnl >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
            {formatSignedUsd(netRealizedPnl)}
          </div>
        </div>

        <div className="bg-slate-950/60 border border-slate-800/60 rounded px-2.5 py-1.5 flex items-center justify-between">
          <div className="text-[10px] text-slate-400 font-mono">Win Rate (netto)</div>
          <div className="text-xs font-bold font-mono tabular-nums text-emerald-400 text-right">
            {hasTraded ? `${winRate}%` : '0.0%'}
          </div>
        </div>

        <div className="bg-slate-950/60 border border-slate-800/60 rounded px-2.5 py-1.5 flex items-center justify-between">
          <div className="text-[10px] text-slate-400 font-mono">Profit Factor (netto)</div>
          <div className="text-xs font-bold font-mono tabular-nums text-blue-400 text-right">
            {hasTraded ? profitFactor : '0.00'}
          </div>
        </div>

        <div className="bg-slate-950/60 border border-slate-800/60 rounded px-2.5 py-1.5 flex items-center justify-between">
          <div className="text-[10px] text-slate-400 font-mono">Max Drawdown (netto)</div>
          <div className="text-xs font-bold font-mono tabular-nums text-rose-400 text-right">
            {hasTraded ? `$${maxDdUsd}` : '$0.00'}
          </div>
        </div>

        <div className="bg-slate-950/60 border border-slate-800/60 rounded px-2.5 py-1.5 flex items-center justify-between">
          <div className="text-[10px] text-slate-400 font-mono">Transakcje</div>
          <div className="text-xs font-bold font-mono tabular-nums text-gray-200 text-right">
            {totalTrades}
          </div>
        </div>
      </div>
    </div>
  );
}
