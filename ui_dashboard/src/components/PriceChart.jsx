import React, { useEffect, useRef } from 'react';
import {
  createChart,
  CandlestickSeries,
  LineSeries,
  LineStyle,
  createSeriesMarkers,
} from 'lightweight-charts';

/**
 * Native Japanese-candlestick price panel (TradingView "Lightweight Charts",
 * pinned exact version — see ui_dashboard/package.json). Recharts (used
 * elsewhere in this dashboard, e.g. the Equity Curve) has no OHLC chart type;
 * this is a small, self-contained wrapper so the rest of AnalyticsTab.jsx stays
 * on Recharts unchanged.
 *
 * Props:
 * - candles: array of {timestamp (epoch ms), open, high, low, close, tma,
 *   upper, lower} — same shape AnalyticsTab already fetches from
 *   /api/v1/history/candles, just no longer stripped down to close-only.
 * - markers: array already in this library's SeriesMarker shape
 *   ({time (epoch seconds), position, shape, color, text}) — built by the
 *   caller (AnalyticsTab) from tradesHistory/addonEvents/activePos, kept out
 *   of this component so it stays a plain, testable rendering wrapper.
 * - activePos: the currently open position for `selectedSymbol` (or null) —
 *   used only for the persistent price-level line (createPriceLine); the
 *   time-of-entry arrow itself is expected to already be part of `markers`.
 * - showTma: whether to render the 3 TMA/upper/lower line series.
 */
export function PriceChart({ candles = [], markers = [], activePos = null, showTma = true }) {
  const containerRef = useRef(null);
  const chartRef = useRef(null);
  const candleSeriesRef = useRef(null);
  const tmaSeriesRef = useRef(null);
  const upperSeriesRef = useRef(null);
  const lowerSeriesRef = useRef(null);
  const markersApiRef = useRef(null);
  const priceLineRef = useRef(null);

  // Mount once. All later prop changes are applied via setData()/setMarkers()
  // in the effects below, never by recreating the chart.
  useEffect(() => {
    const chart = createChart(containerRef.current, {
      layout: {
        background: { color: 'transparent' },
        textColor: '#94a3b8',
        fontFamily: 'ui-monospace, monospace',
        fontSize: 10,
      },
      grid: {
        vertLines: { color: 'rgba(30, 41, 59, 0.6)' },
        horzLines: { color: 'rgba(30, 41, 59, 0.6)' },
      },
      rightPriceScale: { borderColor: '#1e293b' },
      timeScale: { borderColor: '#1e293b', timeVisible: true, secondsVisible: false },
      crosshair: { vertLine: { color: '#334155', width: 1 }, horzLine: { color: '#334155', width: 1 } },
      autoSize: true,
    });

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: '#10b981',
      downColor: '#f43f5e',
      borderVisible: false,
      wickUpColor: '#10b981',
      wickDownColor: '#f43f5e',
    });
    const tmaSeries = chart.addSeries(LineSeries, { color: '#3b82f6', lineWidth: 1.5, priceLineVisible: false, lastValueVisible: false });
    const upperSeries = chart.addSeries(LineSeries, { color: '#ef4444', lineWidth: 1, lineStyle: LineStyle.Dashed, priceLineVisible: false, lastValueVisible: false });
    const lowerSeries = chart.addSeries(LineSeries, { color: '#f59e0b', lineWidth: 1, lineStyle: LineStyle.Dashed, priceLineVisible: false, lastValueVisible: false });
    const markersApi = createSeriesMarkers(candleSeries, []);

    chartRef.current = chart;
    candleSeriesRef.current = candleSeries;
    tmaSeriesRef.current = tmaSeries;
    upperSeriesRef.current = upperSeries;
    lowerSeriesRef.current = lowerSeries;
    markersApiRef.current = markersApi;

    return () => {
      chart.remove();
      chartRef.current = null;
      candleSeriesRef.current = null;
      tmaSeriesRef.current = null;
      upperSeriesRef.current = null;
      lowerSeriesRef.current = null;
      markersApiRef.current = null;
      priceLineRef.current = null;
    };
  }, []);

  // Candle + TMA band data.
  useEffect(() => {
    if (!candleSeriesRef.current || !Array.isArray(candles) || candles.length === 0) return;

    const sorted = [...candles].sort((a, b) => Number(a.timestamp) - Number(b.timestamp));
    const toSec = (ms) => Math.floor(Number(ms) / 1000);

    candleSeriesRef.current.setData(
      sorted.map((c) => ({
        time: toSec(c.timestamp),
        open: Number(c.open),
        high: Number(c.high),
        low: Number(c.low),
        close: Number(c.close),
      }))
    );

    const tmaData = sorted.filter((c) => c.tma != null).map((c) => ({ time: toSec(c.timestamp), value: Number(c.tma) }));
    const upperData = sorted.filter((c) => c.upper != null).map((c) => ({ time: toSec(c.timestamp), value: Number(c.upper) }));
    const lowerData = sorted.filter((c) => c.lower != null).map((c) => ({ time: toSec(c.timestamp), value: Number(c.lower) }));

    if (showTma) {
      tmaSeriesRef.current?.setData(tmaData);
      upperSeriesRef.current?.setData(upperData);
      lowerSeriesRef.current?.setData(lowerData);
    } else {
      tmaSeriesRef.current?.setData([]);
      upperSeriesRef.current?.setData([]);
      lowerSeriesRef.current?.setData([]);
    }

    chartRef.current?.timeScale().fitContent();
  }, [candles, showTma]);

  // Trade/addon/active-position markers.
  useEffect(() => {
    markersApiRef.current?.setMarkers(markers || []);
  }, [markers]);

  // Persistent price-level line for the currently open position (native
  // equivalent of the old Recharts ReferenceLine) — separate from the
  // time-anchored entry marker, which lives in `markers`.
  useEffect(() => {
    const series = candleSeriesRef.current;
    if (!series) return;

    if (priceLineRef.current) {
      series.removePriceLine(priceLineRef.current);
      priceLineRef.current = null;
    }
    if (activePos) {
      const isLong = activePos.side === 'long';
      const pnl = Number(activePos.unrealized_pnl || 0);
      priceLineRef.current = series.createPriceLine({
        price: Number(activePos.entry_price),
        color: isLong ? '#10b981' : '#f43f5e',
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: `AKTYWNA ${activePos.side.toUpperCase()} (uPnL ${pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}$)`,
      });
    }
  }, [activePos]);

  return <div ref={containerRef} className="w-full h-full" />;
}
