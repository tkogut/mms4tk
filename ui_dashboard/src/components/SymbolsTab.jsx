import React from 'react';
import { Coins, CheckCircle, Shield, ArrowUpRight } from 'lucide-react';

export function SymbolsTab() {
  const symbols = [
    { name: 'BTC/USDC', type: 'SPOT', status: 'ACTIVE', leverage: '3.0x', exchange: 'OKX Global' },
    { name: 'ETH/USDC', type: 'SPOT', status: 'WATCHLIST', leverage: '1.0x', exchange: 'OKX Global' },
    { name: 'SOL/USDC', type: 'SPOT', status: 'WATCHLIST', leverage: '1.0x', exchange: 'OKX Global' },
  ];

  return (
    <div className="space-y-6">
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 shadow-lg">
        <div className="flex items-center gap-3 mb-4">
          <Coins className="w-6 h-6 text-amber-400" />
          <div>
            <h3 className="font-bold text-gray-100">Obsługiwane Pary Rynkowe (OKX SPOT)</h3>
            <p className="text-xs text-slate-400">Pary aktywne oraz znajdujące się na liście obserwacyjnej algorytmu</p>
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {symbols.map((sym, idx) => (
            <div key={idx} className="bg-slate-950 border border-slate-800 rounded-xl p-4 space-y-3">
              <div className="flex items-center justify-between">
                <span className="font-bold text-base text-gray-100">{sym.name}</span>
                <span className={`text-[10px] font-bold px-2 py-0.5 rounded border font-mono ${
                  sym.status === 'ACTIVE' 
                    ? 'bg-emerald-950 text-emerald-400 border-emerald-500/30' 
                    : 'bg-slate-800 text-slate-400 border-slate-700'
                }`}>
                  {sym.status}
                </span>
              </div>

              <div className="space-y-1 text-xs text-slate-400">
                <div className="flex justify-between">
                  <span>Rynek:</span>
                  <span className="text-slate-200 font-medium">{sym.type}</span>
                </div>
                <div className="flex justify-between">
                  <span>Giełda:</span>
                  <span className="text-slate-200 font-medium">{sym.exchange}</span>
                </div>
                <div className="flex justify-between">
                  <span>Dźwignia Strategii:</span>
                  <span className="text-blue-400 font-mono font-bold">{sym.leverage}</span>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
