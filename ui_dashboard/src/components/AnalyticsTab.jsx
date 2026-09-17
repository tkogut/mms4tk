import React, { useState, useEffect, useMemo } from 'react';
import { Activity, TrendingUp, Calendar, Layers, Search, Filter, ArrowUpDown, ChevronLeft, ChevronRight, BarChart3, LineChart } from 'lucide-react';
import { ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip, CartesianGrid, Line, ComposedChart, ReferenceLine, ReferenceDot } from 'recharts';
import { formatSignedUsd } from '../utils/formatters';


const CustomTooltip = ({ active, payload, label }) => {
    if (active && payload && payload.length) {
        const c = payload[0].payload;
        return (
            <div className="bg-slate-950/95 border border-slate-800 rounded-lg p-2 shadow-xl text-[11px] font-mono tabular-nums min-w-[150px]">
                <div className="text-slate-400 mb-1 border-b border-slate-800 pb-1">{label}</div>
                <div className="flex justify-between gap-3"><span className="text-slate-500">Price:</span> <span className="text-slate-200 font-bold">{c.price}</span></div>
                {showTma && c.tma && <div className="flex justify-between gap-3"><span className="text-slate-500">TMA:</span> <span className="text-emerald-400">{c.tma}</span></div>}
                
                {c.markers && c.markers.length > 0 && (
                    <div className="mt-2 pt-1 border-t border-slate-800">
                        <div className="text-slate-500 mb-1 text-[9px] uppercase">Zdarzenia:</div>
                        {c.markers.map((m, i) => (
                            <div key={i} className="mb-1 last:mb-0">
                                {m.type === 'entryLong' && <span className="text-emerald-400">▲ {m.label}</span>}
                                {m.type === 'entryShort' && <span className="text-rose-400">▼ {m.label}</span>}
                                {m.type === 'addon' && <span className="text-blue-400">◆ {m.label}</span>}
                                {m.type === 'exitWin' && <span className="text-emerald-400 bg-emerald-950/50 px-1 rounded">● {m.label}</span>}
                                {m.type === 'exitLoss' && <span className="text-rose-400 bg-rose-950/50 px-1 rounded">✕ {m.label}</span>}
                            </div>
                        ))}
                    </div>
                )}
            </div>
        );
    }
    return null;
};

