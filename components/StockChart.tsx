'use client';

import React, { useEffect, useRef, useState } from 'react';
import {
  createChart,
  CandlestickSeries,
  HistogramSeries,
  LineSeries,
  ColorType,
  IChartApi,
  ISeriesApi,
} from 'lightweight-charts';
import { X, TrendingUp, BarChart2, Layers } from 'lucide-react';

export interface BarData {
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  vwap?: number;
}

export interface StockChartProps {
  symbol: string;
  name: string;
  sector: string;
  closePrice: number;
  rvol: number;
  rsi: number;
  bars: BarData[];
  onClose: () => void;
}

// Compute Simple Moving Average (SMA)
function calculateSMA(data: BarData[], period: number) {
  const result: { time: string; value: number }[] = [];
  for (let i = period - 1; i < data.length; i++) {
    let sum = 0;
    for (let j = 0; j < period; j++) {
      sum += data[i - j].close;
    }
    result.push({
      time: data[i].timestamp,
      value: Number((sum / period).toFixed(2)),
    });
  }
  return result;
}

// Compute Exponential Moving Average (EMA)
function calculateEMA(data: BarData[], period: number) {
  const result: { time: string; value: number }[] = [];
  if (data.length < period) return result;

  const multiplier = 2 / (period + 1);

  // Initial SMA as first EMA
  let initialSum = 0;
  for (let i = 0; i < period; i++) {
    initialSum += data[i].close;
  }
  let currentEMA = initialSum / period;
  result.push({
    time: data[period - 1].timestamp,
    value: Number(currentEMA.toFixed(2)),
  });

  for (let i = period; i < data.length; i++) {
    currentEMA = (data[i].close - currentEMA) * multiplier + currentEMA;
    result.push({
      time: data[i].timestamp,
      value: Number(currentEMA.toFixed(2)),
    });
  }
  return result;
}

