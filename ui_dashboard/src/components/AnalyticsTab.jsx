import React from 'react';
import { BarChart3, TrendingUp, DollarSign, Activity } from 'lucide-react';
import { ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip, CartesianGrid, LineChart, Line } from 'recharts';

export function AnalyticsTab({ analyticsData }) {
  // Sample/Real candles for TMA chart
  const candles = analyticsData?.candles || [
    { time: '12:00', price: 96200, tma: 96150, upper: 96800, lower: 95500 },
    { time: '13:00', price: 96500, tma: 96300, upper: 96950, lower: 95650 },
    { time: '14:00', price: 96100, tma: 96250, upper: 96900, lower: 95600 },
    { time: '15:00', price: 96800, tma: 96400, upper: 97100, lower: 95700 },
    { time: '16:00', price: 97300, tma: 96600, upper: 97350, lower: 95850 },
  ];

  const pnlHistory = analyticsData?.pnl_history || [
    { date: '08-08', balance: 100 },
    { date: '08-09', balance: 102.5 },
    { date: '08-10', balance: 101.8 },
    { date: '08-11', balance: 104.2 },
    { date: '08-12', balance: 107.0 },
  ];

  return (
    <div className="space-y-6">
      {/* Charts Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* TMA Bands & BTC Price Chart */}
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 shadow-lg">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-2">
              <Activity className="w-5 h-5 text-emerald-400" />
              <h3 className="font-semibold text-gray-200">Wskaźnik TMA & Ścieżka Ceny BTC</h3>
            </div>
            <span className="text-xs text-slate-400 font-mono">OKX Spot BTC/USDC</span>
          </div>

          <div className="h-64 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={candles}>
                <defs>
                  <linearGradient id="priceGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#10b981" stopOpacity={0.3}/>
                    <stop offset="95%" stopColor="#10b981" stopOpacity={0}/>
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                <XAxis dataKey="time" stroke="#64748b" fontSize={11} />
                <YAxis domain={['auto', 'auto']} stroke="#64748b" fontSize={11} />
                <Tooltip 
                  contentStyle={{ backgroundColor: '#0f172a', borderColor: '#334155', borderRadius: '8px', color: '#fff' }}
                />
                <Area type="monotone" dataKey="price" stroke="#10b981" fillOpacity={1} fill="url(#priceGrad)" strokeWidth={2} name="Cena BTC" />
                <Line type="monotone" dataKey="tma" stroke="#3b82f6" strokeWidth={1.5} dot={false} name="Średnia TMA" />
                <Line type="monotone" dataKey="upper" stroke="#ef4444" strokeWidth={1} strokeDasharray="3 3" dot={false} name="Górne Pasmo" />
                <Line type="monotone" dataKey="lower" stroke="#f59e0b" strokeWidth={1} strokeDasharray="3 3" dot={false} name="Dolne Pasmo" />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* Equity / PnL Progression */}
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 shadow-lg">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-2">
              <TrendingUp className="w-5 h-5 text-blue-400" />
              <h3 className="font-semibold text-gray-200">Krzywa Salda Portfela (USDC)</h3>
            </div>
            <span className="text-xs text-emerald-400 font-bold bg-emerald-950/60 px-2 py-0.5 rounded border border-emerald-500/30">
              +7.0% Total ROI
            </span>
          </div>

          <div className="h-64 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={pnlHistory}>
                <defs>
                  <linearGradient id="pnlGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.3}/>
                    <stop offset="95%" stopColor="#3b82f6" stopOpacity={0}/>
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                <XAxis dataKey="date" stroke="#64748b" fontSize={11} />
                <YAxis domain={['auto', 'auto']} stroke="#64748b" fontSize={11} />
                <Tooltip 
                  contentStyle={{ backgroundColor: '#0f172a', borderColor: '#334155', borderRadius: '8px', color: '#fff' }}
                />
                <Area type="monotone" dataKey="balance" stroke="#3b82f6" fillOpacity={1} fill="url(#pnlGrad)" strokeWidth={2.5} name="Saldo USDC" />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>

      {/* Strategy Performance Summary */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
        <h3 className="font-semibold text-gray-200 mb-3">📊 Wykaz Sygnałów Strategicznych</h3>
        <div className="overflow-x-auto">
          <table className="w-full text-xs text-left text-slate-300">
            <thead className="bg-slate-950 text-slate-400 uppercase font-mono border-b border-slate-800">
              <tr>
                <th className="px-4 py-2.5">Czas</th>
                <th className="px-4 py-2.5">Para</th>
                <th className="px-4 py-2.5">Typ Sygnału</th>
                <th className="px-4 py-2.5">Cena Wejścia</th>
                <th className="px-4 py-2.5">Dźwignia</th>
                <th className="px-4 py-2.5">Wynik</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/60">
              <tr className="hover:bg-slate-800/30">
                <td className="px-4 py-3 font-mono">2026-08-12 14:32</td>
                <td className="px-4 py-3 font-bold text-gray-200">BTC/USDC</td>
                <td className="px-4 py-3">
                  <span className="px-2 py-0.5 rounded bg-emerald-950 text-emerald-400 border border-emerald-500/30 font-bold">BUY SPOT</span>
                </td>
                <td className="px-4 py-3 font-mono">$96,450.00</td>
                <td className="px-4 py-3 font-mono">3.0x</td>
                <td className="px-4 py-3 font-bold text-emerald-400">+1.25%</td>
              </tr>
              <tr className="hover:bg-slate-800/30">
                <td className="px-4 py-3 font-mono">2026-08-11 18:10</td>
                <td className="px-4 py-3 font-bold text-gray-200">BTC/USDC</td>
                <td className="px-4 py-3">
                  <span className="px-2 py-0.5 rounded bg-amber-950 text-amber-400 border border-amber-500/30 font-bold">SCOUT TAKE</span>
                </td>
                <td className="px-4 py-3 font-mono">$95,800.00</td>
                <td className="px-4 py-3 font-mono">1.0x</td>
                <td className="px-4 py-3 font-bold text-emerald-400">+0.85%</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