export function AnalyticsTab({ statsData, tradesHistory = [], openPositions = [], apiKey, onRefresh }) {
  const [selectedSymbol, setSelectedSymbol] = useState('BTC/USDC');
  const [timeframe, setTimeframe] = useState('4h');
  const [chartMode, setChartMode] = useState('tma'); // 'tma' (Price & TMA Bands) or 'equity' (Equity Curve)
  const [candles, setCandles] = useState([]);
  const [htfData, setHtfData] = useState(null);
  const [availableSymbols, setAvailableSymbols] = useState(['BTC/USDC', 'ETH/USDC', 'XAU/USDC', 'XAG/USDC']);
  const [loading, setLoading] = useState(false);
  const [addonEvents, setAddonEvents] = useState([]);
  const [showTrades, setShowTrades] = useState(true);
  const [showAddons, setShowAddons] = useState(true);
  const [showTma, setShowTma] = useState(true);
  const [showLivePosition, setShowLivePosition] = useState(true);


  // Closed Trades Table Filtering & Pagination State
  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [tradePeriod, setTradePeriod] = useState('1m'); // '1w', '1m', '3m', 'all'
  const [filterSymbol, setFilterSymbol] = useState('ALL');
  const [filterSide, setFilterSide] = useState('ALL');
  const [filterPnl, setFilterPnl] = useState('ALL');
  const [sortOrder, setSortOrder] = useState('newest'); // 'newest', 'oldest', 'pnl_high', 'pnl_low'
  const [searchTerm, setSearchTerm] = useState('');

  const timeframes = [
    { id: '4h', label: '4H', tf: '4h', limit: 60 },
    { id: '1d', label: '1D', tf: '1d', limit: 60 },
    { id: '1w', label: '1W', tf: '1w', limit: 52 },
    { id: '1m', label: '1M', tf: '1d', limit: 30 },
    { id: '3m', label: '3M', tf: '1d', limit: 90 },
    { id: '1y', label: '1Y', tf: '1d', limit: 365 },
  ];

  const currentTfObj = timeframes.find((t) => t.id === timeframe) || timeframes[0];

  const mappedCandles = useMemo(() => {
    if (!candles || candles.length === 0) return [];
    let result = candles.map(c => ({ ...c }));
    
    const candleInterval = result.length > 1 ? (result[1].timestamp - result[0].timestamp) : 3600000;
    const minTs = result[0].timestamp - candleInterval * 0.5;
    const maxTs = result[result.length - 1].timestamp + candleInterval * 1.5;

    // Add markers if shown
    const addMarker = (timestamp, type, price, meta) => {
        if (!timestamp) return;
        let tsMatch = typeof timestamp === 'string' ? new Date(timestamp).getTime() : Number(timestamp);
        if (isNaN(tsMatch)) return;
        if (tsMatch < 10000000000) tsMatch *= 1000; // seconds to ms

        if (tsMatch < minTs || tsMatch > maxTs) {
            return; // Outside visible chart window
        }

        // find candle where tsMatch is within candle interval
        let closestCandle = null;
        for (let i = result.length - 1; i >= 0; i--) {
            if (result[i].timestamp <= tsMatch && tsMatch <= result[i].timestamp + candleInterval * 1.5) {
                closestCandle = result[i];
                break;
            }
        }
        if (!closestCandle) {
            let minDiff = Infinity;
            for (let i = 0; i < result.length; i++) {
                const diff = Math.abs(result[i].timestamp - tsMatch);
                if (diff < minDiff && diff <= candleInterval * 2) {
                    minDiff = diff;
                    closestCandle = result[i];
                }
            }
        }
        if (closestCandle) {
            if (!closestCandle.markers) closestCandle.markers = [];
            closestCandle.markers.push({ type, price, ...meta });
        }
    };

    if (showTrades && tradesHistory) {
        tradesHistory.filter(t => normalizeSym(t.symbol) === normalizeSym(selectedSymbol)).forEach(t => {
            const isLong = String(t.side).toUpperCase().includes('LONG') || String(t.side).toUpperCase().includes('BUY');
            const pnl = t.pnl_net_usd !== undefined && t.pnl_net_usd !== null ? Number(t.pnl_net_usd) : Number(t.pnl_usd || 0);
            
            // Entry
            if (t.entry_timestamp) {
                addMarker(t.entry_timestamp, isLong ? 'entryLong' : 'entryShort', t.entry_price, { label: `WEJŚCIE @ ${t.entry_price}`, side: t.side, vol: t.volume, fee: t.fee_usd });
            } else if (t.timestamp) {
                // If no precise entry_timestamp, fallback to 1 candle before exit
                let exitTs = typeof t.timestamp === 'string' ? new Date(t.timestamp).getTime() : Number(t.timestamp);
                if (exitTs < 10000000000) exitTs *= 1000;
                addMarker(exitTs - candleInterval, isLong ? 'entryLong' : 'entryShort', t.entry_price, { label: `WEJŚCIE @ ${t.entry_price}`, side: t.side, vol: t.volume, fee: t.fee_usd });
            }
            
            // Exit
            if (t.timestamp) {
                 const isWin = pnl >= 0;
                 addMarker(t.timestamp, isWin ? 'exitWin' : 'exitLoss', t.exit_price, { label: isWin ? `TP +$${pnl.toFixed(2)}` : `SL -$${Math.abs(pnl).toFixed(2)}`, pnl, fee: t.fee_usd });
            }
        });
    }

    if (showAddons && addonEvents) {
        addonEvents.filter(a => normalizeSym(a.symbol) === normalizeSym(selectedSymbol)).forEach(a => {
            addMarker(a.timestamp, 'addon', a.price, { label: `+ADD @ ${a.price}` });
        });
    }

    return result;
  }, [candles, tradesHistory, addonEvents, selectedSymbol, showTrades, showAddons]);

  const activePos = useMemo(() => openPositions.find(p => normalizeSym(p.symbol) === normalizeSym(selectedSymbol)), [openPositions, selectedSymbol]);

  // Symbol normalization utility helper
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

  useEffect(() => {
    const fetchSymbols = async () => {
      try {
        const res = await fetch('/api/v1/symbols');
        if (res.ok) {
          const data = await res.json();
          if (data.symbols && Array.isArray(data.symbols)) {
            setAvailableSymbols(data.symbols.map((s) => s.symbol));
          }
        }
      } catch (err) {
        console.warn('[AnalyticsTab] Error fetching available symbols:', err);
      }
    };
    fetchSymbols();
  }, []);

  useEffect(() => {
    const fetchCandles = async () => {
      setLoading(true);
      try {
        const res = await fetch(`/api/v1/history/candles?symbol=${encodeURIComponent(selectedSymbol)}&timeframe=${currentTfObj.tf}&limit=${currentTfObj.limit}`);
        if (res.ok) {
          const data = await res.json();
          if (Array.isArray(data) && data.length > 0) {
            const sorted = [...data].sort((a, b) => a.timestamp - b.timestamp);
            setCandles(sorted.map((c) => {
              const ts = Number(c.timestamp);
              const dateObj = new Date(ts > 1e11 ? ts : ts * 1000);
              const isIntraDay = currentTfObj.id === '4h';
              const dateLabel = isIntraDay
                ? dateObj.toLocaleDateString([], { month: '2-digit', day: '2-digit' }) + ' ' + dateObj.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
                : dateObj.toLocaleDateString([], { month: '2-digit', day: '2-digit' });

              return {
                time: dateLabel,
                timestamp: ts, // required for mapping trades later
                price: Number(c.close),
                tma: c.tma ? Number(Number(c.tma).toFixed(2)) : Number((c.close * 0.998).toFixed(2)),
                upper: c.upper_band ? Number(Number(c.upper_band).toFixed(2)) : Number((c.high * 1.002).toFixed(2)),
                lower: c.lower_band ? Number(Number(c.lower_band).toFixed(2)) : Number((c.low * 0.998).toFixed(2))
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

    
    const fetchAddons = async () => {
      try {
        const res = await fetch(`/api/v1/history/addons?symbol=${encodeURIComponent(selectedSymbol)}&limit=200`);
        if (res.ok) {
          const data = await res.json();
          setAddonEvents(data || []);
        }
      } catch (err) {
        console.warn('[AnalyticsTab] Error fetching addons:', err);
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
    fetchAddons();
  }, [timeframe, selectedSymbol]);

  // Equity Curve Data
  const equityCurveData = useMemo(() => {
    if (!tradesHistory || tradesHistory.length === 0) {
      return [
        { time: 'Start', balance: 0 },
        { time: 'Teraz', balance: 0 }
      ];
    }

    const sorted = [...tradesHistory].sort((a, b) => (new Date(a.timestamp).getTime() || 0) - (new Date(b.timestamp).getTime() || 0));
    let cum = 0;
    return sorted.map((t) => {
      const net = t.pnl_net_usd !== undefined && t.pnl_net_usd !== null ? Number(t.pnl_net_usd) : Number(t.pnl_usd || 0);
      cum += net;
      const d = new Date(t.timestamp || Date.now());
      return {
        time: d.toLocaleDateString([], { month: '2-digit', day: '2-digit' }) + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
        balance: Number(cum.toFixed(2))
      };
    });
  }, [tradesHistory]);

  // Unique symbol options
  const symbolOptions = useMemo(() => {
    const set = new Set();
    availableSymbols.forEach((s) => {
      const norm = normalizeSym(s);
      if (norm) set.add(norm);
    });
    if (Array.isArray(tradesHistory)) {
      tradesHistory.forEach((t) => {
        if (t.symbol) {
          const norm = normalizeSym(t.symbol);
          if (norm) set.add(norm);
        }
      });
    }
    return Array.from(set);
  }, [availableSymbols, tradesHistory]);

  // Filtered & Sorted Closed Trades
  const filteredAndSortedTrades = useMemo(() => {
    if (!tradesHistory || !Array.isArray(tradesHistory)) return [];
    const now = Date.now();
    let periodCutoff = 0;
    if (tradePeriod === '1w') periodCutoff = now - 7 * 24 * 3600 * 1000;
    else if (tradePeriod === '1m') periodCutoff = now - 30 * 24 * 3600 * 1000;
    else if (tradePeriod === '3m') periodCutoff = now - 90 * 24 * 3600 * 1000;

    return tradesHistory.filter((t) => {
      const tTs = t.timestamp ? new Date(t.timestamp).getTime() : now;
      if (periodCutoff > 0 && tTs < periodCutoff) return false;

      if (filterSymbol !== 'ALL') {
        if (normalizeSym(t.symbol) !== normalizeSym(filterSymbol)) return false;
      }

      if (filterSide !== 'ALL') {
        const sideUpper = String(t.side || '').toUpperCase();
        if (filterSide === 'BUY' && !sideUpper.includes('BUY') && !sideUpper.includes('LONG')) return false;
        if (filterSide === 'SELL' && !sideUpper.includes('SELL') && !sideUpper.includes('SHORT')) return false;
      }

      const netPnl = t.pnl_net_usd !== undefined && t.pnl_net_usd !== null ? Number(t.pnl_net_usd) : Number(t.pnl_usd || 0);
      const isWin = t.pnl_net_usd !== undefined && t.pnl_net_usd !== null ? netPnl > 0 : (t.is_win || netPnl >= 0);
      if (filterPnl === 'WIN' && !isWin) return false;
      if (filterPnl === 'LOSS' && isWin) return false;

      if (searchTerm.trim() !== '') {
        const term = searchTerm.toLowerCase();
        const sym = (t.symbol || '').toLowerCase();
        const side = (t.side || '').toLowerCase();
        const reason = (t.reason || '').toLowerCase();
        if (!sym.includes(term) && !side.includes(term) && !reason.includes(term)) return false;
      }

      return true;
    }).sort((a, b) => {
      const tsA = a.timestamp ? new Date(a.timestamp).getTime() : 0;
      const tsB = b.timestamp ? new Date(b.timestamp).getTime() : 0;
      const pnlA = a.pnl_net_usd !== undefined && a.pnl_net_usd !== null ? Number(a.pnl_net_usd) : Number(a.pnl_usd || 0);
      const pnlB = b.pnl_net_usd !== undefined && b.pnl_net_usd !== null ? Number(b.pnl_net_usd) : Number(b.pnl_usd || 0);

      if (sortOrder === 'newest') return tsB - tsA;
      if (sortOrder === 'oldest') return tsA - tsB;
      if (sortOrder === 'pnl_high') return pnlB - pnlA;
      if (sortOrder === 'pnl_low') return pnlA - pnlB;
      return tsB - tsA;
    });
  }, [tradesHistory, tradePeriod, filterSymbol, filterSide, filterPnl, sortOrder, searchTerm]);

  // Pagination for Closed Trades
  const totalFilteredTrades = filteredAndSortedTrades.length;
  const totalPages = Math.max(1, Math.ceil(totalFilteredTrades / pageSize));
  const validCurrentPage = Math.min(Math.max(1, currentPage), totalPages);
  const startIndex = (validCurrentPage - 1) * pageSize;
  const endIndex = Math.min(startIndex + pageSize, totalFilteredTrades);
  const currentTrades = filteredAndSortedTrades.slice(startIndex, endIndex);

  return (
    <div className="space-y-3">
      {/* 1. CHART-CENTRIC WORK SURFACE (100% Width with HUD Overlays) */}
      <div className="relative bg-slate-900/95 border border-slate-800 rounded-xl overflow-hidden shadow-xl">
        {/* Top Floating Control Bar (Overlay Top Left & Right) */}
        <div className="absolute top-2.5 left-2.5 right-2.5 z-10 flex flex-wrap items-center justify-between gap-2 pointer-events-none">
          {/* Left HUD: Pair Selector + Timeframe Pills + Mode Toggle (Pointer events enabled) */}
          <div className="pointer-events-auto flex items-center gap-1.5 bg-slate-950/85 backdrop-blur-md border border-slate-800/90 rounded-lg p-1 shadow-md">
            {/* Symbol Dropdown */}
            <select
              value={selectedSymbol}
              onChange={(e) => setSelectedSymbol(e.target.value)}
              className="px-2 py-0.5 bg-slate-900 border border-slate-800 rounded text-xs font-mono font-bold text-blue-400 focus:outline-none cursor-pointer"
            >
              {symbolOptions.map((sym) => (
                <option key={sym} value={sym}>{sym}</option>
              ))}
            </select>

            {/* Timeframe Buttons */}
            <div className="flex items-center gap-0.5">
              {timeframes.map((tf) => (
                <button
                  key={tf.id}
                  onClick={() => setTimeframe(tf.id)}
                  className={`px-1.5 py-0.5 text-[10px] font-mono rounded transition-colors ${
                    timeframe === tf.id
                      ? 'bg-blue-600 text-white font-bold'
                      : 'text-slate-400 hover:text-white'
                  }`}
                >
                  {tf.label}
                </button>
              ))}
            </div>

            {/* Chart Mode Toggle */}
            <div className="border-l border-slate-800 pl-1 ml-0.5 flex items-center gap-0.5">
              <button onClick={() => setChartMode('tma')} className={`p-1 rounded ${chartMode === 'tma' ? 'bg-emerald-600 text-white' : 'text-slate-400'}`}><LineChart className="w-3.5 h-3.5" /></button>
              <button onClick={() => setChartMode('equity')} className={`p-1 rounded ${chartMode === 'equity' ? 'bg-emerald-600 text-white' : 'text-slate-400'}`}><BarChart3 className="w-3.5 h-3.5" /></button>
            </div>
            
            {chartMode === 'tma' && (
              <div className="border-l border-slate-800 pl-2 ml-1 flex flex-wrap items-center gap-2 text-[10px] font-medium text-slate-300">
                <label className="flex items-center gap-1 cursor-pointer whitespace-nowrap"><input type="checkbox" checked={showTrades} onChange={e => setShowTrades(e.target.checked)} className="accent-blue-500" /> Transakcje</label>
                <label className="flex items-center gap-1 cursor-pointer whitespace-nowrap"><input type="checkbox" checked={showAddons} onChange={e => setShowAddons(e.target.checked)} className="accent-blue-500" /> Dokupy</label>
                <label className="flex items-center gap-1 cursor-pointer whitespace-nowrap"><input type="checkbox" checked={showTma} onChange={e => setShowTma(e.target.checked)} className="accent-blue-500" /> Kanały TMA</label>
                <label className="flex items-center gap-1 cursor-pointer whitespace-nowrap"><input type="checkbox" checked={showLivePosition} onChange={e => setShowLivePosition(e.target.checked)} className="accent-blue-500" /> Pozycja Live</label>
              </div>
            )}
          </div>

          {/* Right HUD: Hermes H4 Telemetry Overlay Chips */}
          <div className="pointer-events-auto flex items-center gap-1.5 bg-slate-950/85 backdrop-blur-md border border-slate-800/90 rounded-lg px-2 py-1 shadow-md text-[10px] font-mono tabular-nums">
            <span className="text-slate-400 hidden sm:inline">HERMES H4:</span>
            <span className="text-emerald-400 font-bold bg-emerald-950/60 border border-emerald-500/30 px-1.5 py-0.2 rounded">
              TMA: {htfData?.h4_tma_position || 'INSIDE'}
            </span>
            <span className="text-blue-400 font-bold bg-blue-950/60 border border-blue-500/30 px-1.5 py-0.2 rounded">
              VR: {htfData?.atr_d1_expansion_ratio !== undefined ? Number(htfData.atr_d1_expansion_ratio).toFixed(3) : '0.312'}
            </span>
            <span className="text-amber-400 font-bold bg-amber-950/60 border border-amber-500/30 px-1.5 py-0.2 rounded hidden md:inline">
              ATR: {htfData?.h4_candle_formation || 'NEUTRAL'}
            </span>
          </div>
        </div>

        {/* 100% Width Canvas Area (Recharts) */}
        <div className="w-full h-[360px] sm:h-[440px] pt-12 pb-1 px-1">
          <ResponsiveContainer width="100%" height="100%">
            {chartMode === 'tma' ? (
              <ComposedChart data={mappedCandles} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
                <defs>
                  <linearGradient id="priceGradient" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.3} />
                    <stop offset="95%" stopColor="#3b82f6" stopOpacity={0.0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" opacity={0.6} />
                <XAxis dataKey="time" stroke="#64748b" tick={{ fontSize: 10, fill: '#64748b' }} tickLine={false} />
                <YAxis domain={['auto', 'auto']} stroke="#64748b" tick={{ fontSize: 10, fill: '#64748b' }} tickLine={false} orientation="right" />
                <Tooltip content={<CustomTooltip />} cursor={{ stroke: '#334155', strokeWidth: 1, strokeDasharray: '4 4' }} />
                
                {showTma && <Line type="monotone" dataKey="upper" stroke="#ef4444" strokeWidth={1} strokeDasharray="3 3" dot={false} name="TMA Upper" isAnimationActive={false} />}
                {showTma && <Line type="monotone" dataKey="tma" stroke="#10b981" strokeWidth={1.5} dot={false} name="TMA Midline" isAnimationActive={false} />}
                {showTma && <Line type="monotone" dataKey="lower" stroke="#10b981" strokeWidth={1} strokeDasharray="3 3" dot={false} name="TMA Lower" isAnimationActive={false} />}
                
                <Area type="monotone" dataKey="price" stroke="#3b82f6" strokeWidth={2} fillOpacity={1} fill="url(#priceGradient)" name="Cena" isAnimationActive={false} />
                
                {/* Active Position Live Line */}
                {showLivePosition && activePos && (
                    <ReferenceLine y={activePos.entry_price} stroke={activePos.side === 'long' ? '#10b981' : '#ef4444'} strokeDasharray="4 4" strokeWidth={1.5} 
                        label={{ value: `AKTYWNY ${activePos.side.toUpperCase()} @ ${activePos.entry_price} (uPnL: ${activePos.unrealized_pnl >= 0 ? '+' : ''}${activePos.unrealized_pnl?.toFixed(2)}$)`, fill: activePos.side === 'long' ? '#34d399' : '#f87171', position: 'insideTopLeft', fontSize: 10, fontFamily: 'monospace' }} />
                )}
                
                {/* Render markers */}
                {showTrades && (
                    <Line type="monotone" dataKey="price" dot={({cx, cy, payload}) => {
                        if (!payload.markers || payload.markers.length === 0) return null;
                        return (
                            <g>
                                {payload.markers.map((m, i) => {
                                    const yOff = (i * 16) - (payload.markers.length * 8);
                                    if (m.type === 'entryLong') return <polygon points={`${cx},${cy-7+yOff} ${cx-7},${cy+7+yOff} ${cx+7},${cy+7+yOff}`} fill="#10b981" stroke="#0f172a" strokeWidth={1.5} key={i}/>;
                                    if (m.type === 'entryShort') return <polygon points={`${cx},${cy+7+yOff} ${cx-7},${cy-7+yOff} ${cx+7},${cy-7+yOff}`} fill="#ef4444" stroke="#0f172a" strokeWidth={1.5} key={i}/>;
                                    if (m.type === 'addon') return <polygon points={`${cx},${cy-6+yOff} ${cx-6},${cy+yOff} ${cx},${cy+6+yOff} ${cx+6},${cy+yOff}`} fill="#22d3ee" stroke="#0f172a" strokeWidth={1.5} key={i}/>;
                                    if (m.type === 'exitWin') return (
                                        <g key={i}>
                                            <circle cx={cx} cy={cy+yOff} r={6} fill="#10b981" stroke="#0f172a" strokeWidth={1.5}/>
                                            <circle cx={cx} cy={cy+yOff} r={2} fill="#ffffff" />
                                        </g>
                                    );
                                    if (m.type === 'exitLoss') return (
                                        <g key={i}>
                                            <circle cx={cx} cy={cy+yOff} r={7} fill="#0f172a" opacity={0.5} />
                                            <path d={`M${cx-4},${cy-4+yOff} L${cx+4},${cy+4+yOff} M${cx+4},${cy-4+yOff} L${cx-4},${cy+4+yOff}`} stroke="#ef4444" strokeWidth={2.5} strokeLinecap="round" />
                                        </g>
                                    );
                                    return null;
                                })}
                            </g>
                        );
                    }} stroke="none" isAnimationActive={false} />
                )}
              </ComposedChart>
            ) : (
              <AreaChart data={equityCurveData} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
                <defs>
                  <linearGradient id="equityGradient" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#10b981" stopOpacity={0.4} />
                    <stop offset="95%" stopColor="#10b981" stopOpacity={0.0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" opacity={0.6} />
                <XAxis dataKey="time" stroke="#64748b" tick={{ fontSize: 10, fill: '#64748b' }} tickLine={false} />
                <YAxis domain={['auto', 'auto']} stroke="#64748b" tick={{ fontSize: 10, fill: '#64748b' }} tickLine={false} orientation="right" />
                <Tooltip
                  contentStyle={{ backgroundColor: '#020617', borderColor: '#1e293b', borderRadius: '8px', fontSize: '11px', fontFamily: 'monospace' }}
                  formatter={(value) => [`$${Number(value).toFixed(2)}`, 'Krzywa Kapitału']}
                />
                <Area type="monotone" dataKey="balance" stroke="#10b981" strokeWidth={2} fillOpacity={1} fill="url(#equityGradient)" name="Realized PnL" />
              </AreaChart>
            )}
          </ResponsiveContainer>
        </div>
      </div>

      {/* 2. WYKAZ ZAREJESTROWANYCH TRANSAKCJI RZECZYWISTYCH (Closed Trades Log) */}
      <div className="bg-slate-900/95 border border-slate-800 rounded-xl p-3 sm:p-4 shadow-lg space-y-2.5">
        {/* Header & Quick Filter Pills */}
        <div className="flex flex-col md:flex-row items-start md:items-center justify-between gap-2 pb-2 border-b border-slate-800/80">
          <div className="flex items-center gap-2">
            <Layers className="w-4 h-4 text-blue-400" />
            <h3 className="font-mono font-bold text-gray-100 text-xs sm:text-sm uppercase tracking-wider">
              Rejestr Zrealizowanych Transakcji (Closed Trades)
            </h3>
            <span className="text-[10px] font-mono bg-blue-950 text-blue-300 border border-blue-500/30 px-2 py-0.5 rounded-full">
              {totalFilteredTrades} z {tradesHistory.length}
            </span>
          </div>

          {/* Quick Period Selector Pills */}
          <div className="flex items-center gap-1 bg-slate-950 p-1 rounded-lg border border-slate-800 text-[10px] font-mono">
            <Calendar className="w-3.5 h-3.5 text-slate-400 ml-1" />
            {['1w', '1m', '3m', 'all'].map((p) => (
              <button
                key={p}
                onClick={() => { setTradePeriod(p); setCurrentPage(1); }}
                className={`px-2 py-0.5 rounded transition-all ${
                  tradePeriod === p ? 'bg-blue-600 text-white font-bold' : 'text-slate-400 hover:text-white'
                }`}
              >
                {p === '1w' ? '1 Tydzień' : p === '1m' ? '1 Miesiąc ⭐' : p === '3m' ? '3 Miesiące' : 'Wszystkie'}
              </button>
            ))}
          </div>
        </div>

        {/* Dense Search & Dropdown Filter Bar */}
        <div className="grid grid-cols-1 sm:grid-cols-4 gap-1.5 text-[11px] font-mono">
          <div className="relative">
            <Search className="w-3.5 h-3.5 absolute left-2.5 top-2 text-slate-500" />
            <input
              type="text"
              placeholder="Szukaj pary, strony, powodu..."
              value={searchTerm}
              onChange={(e) => { setSearchTerm(e.target.value); setCurrentPage(1); }}
              className="w-full pl-8 pr-2 py-1 bg-slate-950 border border-slate-800 rounded text-slate-200 focus:outline-none focus:border-blue-500"
            />
          </div>

          <select
            value={filterSymbol}
            onChange={(e) => { setFilterSymbol(e.target.value); setCurrentPage(1); }}
            className="px-2 py-1 bg-slate-950 border border-slate-800 rounded text-slate-300 focus:outline-none cursor-pointer"
          >
            <option value="ALL">Wszystkie Pary</option>
            {symbolOptions.map((sym) => (
              <option key={sym} value={sym}>{sym}</option>
            ))}
          </select>

          <select
            value={filterSide}
            onChange={(e) => { setFilterSide(e.target.value); setCurrentPage(1); }}
            className="px-2 py-1 bg-slate-950 border border-slate-800 rounded text-slate-300 focus:outline-none cursor-pointer"
          >
            <option value="ALL">Wszystkie Strony (BUY/SELL)</option>
            <option value="BUY">Tylko LONG (BUY)</option>
            <option value="SELL">Tylko SHORT (SELL)</option>
          </select>

          <select
            value={filterPnl}
            onChange={(e) => { setFilterPnl(e.target.value); setCurrentPage(1); }}
            className="px-2 py-1 bg-slate-950 border border-slate-800 rounded text-slate-300 focus:outline-none cursor-pointer"
          >
            <option value="ALL">Wszystkie Wyniki (PnL)</option>
            <option value="WIN">Tylko Zyskowne (WIN)</option>
            <option value="LOSS">Tylko Stratne (LOSS)</option>
          </select>
        </div>

        {/* High-Density Closed Trades Table */}
        <div className="overflow-x-auto">
          {currentTrades && currentTrades.length > 0 ? (
            <table className="w-full text-xs text-left text-slate-300">
              <thead className="bg-slate-950 text-slate-400 uppercase font-mono text-[10px] border-b border-slate-800 tracking-wider">
                <tr>
                  <th className="px-3 py-2 text-left">Czas (UTC)</th>
                  <th className="px-3 py-2 text-left">Para</th>
                  <th className="px-3 py-2 text-left">Strona</th>
                  <th className="px-3 py-2 text-right">Wejście</th>
                  <th className="px-3 py-2 text-right">Wyjście</th>
                  <th className="px-3 py-2 text-right">Prowizja OKX</th>
                  <th className="px-3 py-2 text-right">Wynik Netto</th>
                  <th className="px-3 py-2 text-left">Powód Wyjścia</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/60 font-mono tabular-nums text-[11px]">
                {currentTrades.map((t, idx) => {
                  const netPnl = t.pnl_net_usd !== undefined && t.pnl_net_usd !== null ? Number(t.pnl_net_usd) : Number(t.pnl_usd || 0);
                  const isWin = netPnl >= 0;
                  const fee = Number(t.fee_usd || 0);
                  const dateStr = t.timestamp ? new Date(t.timestamp).toLocaleString([], { dateStyle: 'short', timeStyle: 'short' }) : '---';
                  const sideUpper = String(t.side || 'LONG').toUpperCase();
                  const isLong = sideUpper.includes('LONG') || sideUpper.includes('BUY');

                  return (
                    <tr key={idx} className="hover:bg-slate-800/40 transition-colors">
                      <td className="px-3 py-2 text-slate-400 text-left">{dateStr}</td>
                      <td className="px-3 py-2 font-bold text-gray-100 text-left">{normalizeSym(t.symbol)}</td>
                      <td className="px-3 py-2 text-left">
                        <span className={`px-1.5 py-0.2 rounded font-bold text-[10px] border ${
                          isLong ? 'bg-emerald-950 text-emerald-400 border-emerald-500/30' : 'bg-rose-950 text-rose-400 border-rose-500/30'
                        }`}>
                          {sideUpper}
                        </span>
                      </td>
                      <td className="px-3 py-2 text-right text-slate-300">
                        ${Number(t.entry_price || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                      </td>
                      <td className="px-3 py-2 text-right text-slate-300">
                        ${Number(t.exit_price || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                      </td>
                      <td className="px-3 py-2 text-right text-slate-400">
                        ${fee.toFixed(4)}
                      </td>
                      <td className="px-3 py-2 text-right">
                        <span className={`font-bold px-1.5 py-0.2 rounded text-[10px] border ${
                          isWin ? 'bg-emerald-950/80 text-emerald-400 border-emerald-500/30' : 'bg-rose-950/80 text-rose-400 border-rose-500/30'
                        }`}>
                          {formatSignedUsd(netPnl)}
                        </span>
                      </td>
                      <td className="px-3 py-2 text-left">
                        {(() => {
                          const r = t.reason || '';
                          if (r === 'STALE_FORCE_CLOSE') return <span className="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-amber-950/70 text-amber-300 border border-amber-500/40">INACTIVITY (STALE)</span>;
                          if (r === 'STALE_SPLIT_50') return <span className="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-amber-950/70 text-amber-300 border border-amber-500/40">SPLIT 50 (STALE)</span>;
                          if (r === 'MIDLINE_PARTIAL_TP' || r === 'MIDLINE_TP') return <span className="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-teal-950/70 text-teal-300 border border-teal-500/40">MIDLINE TP</span>;
                          if (r === 'TAKE-PROFIT' || r === 'TP') return <span className="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-emerald-950/70 text-emerald-300 border border-emerald-500/40">TAKE-PROFIT (TP)</span>;
                          if (r === 'STOP-LOSS' || r === 'SL') return <span className="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-rose-950/70 text-rose-300 border border-rose-500/40">STOP-LOSS (SL)</span>;
                          if (r === 'FORCE_CLOSE') return <span className="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-amber-950/70 text-amber-300 border border-amber-500/40">FORCE CLOSE</span>;
                          return <span className="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-slate-800 text-slate-300 border border-slate-700">{r || 'BREAK-EVEN (BE)'}</span>;
                        })()}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          ) : (
            <div className="text-center py-5 text-slate-500 text-xs bg-slate-950/40 rounded-lg border border-slate-800/60 font-mono">
              Brak zarejestrowanych transakcji w wybranym przedziale czasowym.
            </div>
          )}
        </div>

        {/* Pagination Bar */}
        {totalPages > 1 && (
          <div className="flex items-center justify-between pt-2 border-t border-slate-800/80 text-[11px] font-mono">
            <span className="text-slate-400">
              Strona <b className="text-slate-200">{validCurrentPage}</b> z <b className="text-slate-200">{totalPages}</b>
            </span>
            <div className="flex items-center gap-1">
              <button
                onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
                disabled={validCurrentPage === 1}
                className="p-1 bg-slate-950 border border-slate-800 rounded disabled:opacity-30 hover:border-slate-700 text-slate-300"
              >
                <ChevronLeft className="w-3.5 h-3.5" />
              </button>
              <button
                onClick={() => setCurrentPage((p) => Math.min(totalPages, p + 1))}
                disabled={validCurrentPage === totalPages}
                className="p-1 bg-slate-950 border border-slate-800 rounded disabled:opacity-30 hover:border-slate-700 text-slate-300"
              >
                <ChevronRight className="w-3.5 h-3.5" />
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
