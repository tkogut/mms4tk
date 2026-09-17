import React, { useState, useEffect, useMemo } from 'react';
import { Activity, TrendingUp, Calendar, Layers, Search, Filter, ArrowUpDown, ChevronLeft, ChevronRight, BarChart3, LineChart } from 'lucide-react';
import { ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip, CartesianGrid, Brush } from 'recharts';
import { formatSignedUsd } from '../utils/formatters';
import { PriceChart } from './PriceChart';

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

  // Symbol normalization utility helper — must be declared before activePos/
  // chartMarkers below, both of which call it inside a useMemo factory (executed
  // synchronously at that point in render, not deferred) — referencing it from
  // its old position further down this file threw `ReferenceError: Cannot access
  // 'normalizeSym' before initialization` on every single render of this tab.
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

  // Each entry is a real VISIBLE TIME WINDOW (label = window, not candle size).
  // `tf` (candle interval) is chosen per window so `limit` (candle count) stays
  // safely under OKX's single-request cap (~300, no pagination) while still
  // covering the whole window: 4H@5m=48, 1D@15m=96, 1W@1h=168, 1M@4h=180,
  // 3M@1d=90, 1Y@1d=365 (unchanged — this one was already a real window before).
  const timeframes = [
    { id: '4h', label: '4H', tf: '5m', limit: 48 },
    { id: '1d', label: '1D', tf: '15m', limit: 96 },
    { id: '1w', label: '1W', tf: '1h', limit: 168 },
    { id: '1m', label: '1M', tf: '4h', limit: 180 },
    { id: '3m', label: '3M', tf: '1d', limit: 90 },
    { id: '1y', label: '1Y', tf: '1d', limit: 365 },
  ];

  const currentTfObj = timeframes.find((t) => t.id === timeframe) || timeframes[0];

  const activePos = useMemo(() => openPositions.find(p => normalizeSym(p.symbol) === normalizeSym(selectedSymbol)), [openPositions, selectedSymbol]);

  // Trade/addon/active-position markers, in Lightweight Charts' own SeriesMarker
  // shape ({time (epoch seconds), position, shape, color, text}) — this replaces
  // the old Recharts-era approach of mutating candle.markers[] and hand-drawing
  // SVG shapes via a custom dot renderer, now that the price panel itself is a
  // native PriceChart (see PriceChart.jsx) instead of a Recharts ComposedChart.
  const chartMarkers = useMemo(() => {
    if (!candles || candles.length === 0) return [];
    const sorted = [...candles].sort((a, b) => a.timestamp - b.timestamp);
    const candleInterval = sorted.length > 1 ? (sorted[1].timestamp - sorted[0].timestamp) : 3600000;
    const minTs = sorted[0].timestamp - candleInterval * 0.5;
    const maxTs = sorted[sorted.length - 1].timestamp + candleInterval * 1.5;

    const markers = [];
    const SHAPE = { entryLong: 'arrowUp', entryShort: 'arrowDown', addon: 'circle', exitWin: 'circle', exitLoss: 'square' };
    const COLOR = { entryLong: '#10b981', entryShort: '#f43f5e', addon: '#22d3ee', exitWin: '#10b981', exitLoss: '#f43f5e' };
    const POSITION = { entryLong: 'belowBar', entryShort: 'aboveBar', addon: 'inBar', exitWin: 'aboveBar', exitLoss: 'belowBar' };

    // Same "snap to nearest candle" behavior as before the PriceChart rewrite —
    // Lightweight Charts markers are placed by `time`, so we still need to land
    // exactly on one of the series' own candle times, not an arbitrary instant.
    const addMarker = (timestamp, type, text) => {
        if (!timestamp) return;
        let tsMatch = typeof timestamp === 'string' ? new Date(timestamp).getTime() : Number(timestamp);
        if (isNaN(tsMatch)) return;
        if (tsMatch < 10000000000) tsMatch *= 1000; // seconds to ms
        if (tsMatch < minTs || tsMatch > maxTs) return; // outside visible chart window

        let closestTs = null;
        for (let i = sorted.length - 1; i >= 0; i--) {
            if (sorted[i].timestamp <= tsMatch && tsMatch <= sorted[i].timestamp + candleInterval * 1.5) {
                closestTs = sorted[i].timestamp;
                break;
            }
        }
        if (closestTs === null) {
            let minDiff = Infinity;
            for (let i = 0; i < sorted.length; i++) {
                const diff = Math.abs(sorted[i].timestamp - tsMatch);
                if (diff < minDiff && diff <= candleInterval * 2) {
                    minDiff = diff;
                    closestTs = sorted[i].timestamp;
                }
            }
        }
        if (closestTs === null) return;

        markers.push({
            time: Math.floor(closestTs / 1000),
            position: POSITION[type] || 'inBar',
            shape: SHAPE[type] || 'circle',
            color: COLOR[type] || '#94a3b8',
            text: text || '',
        });
    };

    if (showTrades && tradesHistory) {
        tradesHistory.filter(t => normalizeSym(t.symbol) === normalizeSym(selectedSymbol)).forEach(t => {
            const isLong = String(t.side).toUpperCase().includes('LONG') || String(t.side).toUpperCase().includes('BUY');
            const pnl = t.pnl_net_usd !== undefined && t.pnl_net_usd !== null ? Number(t.pnl_net_usd) : Number(t.pnl_usd || 0);

            if (t.entry_timestamp) {
                addMarker(t.entry_timestamp, isLong ? 'entryLong' : 'entryShort', `WEJŚCIE @ ${t.entry_price}`);
            } else if (t.timestamp) {
                // If no precise entry_timestamp, fallback to 1 candle before exit
                let exitTs = typeof t.timestamp === 'string' ? new Date(t.timestamp).getTime() : Number(t.timestamp);
                if (exitTs < 10000000000) exitTs *= 1000;
                addMarker(exitTs - candleInterval, isLong ? 'entryLong' : 'entryShort', `WEJŚCIE @ ${t.entry_price}`);
            }

            if (t.timestamp) {
                const isWin = pnl >= 0;
                addMarker(t.timestamp, isWin ? 'exitWin' : 'exitLoss', isWin ? `TP +$${pnl.toFixed(2)}` : `SL -$${Math.abs(pnl).toFixed(2)}`);
            }
        });
    }

    if (showAddons && addonEvents) {
        addonEvents.filter(a => normalizeSym(a.symbol) === normalizeSym(selectedSymbol)).forEach(a => {
            addMarker(a.timestamp, 'addon', `+ADD @ ${a.price}`);
        });
    }

    if (showLivePosition && activePos?.entry_timestamp) {
        addMarker(activePos.entry_timestamp, activePos.side === 'long' ? 'entryLong' : 'entryShort', `AKTYWNA POZYCJA @ ${activePos.entry_price}`);
    }

    // createSeriesMarkers requires its input sorted ascending by time.
    markers.sort((a, b) => a.time - b.time);
    return markers;
  }, [candles, tradesHistory, addonEvents, selectedSymbol, showTrades, showAddons, showLivePosition, activePos]);

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
              const rawTs = Number(c.timestamp);
              // Normalize once here so every downstream consumer (PriceChart,
              // marker-time snapping) can assume epoch MILLISECONDS — ccxt/OKX
              // already return ms, this only guards a theoretical seconds input.
              const tsMs = rawTs > 1e11 ? rawTs : rawTs * 1000;
              const dateObj = new Date(tsMs);
              const isIntraDay = currentTfObj.id === '4h';
              const dateLabel = isIntraDay
                ? dateObj.toLocaleDateString([], { month: '2-digit', day: '2-digit' }) + ' ' + dateObj.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
                : dateObj.toLocaleDateString([], { month: '2-digit', day: '2-digit' });

              return {
                time: dateLabel,
                timestamp: tsMs, // epoch ms — required for PriceChart + trade marker mapping
                open: Number(c.open),
                high: Number(c.high),
                low: Number(c.low),
                close: Number(c.close),
                price: Number(c.close), // kept for the Equity Curve / any legacy consumer
                tma: c.tma != null ? Number(Number(c.tma).toFixed(2)) : null,
                upper: c.upper_band != null ? Number(Number(c.upper_band).toFixed(2)) : null,
                lower: c.lower_band != null ? Number(Number(c.lower_band).toFixed(2)) : null
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

        {/* 100% Width Canvas Area — native candlesticks (Lightweight Charts) for
            'tma', Recharts for 'equity' (a plain line, no OHLC needed there). */}
        <div className="w-full h-[360px] sm:h-[440px] pt-12 pb-1 px-1">
          {chartMode === 'tma' ? (
            <PriceChart candles={candles} markers={chartMarkers} activePos={showLivePosition ? activePos : null} showTma={showTma} />
          ) : (
            <ResponsiveContainer width="100%" height="100%">
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
                <Brush dataKey="time" height={22} stroke="#3b82f6" fill="#0f172a" travellerWidth={8} />
              </AreaChart>
            </ResponsiveContainer>
          )}
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
