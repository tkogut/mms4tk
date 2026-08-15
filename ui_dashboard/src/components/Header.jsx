import React from 'react';
import { Cpu, ShieldCheck, Activity, RefreshCw } from 'lucide-react';

export function Header({ version = "0.21.4", isConnected }) {
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
    <header className="mb-6 flex flex-col sm:flex-row items-center justify-between gap-4 pb-4 border-b border-slate-800">
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
              <span>v{version || '0.21.2'}</span>
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

      <div className="flex items-center gap-3">
        <div className={`flex items-center gap-2 px-3 py-1.5 rounded-lg border text-xs font-medium ${
          isConnected 
            ? 'bg-emerald-950/40 border-emerald-500/30 text-emerald-400' 
            : 'bg-red-950/40 border-red-500/30 text-red-400'
        }`}>
          <span className={`w-2 h-2 rounded-full ${isConnected ? 'bg-emerald-400 animate-pulse' : 'bg-red-400'}`} />
          <span>{isConnected ? 'API LIVE' : 'DISCONNECTED'}</span>
        </div>

        <div className="flex items-center gap-2 px-3 py-1.5 bg-slate-900 border border-slate-800 rounded-lg text-xs text-slate-300">
          <ShieldCheck className="w-4 h-4 text-blue-400" />
          <span>OKX SPOT BTC/USDC</span>
        </div>
      </div>
    </header>
  );
}