export default function StockChart({
  symbol,
  name,
  sector,
  closePrice,
  rvol,
  rsi,
  bars,
  onClose,
}: StockChartProps) {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartInstanceRef = useRef<IChartApi | null>(null);
  const [showIndicators, setShowIndicators] = useState({
    ema20: true,
    sma50: true,
    sma200: true,
  });

  useEffect(() => {
    if (!chartContainerRef.current || !bars || bars.length === 0) return;

    // Sort bars chronologically
    const sortedBars = [...bars].sort(
      (a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime()
    );

    // Initialize Chart
    const chart = createChart(chartContainerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: '#0b111e' },
        textColor: '#94a3b8',
        fontSize: 12,
        fontFamily: "'Inter', system-ui, -apple-system, sans-serif",
      },
      grid: {
        vertLines: { color: 'rgba(35, 48, 76, 0.4)' },
        horzLines: { color: 'rgba(35, 48, 76, 0.4)' },
      },
      crosshair: {
        mode: 1, // Magnet
        vertLine: {
          color: '#475569',
          width: 1,
          style: 3,
          labelBackgroundColor: '#1e293b',
        },
        horzLine: {
          color: '#475569',
          width: 1,
          style: 3,
          labelBackgroundColor: '#1e293b',
        },
      },
      rightPriceScale: {
        borderColor: '#23304c',
        scaleMargins: {
          top: 0.1,
          bottom: 0.25, // Leaves bottom room for volume overlay
        },
      },
      timeScale: {
        borderColor: '#23304c',
        timeVisible: false,
        secondsVisible: false,
      },
      handleScroll: true,
      handleScale: true,
    });

    chartInstanceRef.current = chart;

    // 1. Candlestick Series
    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: '#10b981', // Neon emerald
      downColor: '#ef4444', // Red
      borderUpColor: '#10b981',
      borderDownColor: '#ef4444',
      wickUpColor: '#10b981',
      wickDownColor: '#ef4444',
    });

    candleSeries.setData(
      sortedBars.map((b) => ({
        time: b.timestamp,
        open: b.open,
        high: b.high,
        low: b.low,
        close: b.close,
      }))
    );

    // 2. Volume Histogram Overlay
    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceFormat: {
        type: 'volume',
      },
      priceScaleId: 'volume_scale',
    });

    chart.priceScale('volume_scale').applyOptions({
      scaleMargins: {
        top: 0.75, // Keeps volume at bottom 25% of the chart
        bottom: 0,
      },
    });

    volumeSeries.setData(
      sortedBars.map((b) => ({
        time: b.timestamp,
        value: b.volume,
        color: b.close >= b.open ? 'rgba(16, 185, 129, 0.4)' : 'rgba(239, 68, 68, 0.4)',
      }))
    );

    // 3. Technical Indicators
    let ema20Series: ISeriesApi<'Line'> | null = null;
    let sma50Series: ISeriesApi<'Line'> | null = null;
    let sma200Series: ISeriesApi<'Line'> | null = null;

    if (showIndicators.ema20) {
      ema20Series = chart.addSeries(LineSeries, {
        color: '#3b82f6', // Bright Blue
        lineWidth: 2,
        title: 'EMA 20',
      });
      ema20Series.setData(calculateEMA(sortedBars, 20));
    }

    if (showIndicators.sma50) {
      sma50Series = chart.addSeries(LineSeries, {
        color: '#eab308', // Yellow
        lineWidth: 2,
        title: 'SMA 50',
      });
      sma50Series.setData(calculateSMA(sortedBars, 50));
    }

    if (showIndicators.sma200) {
      sma200Series = chart.addSeries(LineSeries, {
        color: '#a855f7', // Purple
        lineWidth: 2,
        title: 'SMA 200',
      });
      sma200Series.setData(calculateSMA(sortedBars, 200));
    }

    // Fit chart viewport to data
    chart.timeScale().fitContent();

    // Resize Handler
    const handleResize = () => {
      if (chartContainerRef.current) {
        chart.applyOptions({
          width: chartContainerRef.current.clientWidth,
          height: chartContainerRef.current.clientHeight,
        });
      }
    };

    window.addEventListener('resize', handleResize);
    handleResize();

    return () => {
      window.removeEventListener('resize', handleResize);
      chart.remove();
      chartInstanceRef.current = null;
    };
  }, [bars, showIndicators]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-4 sm:p-6 animate-in fade-in duration-200">
      <div className="relative flex flex-col w-full max-w-5xl h-[85vh] bg-[#0c121e] border border-slate-700/60 rounded-xl shadow-2xl overflow-hidden">
        {/* Modal Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-800 bg-[#0f172a]/70">
          <div className="flex items-center space-x-4">
            <div className="w-10 h-10 rounded-lg bg-emerald-500/10 border border-emerald-500/30 flex items-center justify-center text-emerald-400 font-bold text-lg">
              {symbol.slice(0, 3)}
            </div>
            <div>
              <div className="flex items-center space-x-2">
                <h3 className="text-xl font-bold tracking-tight text-white">{symbol}</h3>
                <span className="text-xs px-2 py-0.5 rounded bg-slate-800 text-slate-400 font-medium">
                  {sector}
                </span>
                <span className="text-xs px-2 py-0.5 rounded bg-emerald-950/80 text-emerald-400 border border-emerald-500/30 font-semibold flex items-center gap-1">
                  <TrendingUp className="w-3 h-3" />
                  Momentum Breakout
                </span>
              </div>
              <p className="text-xs text-slate-400 mt-0.5">{name}</p>
            </div>
          </div>

          <div className="flex items-center space-x-6">
            <div className="hidden sm:flex items-center space-x-5 text-right">
              <div>
                <span className="text-[10px] uppercase tracking-wider text-slate-500 block">Close</span>
                <span className="text-base font-semibold text-slate-100 font-mono">
                  ${closePrice ? closePrice.toFixed(2) : '—'}
                </span>
              </div>
              <div>
                <span className="text-[10px] uppercase tracking-wider text-slate-500 block">RVOL</span>
                <span className="text-base font-semibold text-emerald-400 font-mono">
                  {rvol ? `${rvol.toFixed(2)}x` : '—'}
                </span>
              </div>
              <div>
                <span className="text-[10px] uppercase tracking-wider text-slate-500 block">RSI (14)</span>
                <span className="text-base font-semibold text-cyan-400 font-mono">
                  {rsi ? rsi.toFixed(1) : '—'}
                </span>
              </div>
            </div>

            <button
              onClick={onClose}
              className="p-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-slate-800 transition"
              aria-label="Close modal"
            >
              <X className="w-6 h-6" />
            </button>
          </div>
        </div>

        {/* Legend & Indicator Toggle Controls */}
        <div className="flex flex-wrap items-center justify-between px-6 py-2.5 bg-[#090e18] border-b border-slate-800/80 text-xs">
          <div className="flex items-center space-x-4">
            <span className="text-slate-500 flex items-center gap-1.5 font-medium">
              <Layers className="w-3.5 h-3.5" /> Overlays:
            </span>
            <button
              onClick={() => setShowIndicators((p) => ({ ...p, ema20: !p.ema20 }))}
              className={`flex items-center space-x-1.5 px-2.5 py-1 rounded border transition ${
                showIndicators.ema20
                  ? 'border-blue-500/50 bg-blue-500/10 text-blue-400'
                  : 'border-slate-800 text-slate-600'
              }`}
            >
              <span className="w-2 h-2 rounded-full bg-blue-500 inline-block" />
              <span>20-EMA</span>
            </button>
            <button
              onClick={() => setShowIndicators((p) => ({ ...p, sma50: !p.sma50 }))}
              className={`flex items-center space-x-1.5 px-2.5 py-1 rounded border transition ${
                showIndicators.sma50
                  ? 'border-yellow-500/50 bg-yellow-500/10 text-yellow-400'
                  : 'border-slate-800 text-slate-600'
              }`}
            >
              <span className="w-2 h-2 rounded-full bg-yellow-400 inline-block" />
              <span>50-SMA</span>
            </button>
            <button
              onClick={() => setShowIndicators((p) => ({ ...p, sma200: !p.sma200 }))}
              className={`flex items-center space-x-1.5 px-2.5 py-1 rounded border transition ${
                showIndicators.sma200
                  ? 'border-purple-500/50 bg-purple-500/10 text-purple-400'
                  : 'border-slate-800 text-slate-600'
              }`}
            >
              <span className="w-2 h-2 rounded-full bg-purple-500 inline-block" />
              <span>200-SMA</span>
            </button>
          </div>

          <div className="flex items-center space-x-2 text-slate-500 text-[11px]">
            <BarChart2 className="w-3.5 h-3.5 text-slate-400" />
            <span>Volume Sub-chart (Green/Red)</span>
            <span className="text-slate-700">|</span>
            <span>{bars.length} daily bars loaded</span>
          </div>
        </div>

        {/* Chart Canvas Container */}
        <div className="relative flex-1 w-full bg-[#0b111e] min-h-[350px]">
          {bars && bars.length > 0 ? (
            <div ref={chartContainerRef} className="w-full h-full" />
          ) : (
            <div className="flex flex-col items-center justify-center h-full text-slate-500">
              <BarChart2 className="w-12 h-12 mb-2 stroke-[1.5] text-slate-600" />
              <p>No historical daily bars available for this symbol.</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
