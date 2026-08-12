import React, { useState } from 'react';
import { Coins, Plus, X, CheckCircle, Shield, ArrowUpRight } from 'lucide-react';

export function SymbolsTab() {
  const [symbols, setSymbols] = useState([
    { name: 'BTC/USDC', type: 'SPOT', status: 'ACTIVE', leverage: '3.0x', exchange: 'OKX Global' },
    { name: 'ETH/USDC', type: 'SPOT', status: 'WATCHLIST', leverage: '1.0x', exchange: 'OKX Global' },
    { name: 'SOL/USDC', type: 'SPOT', status: 'WATCHLIST', leverage: '1.0x', exchange: 'OKX Global' },
  ]);

  const [showModal, setShowModal] = useState(false);
  const [newSymbol, setNewSymbol] = useState('');
  const [newLeverage, setNewLeverage] = useState('1.0x');

  const handleAddSymbol = (e) => {
    e.preventDefault();
    if (!newSymbol) return;
    setSymbols([...symbols, {
      name: newSymbol.toUpperCase(),
      type: 'SPOT',
      status: 'WATCHLIST',
      leverage: newLeverage,
      exchange: 'OKX Global'
    }]);
    setNewSymbol('');
    setShowModal(false);
  };

  return (
    <div className="space-y-6">
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 shadow-lg">
        <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 mb-6">
          <div className="flex items-center gap-3">
            <Coins className="w-6 h-6 text-amber-400" />
            <div>
              <h3 className="font-bold text-gray-100">Obsługiwane Pary Rynkowe (OKX SPOT)</h3>
              <p className="text-xs text-slate-400">Pary aktywne oraz znajdujące się na liście obserwacyjnej algorytmu</p>
            </div>
          </div>

          <button
            onClick={() => setShowModal(true)}
            className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white rounded-lg text-xs font-semibold transition-colors shadow-md shadow-blue-500/20"
          >
            <Plus className="w-4 h-4" />
            <span>Dodaj Nową Parę</span>
          </button>
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

      {/* Add Symbol Modal */}
      {showModal && (
        <div className="fixed inset-0 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4 z-50">
          <div className="bg-slate-900 border border-slate-800 rounded-xl max-w-md w-full p-6 space-y-4 shadow-2xl">
            <div className="flex items-center justify-between pb-3 border-b border-slate-800">
              <h3 className="text-base font-bold text-gray-100 flex items-center gap-2">
                <Plus className="w-5 h-5 text-blue-400" />
                <span>Dodaj Parę do Obserwacji</span>
              </h3>
              <button 
                onClick={() => setShowModal(false)}
                className="text-slate-400 hover:text-white p-1"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <form onSubmit={handleAddSymbol} className="space-y-4">
              <div>
                <label className="block text-xs font-semibold text-slate-300 mb-1.5">
                  Nazwa Pary (np. DOGE/USDC, SUI/USDC):
                </label>
                <input
                  type="text"
                  placeholder="np. DOGE/USDC"
                  value={newSymbol}
                  onChange={(e) => setNewSymbol(e.target.value)}
                  className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-lg text-sm text-slate-200 focus:outline-none focus:border-blue-500 font-mono"
                  required
                />
              </div>

              <div>
                <label className="block text-xs font-semibold text-slate-300 mb-1.5">
                  Docelowa Dźwignia Strategii:
                </label>
                <select
                  value={newLeverage}
                  onChange={(e) => setNewLeverage(e.target.value)}
                  className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-lg text-sm text-slate-200 focus:outline-none focus:border-blue-500 font-mono"
                >
                  <option value="1.0x">1.0x (Bezpieczny SPOT)</option>
                  <option value="2.0x">2.0x</option>
                  <option value="3.0x">3.0x (Standard)</option>
                  <option value="5.0x">5.0x</option>
                  <option value="10.0x">10.0x (Maksymalny)</option>
                </select>
              </div>

              <div className="flex justify-end gap-3 pt-2">
                <button
                  type="button"
                  onClick={() => setShowModal(false)}
                  className="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg text-xs font-medium"
                >
                  Anuluj
                </button>
                <button
                  type="submit"
                  className="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white rounded-lg text-xs font-semibold shadow-md"
                >
                  Dodaj Parę
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
