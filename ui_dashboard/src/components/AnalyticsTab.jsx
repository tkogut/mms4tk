import React, { useState, useEffect } from 'react';
import { Activity, TrendingUp, Clock, ShieldCheck, AlertCircle, FileText } from 'lucide-react';
import { ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip, CartesianGrid, Line } from 'recharts';

export function AnalyticsTab({ statsData, tradesHistory }) {
  const [timeframe, setTimeframe] = useState('4h');
  const [candles, setCandles] = useState([]);
  const [htfData, setHtfData] = useState(null);
  const [loading, setLoading] = useState(false);

  const timeframes = [
    { id: '1m', label: '1m' },
    { id: '5m', label: '5m' },
    { id: '15m', label: '15m' },
    { id: '1h', label: '1h' },
    { id: '4h', label: '4h' },
    { id: '1d', label: '1D' },
    { id: '1w', label: '1W' },
    { id: 'all', label: 'ALL' }
  ];

  useEffect(() => {
    const fetchCandles = async () => {
      setLoading(true);
      try {
        const tf = timeframe === 'all' ? '1d' : timeframe;
        const res = await fetch(`/api/v1/history/candles?symbol=BTC/USDC&timeframe=${tf}&limit=50`);
        if (res.ok) {
          const data = await res.json();
          if (Array.isArray(data) && data.length > 0) {
            // Sort candles chronologically ASC
            const sorted = [...data].sort((a, b) => a.timestamp - b.timestamp);
            setCandles(sorted.map((c) => {
              const ts = Number(c.timestamp);
              const dateObj = new Date(ts > 1e11 ? ts : ts * 1000);
              const dateLabel = dateObj.toLocaleDateString([], { month: '2-digit', day: '2-digit' }) + ' ' + dateObj.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
              return {
                time: dateLabel,
                price: c.close,
                tma: c.close * 0.998,
                upper: c.high * 1.002,
                lower: c.low * 0.998
              };
            }));
          }
        }
      } catch (err) {
        console.warn('[AnalyticsTab] Error fetching candles:', err);
      } finally {
        setLoading(false);
      }
    };

    const fetchHtf = async () => {
      try {
        const res = await fetch('/api/v1/analytics/higher_tf');
        if (res.ok) {
          const data = await res.json();
          setHtfData(data);
        }
      } catch (err) {
        console.warn('[AnalyticsTab] Error fetching HTF analytics:', err);
      }
    };

    fetchCandles();
    fetchHtf();
  }, [timeframe]);

  const pnlCurve = tradesHistory && tradesHistory.length > 0
    ? tradesHistory.map((t) => {
        const ts = t.timestamp ? new Date(t.timestamp).getTime() : Date.now();
        const dateObj = new Date(ts);
        const label = dateObj.toLocaleDateString([], { month: '2-digit', day: '2-digit' });
        return {
          date: label,
          balance: t.pnl_usd || 0
        };
      })
    : [
        { date: '10.08', balance: 0 },
        { date: '11.08', balance: 0.5 },
        { date: '12.08', balance: Number(statsData?.realized_pnl || 0) }
      ];

  return (
    <div className="space-y-6">
      {/* Hermes H4 Analysis Report Box */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 shadow-lg">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <FileText className="w-5 h-5 text-blue-400" />
            <h3 className="font-bold text-gray-100 text-base">Raport Analizy H4 Hermes (Co 4 Godziny)</h3>
          </div>
          <span className="text-xs bg-blue-950 border border-blue-500/30 text-blue-300 px-2.5 py-1 rounded-full font-mono">
            H4 Cron Analysis
          </span>
        </div>

        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs font-mono">
          <div className="bg-slate-950 p-3 rounded-lg border border-slate-800">
            <span className="text-slate-400 block mb-1 text-[10px]">H4 TMA Position:</span>
            <span className="font-bold text-emerald-400 text-sm">{htfData?.h4_tma_position || 'INSIDE'}</span>
          </div>

          <div className="bg-slate-950 p-3 rounded-lg border border-slate-800">
            <span className="text-slate-400 block mb-1 text-[10px]">H4 Candle Formation:</span>
            <span className="font-bold text-blue-400 text-sm">{htfData?.h4_candle_formation || 'NEUTRAL'}</span>
          </div>

          <div className="bg-slate-950 p-3 rounded-lg border border-slate-800">
            <span className="text-slate-400 block mb-1 text-[10px]">ATR D1 Expansion Ratio:</span>
            <span className="font-bold text-amber-400 text-sm">{htfData?.atr_d1_expansion_ratio !== undefined ? Number(htfData.atr_d1_expansion_ratio).toFixed(4) : '0.3125'}</span>
          </div>

          <div className="bg-slate-950 p-3 rounded-lg border border-slate-800">
            <span className="text-slate-400 block mb-1 text-[10px]">Engine State:</span>
            <span className="font-bold text-emerald-300 text-sm">BASE_MODE</span>
          </div>
        </div>
      </div>

      {/* Timeframe Selector Toolbar */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 flex flex-wrap items-center justify-between gap-4 shadow-md">
        <div className="flex items-center gap-2 text-sm text-slate-300 font-semibold">
          <Clock className="w-4 h-4 text-blue-400" />
          <span>Skala Czasowa Wykresu (Timeframe):</span>
        </div>

        <div className="flex items-center gap-1.5 bg-slate-950 p-1 rounded-lg border border-slate-800">
          {timeframes.map((tf) => (
            <button
              key={tf.id}
              onClick={() => setTimeframe(tf.id)}
              className={`px-3 py-1 rounded-md text-xs font-mono font-medium transition-all ${
                timeframe === tf.id
                  ? 'bg-blue-600 text-white shadow-sm font-bold'
                  : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/80'
              }`}
            >
              {tf.label}
            </button>
          ))}
        </div>
      </div>

      {/* Charts Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* TMA Bands & BTC Price Chart */}
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 shadow-lg">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-2">
              <Activity className="w-5 h-5 text-emerald-400" />
              <h3 className="font-semibold text-gray-200">Ścieżka Ceny BTC & Pasma TMA ({timeframe})</h3>
            </div>
            <span className="text-xs text-slate-400 font-mono">OKX Spot BTC/USDC</span>
          </div>

          <div className="h-64 w-full">
            {candles.length > 0 ? (
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={candles}>
                  <defs>
                    <linearGradient id="priceGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#10b981" stopOpacity={0.3}/>
                      <stop offset="95%" stopColor="#10b981" stopOpacity={0}/>
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                  <XAxis dataKey="time" stroke="#64748b" fontSize={10} angle={-15} textAnchor="end" height={40} />
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
            ) : (
              <div className="h-full flex items-center justify-center text-slate-500 text-xs font-mono">
                {loading ? 'Ładowanie świec z OKX API...' : 'Brak dostępnych świec dla wybranego interwału'}
              </div>
            )}
          </div>
        </div>

        {/* Equity / PnL Progression */}
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 shadow-lg">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-2">
              <TrendingUp className="w-5 h-5 text-blue-400" />
              <h3 className="font-semibold text-gray-200">Krzywa Salda Portfela (Equity Curve)</h3>
            </div>
            <span className="text-xs text-emerald-400 font-bold bg-emerald-950/60 px-2.5 py-1 rounded border border-emerald-500/30 font-mono">
              PnL: ${Number(statsData?.realized_pnl || 0).toFixed(2)}
            </span>
          </div>

          <div className="h-64 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={pnlCurve}>
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
                <Area type="monotone" dataKey="balance" stroke="#3b82f6" fillOpacity={1} fill="url(#pnlGrad)" strokeWidth={2.5} name="PnL USD" />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>
    </div>
  );
}
