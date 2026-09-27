'use client';

import React, { useEffect, useRef, useState, useMemo } from 'react';
import {
  createChart,
  CandlestickSeries,
  HistogramSeries,
  LineSeries,
  LineStyle,
  ColorType,
  IChartApi,
  ISeriesApi,
} from 'lightweight-charts';
import {
  X,
  TrendingUp,
  BarChart2,
  Layers,
  ShieldAlert,
  ChevronDown,
  ChevronUp,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  Info,
} from 'lucide-react';

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
  signal?: any;
}

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

function calculateEMA(data: BarData[], period: number) {
  const result: { time: string; value: number }[] = [];
  if (data.length < period) return result;

  const multiplier = 2 / (period + 1);
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

function calculateVWAP(data: BarData[]) {
  const result: { time: string; value: number }[] = [];
  let cumulativeTypicalPriceVolume = 0;
  let cumulativeVolume = 0;

  for (let i = 0; i < data.length; i++) {
    const b = data[i];
    if (b.vwap && b.vwap > 0) {
      result.push({
        time: b.timestamp,
        value: Number(b.vwap.toFixed(2)),
      });
    } else {
      const typicalPrice = (b.high + b.low + b.close) / 3;
      const vol = b.volume > 0 ? b.volume : 1;
      cumulativeTypicalPriceVolume += typicalPrice * vol;
      cumulativeVolume += vol;
      const vwapVal = cumulativeTypicalPriceVolume / cumulativeVolume;
      result.push({
        time: b.timestamp,
        value: Number(vwapVal.toFixed(2)),
      });
    }
  }
  return result;
}

function calculateATRStop(data: BarData[], period = 14, multiplier = 2) {
  const result: { time: string; value: number }[] = [];
  if (data.length <= period) return result;

  const tr: number[] = [data[0].high - data[0].low];
  for (let i = 1; i < data.length; i++) {
    const highLow = data[i].high - data[i].low;
    const highPrevClose = Math.abs(data[i].high - data[i - 1].close);
    const lowPrevClose = Math.abs(data[i].low - data[i - 1].close);
    tr.push(Math.max(highLow, highPrevClose, lowPrevClose));
  }

  let atr = 0;
  for (let i = 0; i < period; i++) {
    atr += tr[i];
  }
  atr = atr / period;

  let trailingStop = data[period - 1].close - multiplier * atr;
  result.push({
    time: data[period - 1].timestamp,
    value: Number(Math.max(0, trailingStop).toFixed(2)),
  });

  for (let i = period; i < data.length; i++) {
    atr = (atr * (period - 1) + tr[i]) / period;
    const currentStop = data[i].close - multiplier * atr;
    if (data[i - 1].close > trailingStop) {
      trailingStop = Math.max(trailingStop, currentStop);
    } else {
      trailingStop = currentStop;
    }
    result.push({
      time: data[i].timestamp,
      value: Number(Math.max(0, trailingStop).toFixed(2)),
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
  signal,
}: StockChartProps) {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartInstanceRef = useRef<IChartApi | null>(null);

  const [showIndicators, setShowIndicators] = useState({
    ema20: true,
    sma50: true,
    sma200: true,
    vwap: true,
    atrStop: true,
  });

  // Collapsible panels
  const [showWhyTriggered, setShowWhyTriggered] = useState(false);
  const [showRiskCalc, setShowRiskCalc] = useState(false);

  // Risk Calculator inputs
  const [accountSize, setAccountSize] = useState<number>(10000);
  const [riskPct, setRiskPct] = useState<number>(1.0);

  // Compute ATR in dollars from bars or from signal.atr_pct
  const atrValue = useMemo(() => {
    if (!bars || bars.length < 15) {
      if (signal?.atr_pct != null) {
        return (signal.atr_pct / 100) * closePrice;
      }
      return closePrice * 0.03; // fallback 3%
    }
    const sorted = [...bars].sort((a, b) => a.timestamp.localeCompare(b.timestamp));
    const trs: number[] = [];
    for (let i = 1; i < sorted.length; i++) {
      const hl = sorted[i].high - sorted[i].low;
      const hpc = Math.abs(sorted[i].high - sorted[i - 1].close);
      const lpc = Math.abs(sorted[i].low - sorted[i - 1].close);
      trs.push(Math.max(hl, hpc, lpc));
    }
    const last14 = trs.slice(-14);
    return last14.reduce((a, b) => a + b, 0) / last14.length;
  }, [bars, closePrice, signal]);

  // Risk values
  const stopPrice = useMemo(() => {
    return Math.max(0, closePrice - 2 * atrValue);
  }, [closePrice, atrValue]);

  const riskPerShare = useMemo(() => {
    return Math.max(0.01, closePrice - stopPrice);
  }, [closePrice, stopPrice]);

  const positionShares = useMemo(() => {
    const maxDollarRisk = (accountSize * riskPct) / 100;
    return Math.floor(maxDollarRisk / riskPerShare);
  }, [accountSize, riskPct, riskPerShare]);

  const positionDollars = useMemo(() => {
    return positionShares * closePrice;
  }, [positionShares, closePrice]);

  useEffect(() => {
    if (!chartContainerRef.current || !bars || bars.length === 0) return;

    const sortedBars = [...bars].sort(
      (a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime()
    );

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
        mode: 1,
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
          bottom: 0.25,
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

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: '#10b981',
      downColor: '#ef4444',
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

    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceFormat: { type: 'volume' },
      priceScaleId: 'volume_scale',
    });

    chart.priceScale('volume_scale').applyOptions({
      scaleMargins: {
        top: 0.75,
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

    let ema20Series: ISeriesApi<'Line'> | null = null;
    let sma50Series: ISeriesApi<'Line'> | null = null;
    let sma200Series: ISeriesApi<'Line'> | null = null;

    if (showIndicators.ema20) {
      ema20Series = chart.addSeries(LineSeries, {
        color: '#3b82f6',
        lineWidth: 2,
        title: 'EMA 20',
      });
      ema20Series.setData(calculateEMA(sortedBars, 20));
    }

    if (showIndicators.sma50) {
      sma50Series = chart.addSeries(LineSeries, {
        color: '#eab308',
        lineWidth: 2,
        title: 'SMA 50',
      });
      sma50Series.setData(calculateSMA(sortedBars, 50));
    }

    if (showIndicators.sma200) {
      sma200Series = chart.addSeries(LineSeries, {
        color: '#a855f7',
        lineWidth: 2,
        title: 'SMA 200',
      });
      sma200Series.setData(calculateSMA(sortedBars, 200));
    }

    if (showIndicators.vwap) {
      const vwapSeries = chart.addSeries(LineSeries, {
        color: '#f97316',
        lineWidth: 2,
        title: 'VWAP',
      });
      vwapSeries.setData(calculateVWAP(sortedBars));
    }

    if (showIndicators.atrStop) {
      const atrStopSeries = chart.addSeries(LineSeries, {
        color: '#f43f5e',
        lineWidth: 2,
        lineStyle: LineStyle.Dashed,
        title: '2x ATR Stop',
      });
      atrStopSeries.setData(calculateATRStop(sortedBars, 14, 2));
    }

    chart.timeScale().fitContent();

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

  // Breakout conditions evaluation
  const conditions = [
    {
      name: 'RVOL ≥ 1.5x',
      description: 'Volume vs 20-day average',
      actual: `${rvol?.toFixed(2)}x`,
      threshold: '≥ 1.50x',
      passed: rvol >= 1.5,
    },
    {
      name: 'RSI 50–75',
      description: 'Momentum not overbought',
      actual: `${rsi?.toFixed(1)}`,
      threshold: '50.0 – 75.0',
      passed: rsi >= 50 && rsi <= 75,
    },
    {
      name: 'Close > 50-SMA',
      description: 'Above intermediate trend',
      actual: signal?.ma_alignment?.sma50 != null
        ? `$${closePrice.toFixed(2)} vs $${signal.ma_alignment.sma50.toFixed(2)}`
        : signal?.ma_alignment?.above_sma50 !== false ? 'Above' : 'Below',
      threshold: 'Close > 50-SMA',
      passed: signal?.ma_alignment?.above_sma50 !== false,
    },
    {
      name: '20-day High Proximity',
      description: 'Within 1% of 20d high',
      actual: signal?.dist_to_52w_high_pct != null
        ? `${signal.dist_to_52w_high_pct.toFixed(1)}%`
        : '≥ 99% of 20d High',
      threshold: '≥ 99% of 20d High',
      passed: true,
    },
    {
      name: 'RS Score ≥ 70',
      description: 'Relative strength percentile',
      actual: signal?.rs_score != null ? `${signal.rs_score.toFixed(1)}` : 'N/A',
      threshold: '≥ 70.0',
      passed: (signal?.rs_score ?? 70) >= 70,
    },
    {
      name: 'Earnings Flag',
      description: 'Proximity to earnings report',
      actual: signal?.near_earnings
        ? `⚠️ Report on ${signal.earnings_date ?? 'upcoming'}`
        : 'Clear (>1 trading day)',
      threshold: 'No earnings in 1d',
      passed: !signal?.near_earnings,
      isWarning: signal?.near_earnings,
    },
  ];

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-3 sm:p-5 animate-in fade-in duration-200">
      <div className="relative flex flex-col w-full max-w-5xl max-h-[92vh] bg-[#0c121e] border border-slate-700/60 rounded-xl shadow-2xl overflow-hidden">
        {/* Modal Header */}
        <div className="flex items-center justify-between px-5 py-3.5 border-b border-slate-800 bg-[#0f172a]/80">
          <div className="flex items-center space-x-3">
            <div className="w-9 h-9 rounded-lg bg-emerald-500/10 border border-emerald-500/30 flex items-center justify-center text-emerald-400 font-bold text-base">
              {symbol.slice(0, 3)}
            </div>
            <div>
              <div className="flex items-center space-x-2">
                <h3 className="text-lg font-bold tracking-tight text-white">{symbol}</h3>
                <span className="text-xs px-2 py-0.5 rounded bg-slate-800 text-slate-400 font-medium">
                  {sector}
                </span>
                {signal?.composite_score != null && (
                  <span className="text-xs px-2 py-0.5 rounded font-mono font-bold bg-blue-500/15 text-blue-300 border border-blue-500/30">
                    Score: {signal.composite_score.toFixed(0)}/100
                  </span>
                )}
                {signal?.signal_streak && signal.signal_streak > 1 && (
                  <span className="text-xs px-2 py-0.5 rounded font-mono bg-orange-500/15 text-orange-400 border border-orange-500/30">
                    🔥 Day {signal.signal_streak}
                  </span>
                )}
              </div>
              <p className="text-xs text-slate-400 truncate max-w-xs">{name}</p>
            </div>
          </div>

          <div className="flex items-center space-x-4">
            <div className="hidden sm:flex items-center space-x-4 text-right font-mono text-xs">
              <div>
                <span className="text-[10px] uppercase text-slate-500 block">Close</span>
                <span className="font-semibold text-slate-100">${closePrice?.toFixed(2)}</span>
              </div>
              <div>
                <span className="text-[10px] uppercase text-slate-500 block">RVOL</span>
                <span className="font-semibold text-emerald-400">{rvol?.toFixed(2)}x</span>
              </div>
              <div>
                <span className="text-[10px] uppercase text-slate-500 block">RSI</span>
                <span className="font-semibold text-cyan-400">{rsi?.toFixed(1)}</span>
              </div>
              <div>
                <span className="text-[10px] uppercase text-slate-500 block">ATR Stop</span>
                <span className="font-semibold text-rose-400">${stopPrice.toFixed(2)}</span>
              </div>
            </div>

            <button
              onClick={onClose}
              className="p-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-slate-800 transition"
              aria-label="Close modal"
            >
              <X className="w-5 h-5" />
            </button>
          </div>
        </div>

        {/* Indicator Toggle Bar + Panel Buttons */}
        <div className="flex flex-wrap items-center justify-between px-5 py-2 bg-[#090e18] border-b border-slate-800/80 text-xs gap-2">
          {/* Overlays */}
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-slate-500 flex items-center gap-1 font-medium mr-1">
              <Layers className="w-3.5 h-3.5" /> Overlays:
            </span>
            <button
              onClick={() => setShowIndicators((p) => ({ ...p, ema20: !p.ema20 }))}
              className={`px-2 py-0.5 rounded border transition text-[11px] ${
                showIndicators.ema20
                  ? 'border-blue-500/50 bg-blue-500/10 text-blue-400'
                  : 'border-slate-800 text-slate-600'
              }`}
            >
              20-EMA
            </button>
            <button
              onClick={() => setShowIndicators((p) => ({ ...p, sma50: !p.sma50 }))}
              className={`px-2 py-0.5 rounded border transition text-[11px] ${
                showIndicators.sma50
                  ? 'border-yellow-500/50 bg-yellow-500/10 text-yellow-400'
                  : 'border-slate-800 text-slate-600'
              }`}
            >
              50-SMA
            </button>
            <button
              onClick={() => setShowIndicators((p) => ({ ...p, sma200: !p.sma200 }))}
              className={`px-2 py-0.5 rounded border transition text-[11px] ${
                showIndicators.sma200
                  ? 'border-purple-500/50 bg-purple-500/10 text-purple-400'
                  : 'border-slate-800 text-slate-600'
              }`}
            >
              200-SMA
            </button>
            <button
              onClick={() => setShowIndicators((p) => ({ ...p, vwap: !p.vwap }))}
              className={`px-2 py-0.5 rounded border transition text-[11px] ${
                showIndicators.vwap
                  ? 'border-orange-500/50 bg-orange-500/10 text-orange-400'
                  : 'border-slate-800 text-slate-600'
              }`}
            >
              VWAP
            </button>
            <button
              onClick={() => setShowIndicators((p) => ({ ...p, atrStop: !p.atrStop }))}
              className={`px-2 py-0.5 rounded border transition text-[11px] ${
                showIndicators.atrStop
                  ? 'border-rose-500/50 bg-rose-500/10 text-rose-400'
                  : 'border-slate-800 text-slate-600'
              }`}
            >
              2x ATR Stop
            </button>
          </div>

          {/* Panel Toggle Buttons */}
          <div className="flex items-center gap-2">
            <button
              onClick={() => setShowWhyTriggered((v) => !v)}
              className={`inline-flex items-center gap-1 px-2.5 py-1 rounded border text-xs font-medium transition ${
                showWhyTriggered
                  ? 'border-emerald-500/50 bg-emerald-500/10 text-emerald-400'
                  : 'border-slate-700 bg-slate-800/80 text-slate-300 hover:text-white'
              }`}
            >
              <Info className="w-3.5 h-3.5" />
              <span>Why It Triggered</span>
              {showWhyTriggered ? (
                <ChevronUp className="w-3 h-3 ml-0.5" />
              ) : (
                <ChevronDown className="w-3 h-3 ml-0.5" />
              )}
            </button>

            <button
              onClick={() => setShowRiskCalc((v) => !v)}
              className={`inline-flex items-center gap-1 px-2.5 py-1 rounded border text-xs font-medium transition ${
                showRiskCalc
                  ? 'border-rose-500/50 bg-rose-500/10 text-rose-400'
                  : 'border-slate-700 bg-slate-800/80 text-slate-300 hover:text-white'
              }`}
            >
              <ShieldAlert className="w-3.5 h-3.5" />
              <span>Risk Box</span>
              {showRiskCalc ? (
                <ChevronUp className="w-3 h-3 ml-0.5" />
              ) : (
                <ChevronDown className="w-3 h-3 ml-0.5" />
              )}
            </button>
          </div>
        </div>

        {/* Collapsible Panel: Why It Triggered */}
        {showWhyTriggered && (
          <div className="px-5 py-3 bg-[#0a0e1a] border-b border-slate-800 text-xs animate-in slide-in-from-top-1 duration-150">
            <div className="flex items-center justify-between mb-2">
              <span className="font-semibold text-slate-300 uppercase tracking-wider text-[10px]">
                Setup Checklist: {signal?.setup_name || 'Momentum Breakout'}
              </span>
              {signal?.near_earnings && (
                <span className="flex items-center gap-1 text-[11px] text-amber-400 bg-amber-500/10 border border-amber-500/30 px-2 py-0.5 rounded">
                  <AlertTriangle className="w-3 h-3" />
                  Earnings on {signal.earnings_date} — caution on breakout continuation
                </span>
              )}
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
              {conditions.map((c) => (
                <div
                  key={c.name}
                  className={`p-2 rounded-lg border flex items-start justify-between gap-1 ${
                    c.isWarning
                      ? 'bg-amber-950/20 border-amber-500/30 text-amber-200'
                      : c.passed
                      ? 'bg-slate-900/60 border-slate-800 text-slate-300'
                      : 'bg-rose-950/20 border-rose-500/30 text-rose-200'
                  }`}
                >
                  <div className="flex flex-col gap-0.5 min-w-0">
                    <span className="font-semibold text-[11px] text-white truncate">
                      {c.name}
                    </span>
                    <span className="text-[10px] text-slate-400 font-mono truncate">
                      {c.actual}
                    </span>
                    <span className="text-[9px] text-slate-500 truncate">
                      req: {c.threshold}
                    </span>
                  </div>
                  {c.isWarning ? (
                    <AlertTriangle className="w-3.5 h-3.5 text-amber-400 flex-shrink-0 mt-0.5" />
                  ) : c.passed ? (
                    <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400 flex-shrink-0 mt-0.5" />
                  ) : (
                    <XCircle className="w-3.5 h-3.5 text-rose-400 flex-shrink-0 mt-0.5" />
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Collapsible Panel: Risk Calculator */}
        {showRiskCalc && (
          <div className="px-5 py-3 bg-[#0a0f1a] border-b border-slate-800 text-xs animate-in slide-in-from-top-1 duration-150">
            <div className="flex flex-wrap items-center justify-between gap-3 mb-2">
              <span className="font-semibold text-rose-400 flex items-center gap-1.5 text-xs">
                <ShieldAlert className="w-3.5 h-3.5" />
                ATR-Based Risk & Position Sizer
              </span>
              <span className="text-[10px] text-slate-500 font-mono">
                ATR(14): ${atrValue.toFixed(2)} ({((atrValue / closePrice) * 100).toFixed(1)}% of price)
              </span>
            </div>

            <div className="grid grid-cols-2 sm:grid-cols-5 gap-3 items-end">
              {/* Account Size */}
              <div>
                <label className="text-[10px] uppercase text-slate-400 block mb-1">
                  Account Size ($)
                </label>
                <input
                  type="number"
                  value={accountSize}
                  onChange={(e) => setAccountSize(Math.max(100, Number(e.target.value)))}
                  className="w-full bg-[#0c121e] border border-slate-700 rounded px-2.5 py-1 text-slate-100 font-mono text-xs focus:outline-none focus:border-rose-500"
                />
              </div>

              {/* Risk % */}
              <div>
                <label className="text-[10px] uppercase text-slate-400 block mb-1">
                  Max Risk %
                </label>
                <input
                  type="number"
                  step="0.1"
                  min="0.1"
                  max="10"
                  value={riskPct}
                  onChange={(e) => setRiskPct(Math.max(0.1, Number(e.target.value)))}
                  className="w-full bg-[#0c121e] border border-slate-700 rounded px-2.5 py-1 text-slate-100 font-mono text-xs focus:outline-none focus:border-rose-500"
                />
              </div>

              {/* ATR Stop Price */}
              <div className="bg-[#0c121e] border border-slate-800 rounded p-2">
                <span className="text-[10px] uppercase text-slate-500 block">Stop (Entry − 2×ATR)</span>
                <span className="font-mono font-bold text-rose-400 text-sm">
                  ${stopPrice.toFixed(2)}
                </span>
                <span className="text-[9px] text-slate-500 block font-mono">
                  -${riskPerShare.toFixed(2)}/sh (−{((riskPerShare / closePrice) * 100).toFixed(1)}%)
                </span>
              </div>

              {/* Max Position Size (Shares) */}
              <div className="bg-[#0c121e] border border-slate-800 rounded p-2">
                <span className="text-[10px] uppercase text-slate-500 block">Position Size</span>
                <span className="font-mono font-bold text-emerald-400 text-sm">
                  {positionShares.toLocaleString()} shares
                </span>
                <span className="text-[9px] text-slate-500 block font-mono">
                  ${positionDollars.toLocaleString(undefined, { maximumFractionDigits: 0 })} notional
                </span>
              </div>

              {/* Dollar Risk */}
              <div className="bg-[#0c121e] border border-slate-800 rounded p-2">
                <span className="text-[10px] uppercase text-slate-500 block">Max $ Risk</span>
                <span className="font-mono font-bold text-amber-400 text-sm">
                  ${((accountSize * riskPct) / 100).toFixed(0)}
                </span>
                <span className="text-[9px] text-slate-500 block">
                  {riskPct}% of account
                </span>
              </div>
            </div>
          </div>
        )}

        {/* Chart Canvas */}
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
