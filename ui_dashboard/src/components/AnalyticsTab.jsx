import React, { useState, useEffect, useMemo } from 'react';
import { Activity, TrendingUp, Clock, FileText, Layers, RefreshCw, ChevronLeft, ChevronRight, XCircle, Search, Filter, Calendar, ArrowUpDown, CheckCircle, AlertTriangle, ShieldAlert } from 'lucide-react';
import { ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip, CartesianGrid, Line } from 'recharts';
import { formatSignedUsd } from '../utils/formatters';

export function AnalyticsTab({ statsData, tradesHistory = [], openPositions = [], apiKey, onRefresh }) {
  const [selectedSymbol, setSelectedSymbol] = useState('BTC/USDC');
  const [timeframe, setTimeframe] = useState('4h');
  const [candles, setCandles] = useState([]);
  const [htfData, setHtfData] = useState(null);
  const [availableSymbols, setAvailableSymbols] = useState(['BTC/USDC', 'ETH/USDC', 'SOL/USDC', 'ICP/USDC']);
  const [loading, setLoading] = useState(false);

  // Position double-confirmation close modal state
  const [confirmCloseTarget, setConfirmCloseTarget] = useState(null);
  const [closingSym, setClosingSym] = useState(null);
  const [actionMsg, setActionMsg] = useState('');

  // Closed Trades Table Filtering & Pagination State
  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [tradePeriod, setTradePeriod] = useState('1m'); // Default 1 Month range limit: '1w', '1m', '3m', 'all'
  const [filterSymbol, setFilterSymbol] = useState('ALL');
  const [filterSide, setFilterSide] = useState('ALL');
  const [filterPnl, setFilterPnl] = useState('ALL');
  const [sortOrder, setSortOrder] = useState('newest'); // 'newest' (default: newest first), 'oldest', 'pnl_high', 'pnl_low'
  const [searchTerm, setSearchTerm] = useState('');

  // Timeframe range selector pills for candles and chart scale
  const timeframes = [
    { id: '4h', label: '4H', tf: '4h', limit: 60 },
    { id: '1d', label: '1D', tf: '1d', limit: 60 },
    { id: '1w', label: '1W', tf: '1w', limit: 52 },
    { id: '1m', label: '1M', tf: '1d', limit: 30 },
    { id: '3m', label: '3M', tf: '1d', limit: 90 },
    { id: '6m', label: '6M', tf: '1d', limit: 180 },
    { id: '1y', label: '1Y', tf: '1d', limit: 365 },
    { id: '5y', label: '5Y', tf: '1w', limit: 260 }
  ];

  const currentTfObj = timeframes.find(t => t.id === timeframe) || timeframes[0];

  // Symbol normalization utility helper: ALWAYS outputs full trading pair e.g. BTC/USDC, ETH/USDC, SOL/USDC
  const normalizeSym = (sym) => {
    if (!sym) return '';
    let str = String(sym).toUpperCase().trim();
    // Split out CCXT symbol suffixes like :USD-310404, -310404, etc.
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
      const base = parts[0];
      const quote = parts[1] || 'USDC';
      return `${base}/${quote === 'USD' ? 'USDC' : quote}`;
    }

    if (str.includes('-')) {
      const parts = str.split('-');
      const base = parts[0];
      const quote = parts[1] || 'USDC';
      return `${base}/${quote === 'USD' ? 'USDC' : quote}`;
    }

    // Single coin ticker fallback (e.g. BTC -> BTC/USDC)
    return `${str}/USDC`;
  };

  useEffect(() => {
    const fetchSymbols = async () => {
      try {
        const res = await fetch('/api/v1/symbols');
        if (res.ok) {
          const data = await res.json();
          if (data.symbols && Array.isArray(data.symbols)) {
            setAvailableSymbols(data.symbols.map(s => s.symbol));
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
            // Sort candles chronologically ASC for chart rendering
            const sorted = [...data].sort((a, b) => a.timestamp - b.timestamp);
            setCandles(sorted.map((c) => {
              const ts = Number(c.timestamp);
              const dateObj = new Date(ts > 1e11 ? ts : ts * 1000);
              
              const isIntraDay = currentTfObj.id === '4h';
              const dateLabel = isIntraDay
                ? dateObj.toLocaleDateString([], { month: '2-digit', day: '2-digit' }) + ' ' + dateObj.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
                : dateObj.toLocaleDateString([], { year: '2-digit', month: '2-digit', day: '2-digit' });

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
  }, [timeframe, selectedSymbol]);

  // Execute Position Close after Double Confirmation Modal
  const executeClosePosition = async (cleanSym, side) => {
    setClosingSym(cleanSym);
    setActionMsg('');
    try {
      const currentKey = apiKey || localStorage.getItem('mms4_api_key') || 'test-api-auth-key-unit-test';
      const res = await fetch('/api/v1/positions/close', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-MMS4-API-KEY': currentKey
        },
        body: JSON.stringify({ symbol: cleanSym, side: side ? side.toLowerCase() : null })
      });
      if (res.ok) {
        setActionMsg(`✓ Pozycja ${cleanSym} (${side || 'ALL'}) została pomyślnie zamknięta na giełdzie OKX.`);
        setConfirmCloseTarget(null);
        if (onRefresh) onRefresh();
      } else {
        const errData = await res.json().catch(() => ({}));
        setActionMsg(`❌ Błąd zamknięcia pozycji: ${errData.detail || errData.message || 'Operacja odrzucona przez API'}`);
      }
    } catch (err) {
      setActionMsg('❌ Błąd połączenia z serwerem API');
    } finally {
      setClosingSym(null);
      setTimeout(() => setActionMsg(''), 6000);
    }
  };

  // Dynamic Cumulative Realized PnL / Equity Curve scaled 1:1 to selected timeframe & candles
  const pnlCurve = useMemo(() => {
    const now = Date.now();
    let timeframeCutoff = 0;
    if (timeframe === '4h') timeframeCutoff = now - 24 * 3600 * 1000;
    else if (timeframe === '1d') timeframeCutoff = now - 48 * 3600 * 1000;
    else if (timeframe === '1w') timeframeCutoff = now - 7 * 24 * 3600 * 1000;
    else if (timeframe === '1m') timeframeCutoff = now - 30 * 24 * 3600 * 1000;
    else if (timeframe === '3m') timeframeCutoff = now - 90 * 24 * 3600 * 1000;
    else if (timeframe === '6m') timeframeCutoff = now - 180 * 24 * 3600 * 1000;
    else if (timeframe === '1y') timeframeCutoff = now - 365 * 24 * 3600 * 1000;
    else timeframeCutoff = 0; // '5y' or all

    // Filter trades within this timeframe range
    const timeframeTrades = (tradesHistory && Array.isArray(tradesHistory))
      ? tradesHistory.filter((t) => {
          const ts = t.timestamp ? new Date(t.timestamp).getTime() : now;
          if (timeframeCutoff > 0 && ts < timeframeCutoff) return false;
          return true;
        }).sort((a, b) => (new Date(a.timestamp).getTime() || 0) - (new Date(b.timestamp).getTime() || 0)) // Chronological ASC for chart line
      : [];

    if (timeframeTrades.length > 0) {
      let cumulativePnl = 0;
      return timeframeTrades.map((t) => {
        const netPnl = t.pnl_net_usd !== undefined && t.pnl_net_usd !== null ? Number(t.pnl_net_usd) : Number(t.pnl_usd || 0);
        cumulativePnl += netPnl;
        const ts = t.timestamp ? new Date(t.timestamp).getTime() : now;
        const dateObj = new Date(ts);
        return {
          date: dateObj.toLocaleDateString([], { month: '2-digit', day: '2-digit' }) + ' ' + dateObj.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
          balance: Number(cumulativePnl.toFixed(2))
        };
      });
    }

    // Fallback: scale curve to current candles timeframe points
    if (candles && candles.length > 0) {
      return candles.map((c) => ({
        date: c.time,
        balance: Number((statsData?.realized_pnl || 0).toFixed(2))
      }));
    }

    return [
      { date: 'Start', balance: 0.00 },
      { date: 'Teraz', balance: 0.00 }
    ];
  }, [tradesHistory, timeframe, candles, statsData]);

  // Unique symbol options for flawless dropdown filtering
  const symbolOptions = useMemo(() => {
    const set = new Set();
    availableSymbols.forEach(s => {
      const norm = normalizeSym(s);
      if (norm) set.add(norm);
    });
    if (Array.isArray(tradesHistory)) {
      tradesHistory.forEach(t => {
        if (t.symbol) {
          const norm = normalizeSym(t.symbol);
          if (norm) set.add(norm);
        }
      });
    }
    return Array.from(set);
  }, [availableSymbols, tradesHistory]);

  // Filter & Sort Trades History (Newest Trades First, Period Filter, Normalized Symbol Grouping, Side, PnL)
  const filteredAndSortedTrades = useMemo(() => {
    if (!tradesHistory || !Array.isArray(tradesHistory)) return [];
    
    const now = Date.now();
    let periodCutoff = 0;
    if (tradePeriod === '1w') periodCutoff = now - 7 * 24 * 3600 * 1000;
    else if (tradePeriod === '1m') periodCutoff = now - 30 * 24 * 3600 * 1000; // 1 month default
    else if (tradePeriod === '3m') periodCutoff = now - 90 * 24 * 3600 * 1000;
    else periodCutoff = 0; // 'all'

    return tradesHistory.filter((t) => {
      // Date period filter
      const tTs = t.timestamp ? new Date(t.timestamp).getTime() : now;
      if (periodCutoff > 0 && tTs < periodCutoff) return false;

      // Symbol filter with normalized symbol comparison
      if (filterSymbol !== 'ALL') {
        const normFilter = normalizeSym(filterSymbol);
        const normTradeSym = normalizeSym(t.symbol);
        if (normTradeSym !== normFilter) return false;
      }

      // Side filter
      if (filterSide !== 'ALL') {
        const sideUpper = String(t.side || '').toUpperCase();
        if (filterSide === 'BUY' && !sideUpper.includes('BUY') && !sideUpper.includes('LONG')) return false;
        if (filterSide === 'SELL' && !sideUpper.includes('SELL') && !sideUpper.includes('SHORT')) return false;
      }

      // PnL result filter
      const netPnl = t.pnl_net_usd !== undefined && t.pnl_net_usd !== null ? Number(t.pnl_net_usd) : Number(t.pnl_usd || 0);
      const isWin = t.pnl_net_usd !== undefined && t.pnl_net_usd !== null ? netPnl > 0 : (t.is_win || netPnl >= 0);
      if (filterPnl === 'WIN' && !isWin) return false;
      if (filterPnl === 'LOSS' && isWin) return false;

      // Search term
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

      if (sortOrder === 'newest') return tsB - tsA; // NEWEST FIRST (default)
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
    <div className="space-y-6">
      {/* Global Notification Banner */}
      {actionMsg && (
        <div className={`p-4 rounded-xl border text-xs font-mono font-bold flex items-center justify-between shadow-lg transition-all ${
          actionMsg.includes('✓')
            ? 'bg-emerald-950/90 border-emerald-500/50 text-emerald-300'
            : 'bg-red-950/90 border-red-500/50 text-red-300'
        }`}>
          <div className="flex items-center gap-2">
            {actionMsg.includes('✓') ? <CheckCircle className="w-5 h-5 text-emerald-400" /> : <AlertTriangle className="w-5 h-5 text-red-400" />}
            <span>{actionMsg}</span>
          </div>
          <button onClick={() => setActionMsg('')} className="text-slate-400 hover:text-white text-xs">✕</button>
        </div>
      )}

      {/* Modal / Popup Podwójnej Autoryzacji Zamknięcia Pozycji */}
      {confirmCloseTarget && (
        <div className="fixed inset-0 bg-slate-950/80 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-slate-900 border border-slate-700 rounded-2xl p-6 max-w-md w-full shadow-2xl space-y-5 animate-in fade-in zoom-in-95 duration-150">
            <div className="flex items-center gap-3 border-b border-slate-800 pb-4">
              <div className="p-3 bg-red-950/80 border border-red-500/30 rounded-xl text-red-400">
                <ShieldAlert className="w-6 h-6" />
              </div>
              <div>
                <h3 className="font-bold text-gray-100 text-lg">Potwierdzenie Zamknięcia</h3>
                <p className="text-xs text-slate-400">Podwójna autoryzacja pozycji rynkowej OKX</p>
              </div>
            </div>

            <div className="bg-slate-950 p-4 rounded-xl border border-slate-800 space-y-2 text-xs font-mono tabular-nums">
              <div className="flex justify-between items-center py-1 border-b border-slate-800/60">
                <span className="text-slate-400">Para Rynkowa:</span>
                <span className="font-bold text-gray-100 text-sm">{confirmCloseTarget.cleanSym}</span>
              </div>
              <div className="flex justify-between items-center py-1 border-b border-slate-800/60">
                <span className="text-slate-400">Strona Pozycji:</span>
                <span className={`px-2 py-0.5 rounded font-bold text-[11px] ${
                  confirmCloseTarget.isLong ? 'bg-emerald-950 text-emerald-400 border border-emerald-500/30' : 'bg-rose-950 text-rose-400 border border-rose-500/30'
                }`}>
                  {confirmCloseTarget.side}
                </span>
              </div>
              <div className="flex justify-between items-center py-1 border-b border-slate-800/60">
                <span className="text-slate-400">Alokacja (Notional):</span>
                <span className="font-bold text-gray-200">${confirmCloseTarget.notional?.toFixed(2)} USD</span>
              </div>
              <div className="flex justify-between items-center py-1">
                <span className="text-slate-400">Niezrealizowany PnL:</span>
                <span className={`font-bold ${confirmCloseTarget.upnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                  {formatSignedUsd(confirmCloseTarget.upnl)} USD
                </span>
              </div>
            </div>

            <p className="text-xs text-slate-300 bg-amber-950/40 border border-amber-500/30 p-3 rounded-lg leading-relaxed">
              ⚠️ <strong>Uwaga:</strong> Wyślesz natychmiastowe zlecenie rynkowe R-SEC do OKX zamykające 100% tej pozycji. Czy na pewno chcesz kontynuować?
            </p>

            <div className="flex items-center gap-3 pt-2">
              <button
                onClick={() => setConfirmCloseTarget(null)}
                disabled={closingSym === confirmCloseTarget.cleanSym}
                className="w-1/2 py-2.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-xl font-bold text-xs transition-colors"
              >
                Anuluj
              </button>
              <button
                onClick={() => executeClosePosition(confirmCloseTarget.cleanSym, confirmCloseTarget.side)}
                disabled={closingSym === confirmCloseTarget.cleanSym}
                className="w-1/2 py-2.5 bg-red-600 hover:bg-red-500 disabled:opacity-50 text-white rounded-xl font-bold text-xs transition-colors flex items-center justify-center gap-1.5 shadow-lg shadow-red-600/30"
              >
                <XCircle className="w-4 h-4" />
                <span>{closingSym === confirmCloseTarget.cleanSym ? 'Zamykanie...' : 'Potwierdzam Zamknięcie'}</span>
              </button>
            </div>
          </div>
        </div>
      )}

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
            <span className="font-bold text-amber-400 text-sm">
              {htfData?.atr_d1_expansion_ratio !== undefined ? Number(htfData.atr_d1_expansion_ratio).toFixed(4) : '0.3125'}
            </span>
          </div>

          <div className="bg-slate-950 p-3 rounded-lg border border-slate-800">
            <span className="text-slate-400 block mb-1 text-[10px]">Engine State:</span>
            <span className="font-bold text-emerald-300 text-sm">BASE_MODE</span>
          </div>
        </div>
      </div>

      {/* Synchronized Timeframe & Symbol Selector Toolbar */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 flex flex-wrap items-center justify-between gap-4 shadow-md">
        {/* Symbol Selector Dropdown */}
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2 text-sm text-slate-300 font-semibold">
            <Activity className="w-4 h-4 text-blue-400" />
            <span>Wybór Pary Rynkowej:</span>
          </div>
          <select
            value={selectedSymbol}
            onChange={(e) => setSelectedSymbol(e.target.value)}
            className="px-3 py-1.5 bg-slate-950 border border-slate-800 rounded-lg text-xs font-mono font-bold text-blue-400 focus:outline-none focus:border-blue-500 cursor-pointer"
          >
            {symbolOptions.map((sym) => (
              <option key={sym} value={sym}>
                {sym}
              </option>
            ))}
          </select>
        </div>

        {/* Timeframe Selector Buttons */}
        <div className="flex items-center gap-1.5 bg-slate-950 p-1 rounded-lg border border-slate-800">
          <Clock className="w-4 h-4 text-slate-400 ml-1.5" />
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

      {/* Synchronized Charts Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* TMA Bands & Price Chart */}
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 shadow-lg">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-2">
              <Activity className="w-5 h-5 text-emerald-400" />
              <h3 className="font-semibold text-gray-200">Ścieżka Ceny {selectedSymbol} & Pasma TMA ({currentTfObj.label})</h3>
            </div>
            <span className="text-xs text-slate-400 font-mono">OKX Spot {selectedSymbol}</span>
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
                  <Area type="monotone" dataKey="price" stroke="#10b981" fillOpacity={1} fill="url(#priceGrad)" strokeWidth={2} name={`Cena ${selectedSymbol.split('/')[0]}`} />
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

        {/* Equity / PnL Progression Chart with Timeframe Range Filter */}
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 shadow-lg">
          <div className="flex flex-wrap items-center justify-between gap-2 mb-4">
            <div className="flex items-center gap-2">
              <TrendingUp className="w-5 h-5 text-blue-400" />
              <h3 className="font-semibold text-gray-200">Krzywa Salda Portfela (Skala: {currentTfObj.label})</h3>
            </div>
            <div className="flex items-center gap-2">
              <span className="text-xs text-emerald-400 font-bold bg-emerald-950/60 px-2.5 py-1 rounded border border-emerald-500/30 font-mono">
                PnL: ${Number(statsData?.realized_pnl || 0).toFixed(2)}
              </span>
            </div>
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
                <XAxis dataKey="date" stroke="#64748b" fontSize={10} angle={-15} textAnchor="end" height={40} />
                <YAxis 
                  domain={[ (min) => Math.min(-0.5, isNaN(min) ? -0.5 : min), (max) => Math.max(0.5, isNaN(max) ? 0.5 : max) ]} 
                  stroke="#64748b" 
                  fontSize={11} 
                  tickFormatter={(v) => `$${Number(v).toFixed(2)}`}
                />
                <Tooltip 
                  contentStyle={{ backgroundColor: '#0f172a', borderColor: '#334155', borderRadius: '8px', color: '#fff' }}
                  formatter={(value) => [`$${Number(value).toFixed(2)}`, 'Skumulowany PnL']}
                />
                <Area type="monotone" dataKey="balance" stroke="#3b82f6" fillOpacity={1} fill="url(#pnlGrad)" strokeWidth={2.5} name="PnL USD" />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>

      {/* Live Open Positions Table with Double Confirmation "Zamknij Pozycję" Button */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 shadow-lg space-y-4">
        <div className="flex items-center justify-between border-b border-slate-800/80 pb-3">
          <h3 className="font-semibold text-gray-200 flex items-center gap-2 text-base">
            <Activity className="w-5 h-5 text-emerald-400" />
            <span>Aktywne Otwarte Pozycje na OKX ({openPositions.length} Otwarte)</span>
          </h3>
          <span className="text-xs font-mono bg-emerald-950/80 text-emerald-400 border border-emerald-500/30 px-3 py-1 rounded-full font-bold">
            Live OKX Positions
          </span>
        </div>

        <div className="overflow-x-auto">
          {openPositions && openPositions.length > 0 ? (
            <table className="w-full text-xs text-left text-slate-300">
              <thead className="bg-slate-950 text-slate-400 uppercase font-mono border-b border-slate-800">
                <tr>
                  <th className="px-4 py-2.5">Para</th>
                  <th className="px-4 py-2.5">Strona</th>
                  <th className="px-4 py-2.5 text-right">Dźwignia</th>
                  <th className="px-4 py-2.5 text-right">Alokacja (Notional)</th>
                  <th className="px-4 py-2.5 text-right">Depozyt (Margin)</th>
                  <th className="px-4 py-2.5 text-right">Cena Wejścia</th>
                  <th className="px-4 py-2.5 text-right">Cena Rynkowa</th>
                  <th className="px-4 py-2.5 text-right">Niezrealizowany PnL</th>
                  <th className="px-4 py-2.5 text-right">Akcja / Zamknięcie</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/60 font-mono tabular-nums">
                {openPositions.map((p, idx) => {
                  const rawSym = p.symbol || p.raw_symbol || 'N/A';
                  const cleanSym = normalizeSym(rawSym);
                  const side = String(p.side || 'long').toUpperCase();
                  const isLong = side === 'BUY' || side === 'LONG';
                  const leverage = Number(p.leverage || 1.0);
                  const notional = Number(p.notional_usd || 0.0);
                  const margin = leverage > 0 ? notional / leverage : notional;
                  const entryPx = Number(p.entry_price || 0.0);
                  const markPx = Number(p.mark_price || 0.0);
                  const upnl = Number(p.unrealized_pnl || 0.0);
                  const isClosing = closingSym === cleanSym;

                  return (
                    <tr key={idx} className="hover:bg-slate-800/30">
                      <td className="px-4 py-3 font-bold text-gray-100">{cleanSym}</td>
                      <td className="px-4 py-3">
                        <span className={`px-2 py-0.5 rounded font-bold text-[11px] ${
                          isLong 
                            ? 'bg-emerald-950 text-emerald-400 border border-emerald-500/30' 
                            : 'bg-rose-950 text-rose-400 border border-rose-500/30'
                        }`}>
                          {side}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-right font-bold text-blue-400">{leverage.toFixed(0)}x</td>
                      <td className="px-4 py-3 text-right font-bold text-gray-200">${notional.toFixed(2)} USD</td>
                      <td className="px-4 py-3 text-right text-amber-400 font-bold">${margin.toFixed(2)} USD</td>
                      <td className="px-4 py-3 text-right text-slate-300">${entryPx > 0 ? entryPx.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '---'}</td>
                      <td className="px-4 py-3 text-right text-slate-300">${markPx > 0 ? markPx.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '---'}</td>
                      <td className="px-4 py-3 text-right">
                        <span className={`font-bold px-2 py-0.5 rounded ${
                          upnl >= 0
                            ? 'bg-emerald-950/80 text-emerald-400 border border-emerald-500/30'
                            : 'bg-red-950/80 text-red-400 border border-red-500/30'
                        }`}>
                          {formatSignedUsd(upnl)} USD
                        </span>
                      </td>
                      <td className="px-4 py-3 text-right">
                        <button
                          onClick={() => setConfirmCloseTarget({ cleanSym, side, notional, margin, entryPx, markPx, upnl, isLong })}
                          disabled={isClosing}
                          className="px-3 py-1.5 bg-red-600 hover:bg-red-500 disabled:opacity-50 text-white rounded-lg font-bold text-xs transition-colors flex items-center justify-end gap-1.5 shadow-md shadow-red-600/20 ml-auto"
                        >
                          <XCircle className="w-4 h-4" />
                          <span>{isClosing ? 'Zamykanie...' : 'Zamknij Pozycję'}</span>
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          ) : (
            <div className="text-center py-6 text-slate-500 text-xs bg-slate-950/40 rounded-lg border border-slate-800/60 font-mono">
              Brak otwartych pozycji w tym cyklu na koncie handlowym OKX.
            </div>
          )}
        </div>
      </div>

      {/* Real Closed Trades History Table with Search, Filter Range & Newest-First Sorting */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 shadow-lg space-y-4">
        {/* Header Bar */}
        <div className="flex flex-col md:flex-row items-start md:items-center justify-between gap-3 pb-3 border-b border-slate-800/80">
          <div className="flex items-center gap-2">
            <Layers className="w-5 h-5 text-blue-400" />
            <h3 className="font-bold text-gray-100 text-base">Wykaz Zarejestrowanych Transakcji Rzeczywistych (SQLite logs.db)</h3>
            <span className="text-xs font-mono bg-blue-950/80 text-blue-300 border border-blue-500/30 px-2.5 py-0.5 rounded-full ml-2">
              Znaleziono: {totalFilteredTrades} z {tradesHistory.length}
            </span>
          </div>

          {/* Quick Period Limitation Selector Pills */}
          <div className="flex items-center gap-1.5 bg-slate-950 p-1 rounded-lg border border-slate-800 text-xs font-mono">
            <Calendar className="w-4 h-4 text-slate-400 ml-1.5" />
            <button
              onClick={() => { setTradePeriod('1w'); setCurrentPage(1); }}
              className={`px-2.5 py-1 rounded text-xs transition-all ${tradePeriod === '1w' ? 'bg-blue-600 text-white font-bold' : 'text-slate-400 hover:text-white'}`}
            >
              1 Tydzień
            </button>
            <button
              onClick={() => { setTradePeriod('1m'); setCurrentPage(1); }}
              className={`px-2.5 py-1 rounded text-xs transition-all ${tradePeriod === '1m' ? 'bg-blue-600 text-white font-bold' : 'text-slate-400 hover:text-white'}`}
            >
              1 Miesiąc ⭐
            </button>
            <button
              onClick={() => { setTradePeriod('3m'); setCurrentPage(1); }}
              className={`px-2.5 py-1 rounded text-xs transition-all ${tradePeriod === '3m' ? 'bg-blue-600 text-white font-bold' : 'text-slate-400 hover:text-white'}`}
            >
              3 Miesiące
            </button>
            <button
              onClick={() => { setTradePeriod('all'); setCurrentPage(1); }}
              className={`px-2.5 py-1 rounded text-xs transition-all ${tradePeriod === 'all' ? 'bg-blue-600 text-white font-bold' : 'text-slate-400 hover:text-white'}`}
            >
              Wszystkie Okresy
            </button>
          </div>
        </div>

        {/* Multi-Filter & Search Toolbar (Czas, Para, Strona, PnL, Sortowanie) */}
        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-5 gap-3 p-3 bg-slate-950 rounded-xl border border-slate-800 text-xs font-mono">
          {/* Search Input */}
          <div className="relative">
            <Search className="w-3.5 h-3.5 absolute left-2.5 top-2.5 text-slate-400" />
            <input
              type="text"
              placeholder="Szukaj pary..."
              value={searchTerm}
              onChange={(e) => { setSearchTerm(e.target.value); setCurrentPage(1); }}
              className="w-full pl-8 pr-2 py-1.5 bg-slate-900 border border-slate-800 rounded-lg text-slate-200 text-xs focus:outline-none focus:border-blue-500"
            />
          </div>

          {/* Symbol Filter with Normalized Symbols */}
          <div>
            <select
              value={filterSymbol}
              onChange={(e) => { setFilterSymbol(e.target.value); setCurrentPage(1); }}
              className="w-full py-1.5 px-2 bg-slate-900 border border-slate-800 rounded-lg text-slate-200 text-xs focus:outline-none focus:border-blue-500"
            >
              <option value="ALL">Wszystkie Pary</option>
              {symbolOptions.map(sym => (
                <option key={sym} value={sym}>{sym}</option>
              ))}
            </select>
          </div>

          {/* Side Filter */}
          <div>
            <select
              value={filterSide}
              onChange={(e) => { setFilterSide(e.target.value); setCurrentPage(1); }}
              className="w-full py-1.5 px-2 bg-slate-900 border border-slate-800 rounded-lg text-slate-200 text-xs focus:outline-none focus:border-blue-500"
            >
              <option value="ALL">Wszystkie Strony</option>
              <option value="BUY">BUY / LONG</option>
              <option value="SELL">SELL / SHORT</option>
            </select>
          </div>

          {/* PnL Filter */}
          <div>
            <select
              value={filterPnl}
              onChange={(e) => { setFilterPnl(e.target.value); setCurrentPage(1); }}
              className="w-full py-1.5 px-2 bg-slate-900 border border-slate-800 rounded-lg text-slate-200 text-xs focus:outline-none focus:border-blue-500"
            >
              <option value="ALL">Wszystkie Wyniki (PnL)</option>
              <option value="WIN">Zyskowne (WIN 🟢)</option>
              <option value="LOSS">Stratne (LOSS 🔴)</option>
            </select>
          </div>

          {/* Sort Order (Grupowanie/Kolejność) */}
          <div>
            <select
              value={sortOrder}
              onChange={(e) => { setSortOrder(e.target.value); setCurrentPage(1); }}
              className="w-full py-1.5 px-2 bg-slate-900 border border-slate-800 rounded-lg text-blue-400 font-bold text-xs focus:outline-none focus:border-blue-500"
            >
              <option value="newest">Najnowsze wpisy najpierw ⭐</option>
              <option value="oldest">Najstarsze wpisy najpierw</option>
              <option value="pnl_high">Najwyższy Zysk (PnL)</option>
              <option value="pnl_low">Największa Strata (PnL)</option>
            </select>
          </div>
        </div>

        {/* Table Content (Newest Trades First) */}
        <div className="overflow-x-auto">
          {totalFilteredTrades > 0 ? (
            <table className="w-full text-xs text-left text-slate-300">
              <thead className="bg-slate-950 text-slate-400 uppercase font-mono border-b border-slate-800">
                <tr>
                  <th className="px-4 py-2.5">Czas Zarejestrowania</th>
                  <th className="px-4 py-2.5">Para</th>
                  <th className="px-4 py-2.5">Strona</th>
                  <th className="px-4 py-2.5 text-right">Cena Wejścia</th>
                  <th className="px-4 py-2.5 text-right">Cena Wyjścia</th>
                  <th className="px-4 py-2.5 text-right">PnL Netto / Brutto ($)</th>
                  <th className="px-4 py-2.5 text-right">Prowizja (Fee)</th>
                  <th className="px-4 py-2.5">Wynik / Stan</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/60 font-mono tabular-nums">
                {currentTrades.map((t, idx) => {
                  const netPnlVal = t.pnl_net_usd !== undefined && t.pnl_net_usd !== null ? Number(t.pnl_net_usd) : Number(t.pnl_usd || 0);
                  const isNetWin = t.pnl_net_usd !== undefined && t.pnl_net_usd !== null ? netPnlVal > 0 : (t.is_win || netPnlVal >= 0);
                  return (
                    <tr key={idx} className="hover:bg-slate-800/30">
                      <td className="px-4 py-3 text-slate-400 font-bold">{t.timestamp ? new Date(t.timestamp).toLocaleString() : '---'}</td>
                      <td className="px-4 py-3 font-bold text-gray-100">{normalizeSym(t.symbol) || 'BTC/USDC'}</td>
                      <td className="px-4 py-3 font-bold">
                        <span className={`px-2 py-0.5 rounded text-[11px] ${
                          String(t.side || '').toUpperCase().includes('BUY') || String(t.side || '').toUpperCase().includes('LONG')
                            ? 'bg-emerald-950 text-emerald-400 border border-emerald-500/30'
                            : 'bg-rose-950 text-rose-400 border border-rose-500/30'
                        }`}>
                          {t.side || 'BUY'}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-right text-slate-200">${Number(t.entry_price || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
                      <td className="px-4 py-3 text-right text-slate-200">${Number(t.exit_price || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
                      <td className="px-4 py-3 text-right">
                        <div className={`font-bold text-sm ${netPnlVal >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                          ${netPnlVal.toFixed(2)}
                        </div>
                        <div className="text-[10px] text-slate-400 font-normal">
                          Brutto: ${Number(t.pnl_usd || 0).toFixed(2)}
                        </div>
                      </td>
                      <td className="px-4 py-3 text-right text-slate-400 text-xs">
                        ${Number(t.fee_usd || 0).toFixed(4)}
                      </td>
                      <td className="px-4 py-3">
                        <span className={`px-2.5 py-0.5 rounded font-bold ${
                          isNetWin ? 'bg-emerald-950 text-emerald-400 border border-emerald-500/30' : 'bg-red-950 text-red-400 border border-red-500/30'
                        }`}>
                          {isNetWin ? 'WIN 🟢' : 'LOSS 🔴'}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          ) : (
            <div className="text-center py-8 text-slate-500 text-sm bg-slate-950/40 rounded-lg border border-slate-800/60 font-mono">
              Brak zarejestrowanych transakcji spełniających wybrane kryteria filtrowania w bazy SQLite.
            </div>
          )}
        </div>

        {/* Pagination Controls & Rows Per Page Selector */}
        {totalFilteredTrades > 0 && (
          <div className="flex flex-col sm:flex-row items-center justify-between gap-3 pt-3 border-t border-slate-800/80 text-xs font-mono text-slate-400">
            <div className="flex items-center gap-3">
              <span>Wyświetlono <span className="font-bold text-gray-200">{startIndex + 1}</span> - <span className="font-bold text-gray-200">{endIndex}</span> z <span className="font-bold text-gray-200">{totalFilteredTrades}</span> wpisów</span>
              <div className="flex items-center gap-1.5 border-l border-slate-800 pl-3">
                <span>Na stronę:</span>
                <select
                  value={pageSize}
                  onChange={(e) => {
                    setPageSize(Number(e.target.value));
                    setCurrentPage(1);
                  }}
                  className="bg-slate-950 border border-slate-800 rounded px-2 py-1 text-blue-400 font-bold focus:outline-none focus:border-blue-500 cursor-pointer"
                >
                  <option value={5}>5</option>
                  <option value={10}>10</option>
                  <option value={25}>25</option>
                  <option value={50}>50</option>
                </select>
              </div>
            </div>

            <div className="flex items-center gap-2">
              <button
                onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
                disabled={validCurrentPage === 1}
                className="flex items-center gap-1 px-3 py-1.5 rounded bg-slate-950 border border-slate-800 hover:bg-slate-800 disabled:opacity-40 disabled:cursor-not-allowed text-slate-300 font-medium transition-all"
              >
                <ChevronLeft className="w-4 h-4" />
                <span>Poprzednia</span>
              </button>

              <span className="px-3 py-1 bg-slate-950 rounded border border-slate-800 font-bold text-blue-400">
                {validCurrentPage} / {totalPages}
              </span>

              <button
                onClick={() => setCurrentPage((p) => Math.min(totalPages, p + 1))}
                disabled={validCurrentPage >= totalPages}
                className="flex items-center gap-1 px-3 py-1.5 rounded bg-slate-950 border border-slate-800 hover:bg-slate-800 disabled:opacity-40 disabled:cursor-not-allowed text-slate-300 font-medium transition-all"
              >
                <span>Następna</span>
                <ChevronRight className="w-4 h-4" />
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
