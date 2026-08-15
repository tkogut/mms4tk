import React from 'react';
import { Cpu, ShieldAlert, CheckCircle2, RefreshCw } from 'lucide-react';

export function Header({ version = "0.23.4", isConnected, stateData, onRefresh }) {
  const isScoutMode = stateData?.state === 'SCOUT_MODE' || stateData?.scout_mode || false;
  const activeMode = stateData?.state || (isScoutMode ? 'SCOUT_MODE' : 'BASE_MODE');

  const handleForceRefresh = async () => {
    try {
      if ('serviceWorker' in navigator) {
        const regs = await navigator.serviceWorker.getRegistrations();
        for (let reg of regs) {
          await reg.unregister();
        }
      }
      if ('caches' in window) {
        const names = await caches.keys();
        for (let name of names) {
          await caches.delete(name);
        }
      }
    } catch (e) {
      console.warn('Cache clear error:', e);
    }
    window.location.reload(true);
  };

  return (
    <header className="mb-6 flex flex-col md:flex-row items-center justify-between gap-4 pb-4 border-b border-slate-800">
      <div className="flex items-center gap-3 text-center sm:text-left">
        <div className="p-3 bg-emerald-500/10 border border-emerald-500/30 rounded-xl text-emerald-400">
          <Cpu className="w-7 h-7" />
        </div>
        <div>
          <h1 className="text-xl sm:text-2xl font-bold bg-gradient-to-r from-gray-100 via-gray-200 to-gray-400 bg-clip-text text-transparent">
            🧠 Mastermind Control Center
          </h1>
          <p className="text-xs text-slate-400 flex items-center justify-center sm:justify-start gap-2 mt-0.5">
            <span>VPS Autotrading Engine & Strategy Supervisor</span>
            <span className="flex items-center gap-1.5 px-2 py-0.5 bg-slate-800 text-emerald-400 font-mono text-[10px] rounded-full border border-emerald-500/30">
              <span>v{version || '0.23.4'}</span>
              <button
                onClick={handleForceRefresh}
                title="Wymuś czyszczenie cache PWA i odśwież wersję"
                className="hover:text-white transition-colors"
              >
                <RefreshCw className="w-3 h-3 hover:rotate-180 transition-transform" />
              </button>
            </span>
          </p>
        </div>
      </div>

      <div className="flex flex-wrap items-center justify-center sm:justify-end gap-3">
        {/* Tryb Operacyjny Bota */}
        <div className="flex items-center gap-2 bg-slate-900 border border-slate-800 rounded-lg px-3 py-1.5 shadow-sm">
          <div className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider hidden sm:inline">Tryb:</div>
          <div className={`flex items-center gap-1.5 px-2.5 py-0.5 rounded font-mono text-xs font-bold border ${
            isScoutMode
              ? 'bg-amber-950/60 border-amber-500/40 text-amber-300'
              : 'bg-emerald-950/60 border-emerald-500/40 text-emerald-300'
          }`}>
            {isScoutMode ? <ShieldAlert className="w-3.5 h-3.5 text-amber-400" /> : <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />}
            <span>{activeMode}</span>
          </div>
        </div>

        {/* API Live Status */}
        <div className={`flex items-center gap-2 px-3 py-1.5 rounded-lg border text-xs font-medium ${
          isConnected 
            ? 'bg-emerald-950/40 border-emerald-500/30 text-emerald-400' 
            : 'bg-red-950/40 border-red-500/30 text-red-400'
        }`}>
          <span className={`w-2 h-2 rounded-full ${isConnected ? 'bg-emerald-400 animate-pulse' : 'bg-red-400'}`} />
          <span>{isConnected ? 'API LIVE' : 'DISCONNECTED'}</span>
        </div>

        {/* Global Refresh Button */}
        {onRefresh && (
          <button
            onClick={onRefresh}
            title="Odśwież dane z API"
            className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-lg text-xs font-medium transition-colors border border-slate-700 shadow-sm"
          >
            <RefreshCw className="w-3.5 h-3.5 text-blue-400" />
            <span>Odśwież</span>
          </button>
        )}
      </div>
    </header>
  );
}

