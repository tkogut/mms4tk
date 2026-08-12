import React from 'react';
import { Activity, Wallet, ShieldAlert, CheckCircle2, RefreshCw, Zap, TrendingUp, AlertTriangle } from 'lucide-react';

export function MonitoringTab({ stateData, statsData, kanbanCards, onRefresh }) {
  const isScoutMode = stateData?.scout_mode || false;
  const activeMode = stateData?.mode || (isScoutMode ? 'SCOUT_MODE' : 'BASE_MODE');
  const leverage = stateData?.leverage || 3.0;
  const stopLoss = stateData?.stop_loss_pct || 1.25;

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
            className="flex items-center gap-2 px-4 py-2.5 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-lg text-sm font-medium transition-colors border border-slate-700"
          >
            <RefreshCw className="w-4 h-4 text-blue-400" />
            <span>Odśwież Dane</span>
          </button>
        </div>
      </div>

      {/* Grid of Key Metrics Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
          <div className="flex items-center justify-between text-slate-400 mb-2">
            <span className="text-xs font-medium">Kapitał Wolny (USDC)</span>
            <Wallet className="w-4 h-4 text-emerald-400" />
          </div>
          <div className="text-2xl font-bold text-gray-100 font-mono">
            ${statsData?.free_balance !== undefined ? Number(statsData.free_balance).toFixed(2) : '---'}
          </div>
          <div className="text-xs text-slate-400 mt-1">Dostępny w portfelu SPOT</div>
        </div>

        <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
          <div className="flex items-center justify-between text-slate-400 mb-2">
            <span className="text-xs font-medium">Pozycja BTC</span>
            <TrendingUp className="w-4 h-4 text-blue-400" />
          </div>
          <div className="text-2xl font-bold text-gray-100 font-mono">
            {statsData?.btc_balance !== undefined ? Number(statsData.btc_balance).toFixed(5) : '---'} BTC
          </div>
          <div className="text-xs text-slate-400 mt-1">Rynek OKX Spot BTC/USDC</div>
        </div>

        <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
          <div className="flex items-center justify-between text-slate-400 mb-2">
            <span className="text-xs font-medium">Dźwignia Rynkowa</span>
            <Zap className="w-4 h-4 text-amber-400" />
          </div>
          <div className="text-2xl font-bold text-gray-100 font-mono">
            {leverage.toFixed(1)}x
          </div>
          <div className="text-xs text-slate-400 mt-1">Dźwignia docelowa alokatora</div>
        </div>

        <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
          <div className="flex items-center justify-between text-slate-400 mb-2">
            <span className="text-xs font-medium">Stop Loss (SL)</span>
            <ShieldAlert className="w-4 h-4 text-red-400" />
          </div>
          <div className="text-2xl font-bold text-gray-100 font-mono">
            {stopLoss.toFixed(2)}%
          </div>
          <div className="text-xs text-slate-400 mt-1">Maksymalny próg ryzyka świecy</div>
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
          <div className="text-center py-8 text-slate-500 text-sm bg-slate-950/40 rounded-lg border border-slate-800/60">
            Brak aktywnych zadań w kolejce Hermes Kanban. Wszystkie systemy działają nominalnie.
          </div>
        )}
      </div>
    </div>
  );
}
