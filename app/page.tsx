'use client';

import React, { useEffect, useState, useMemo } from 'react';
import dynamic from 'next/dynamic';
import {
  TrendingUp,
  Activity,
  Flame,
  Search,
  SlidersHorizontal,
  RefreshCw,
  ExternalLink,
  ChevronDown,
  Info,
  Calendar,
  CheckCircle2,
  Clock,
  Sparkles,
} from 'lucide-react';
import type { BarData } from '../components/StockChart';

// Dynamically import StockChart to ensure it only renders client-side (HTML5 Canvas)
const StockChart = dynamic(() => import('../components/StockChart'), {
  ssr: false,
});

interface MAAlignment {
  above_ema20: boolean | null;
  above_sma50: boolean | null;
  above_sma200: boolean | null;
  ema20?: number | null;
  sma50?: number | null;
  sma200?: number | null;
}

interface Signal {
  id: number;
  timestamp: string;
  symbol: string;
  name: string;
  sector: string;
  market_cap: number | null;
  setup_name: string;
  close_price: number;
  rvol: number;
  rsi: number;
  ma_alignment: MAAlignment;
  created_at: string;
}

export default function DashboardPage() {
  const [signals, setSignals] = useState<Signal[]>([]);
  const [barsMap, setBarsMap] = useState<Record<string, BarData[]>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Filters
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedSector, setSelectedSector] = useState<string>('ALL');
  const [minRVOL, setMinRVOL] = useState<number>(1.5);
  const [activeSymbolForChart, setActiveSymbolForChart] = useState<Signal | null>(null);

  useEffect(() => {
    async function loadData() {
      try {
        setLoading(true);
        setError(null);

        // Fetch latest_signals.json and signal_bars.json from public/data
        const [signalsRes, barsRes] = await Promise.all([
          fetch('/data/latest_signals.json', { cache: 'no-store' }),
          fetch('/data/signal_bars.json', { cache: 'no-store' }),
        ]);

        if (!signalsRes.ok) {
          throw new Error(`Failed to load signals: HTTP ${signalsRes.status}`);
        }
        if (!barsRes.ok) {
          throw new Error(`Failed to load historical bars: HTTP ${barsRes.status}`);
        }

        const signalsData: Signal[] = await signalsRes.json();
        const barsData: Record<string, BarData[]> = await barsRes.json();

        setSignals(signalsData);
        setBarsMap(barsData);
      } catch (err: any) {
        console.error('Data load error:', err);
        setError(err.message || 'Error loading screener data');
      } finally {
        setLoading(false);
      }
    }

    loadData();
  }, []);

  // Compute metrics
  const totalSignals = signals.length;

  const highestRVOLSignal = useMemo(() => {
    if (signals.length === 0) return null;
    return [...signals].sort((a, b) => b.rvol - a.rvol)[0];
  }, [signals]);

  const avgRSI = useMemo(() => {
    if (signals.length === 0) return 0;
    const sum = signals.reduce((acc, curr) => acc + (curr.rsi || 0), 0);
    return (sum / signals.length).toFixed(1);
  }, [signals]);

  const uniqueSectors = useMemo(() => {
    const sectors = new Set<string>();
    signals.forEach((s) => {
      if (s.sector && s.sector !== 'Unknown') sectors.add(s.sector);
    });
    return Array.from(sectors);
  }, [signals]);

  // Filter signals
  const filteredSignals = useMemo(() => {
    return signals.filter((s) => {
      const matchesSearch =
        s.symbol.toLowerCase().includes(searchQuery.toLowerCase()) ||
        s.name.toLowerCase().includes(searchQuery.toLowerCase());
      const matchesSector =
        selectedSector === 'ALL' || s.sector === selectedSector;
      const matchesRVOL = s.rvol >= minRVOL;

      return matchesSearch && matchesSector && matchesRVOL;
    });
  }, [signals, searchQuery, selectedSector, minRVOL]);

  return (
    <div className="min-h-screen bg-[#090d16] text-slate-100 flex flex-col">
      {/* Top Navigation */}
      <header className="border-b border-slate-800/80 bg-[#0d1424]/90 backdrop-blur sticky top-0 z-40">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
          <div className="flex items-center space-x-3">
            <div className="w-9 h-9 rounded-lg bg-emerald-500/10 border border-emerald-500/30 flex items-center justify-center text-emerald-400">
              <TrendingUp className="w-5 h-5" />
            </div>
            <div>
              <div className="flex items-center space-x-2">
                <span className="font-bold text-base tracking-tight text-white">
                  Market Screener
                </span>
                <span className="text-[10px] uppercase font-bold tracking-wider px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/30">
                  Live Daily
                </span>
              </div>
              <p className="text-[11px] text-slate-400">
                US Equities Momentum & Breakout Engine
              </p>
            </div>
          </div>

          <div className="flex items-center space-x-3 text-xs text-slate-400">
            <div className="hidden md:flex items-center space-x-2 bg-slate-900/60 px-3 py-1.5 rounded-lg border border-slate-800">
              <Calendar className="w-3.5 h-3.5 text-slate-500" />
              <span>
                {signals.length > 0 ? `Latest: ${signals[0].timestamp}` : 'Synchronized'}
              </span>
            </div>
            <div className="flex items-center space-x-1.5 text-emerald-400 bg-emerald-500/10 border border-emerald-500/20 px-2.5 py-1.5 rounded-lg">
              <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
              <span className="font-semibold text-xs">Automated</span>
            </div>
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main className="flex-1 max-w-7xl w-full mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-8">
        {/* Metric Cards Row */}
        <section className="grid grid-cols-1 md:grid-cols-3 gap-5">
          {/* Card 1: Total Signals */}
          <div className="relative overflow-hidden rounded-xl bg-[#0f172a] border border-slate-800/80 p-5 shadow-lg">
            <div className="absolute top-0 right-0 w-32 h-32 bg-emerald-500/5 rounded-full blur-2xl -mr-10 -mt-10" />
            <div className="flex items-center justify-between text-slate-400 text-xs font-medium uppercase tracking-wider mb-3">
              <span>Total Breakout Signals</span>
              <Activity className="w-4 h-4 text-emerald-400" />
            </div>
            <div className="flex items-baseline space-x-3">
              <span className="text-3xl font-extrabold text-white tracking-tight font-mono">
                {loading ? '—' : totalSignals}
              </span>
              <span className="text-xs text-emerald-400 font-medium flex items-center">
                <Sparkles className="w-3 h-3 mr-1" />
                Active Setups
              </span>
            </div>
            <p className="text-xs text-slate-400 mt-2">
              Passed RVOL &ge; 1.5x, RSI 50–75 &amp; 50-SMA Trend Filter
            </p>
          </div>

          {/* Card 2: Highest RVOL Breakdown */}
          <div className="relative overflow-hidden rounded-xl bg-[#0f172a] border border-slate-800/80 p-5 shadow-lg">
            <div className="absolute top-0 right-0 w-32 h-32 bg-cyan-500/5 rounded-full blur-2xl -mr-10 -mt-10" />
            <div className="flex items-center justify-between text-slate-400 text-xs font-medium uppercase tracking-wider mb-3">
              <span>Highest RVOL Leader</span>
              <Flame className="w-4 h-4 text-orange-400" />
            </div>
            {highestRVOLSignal ? (
              <div className="flex items-baseline space-x-3">
                <span className="text-3xl font-extrabold text-white tracking-tight font-mono">
                  {highestRVOLSignal.rvol.toFixed(2)}x
                </span>
                <span className="text-sm font-bold text-orange-400 bg-orange-500/10 px-2 py-0.5 rounded border border-orange-500/20">
                  {highestRVOLSignal.symbol}
                </span>
              </div>
            ) : (
              <div className="text-2xl font-bold text-slate-600 font-mono">—</div>
            )}
            <p className="text-xs text-slate-400 mt-2">
              {highestRVOLSignal
                ? `${highestRVOLSignal.name} on ${highestRVOLSignal.timestamp}`
                : 'Awaiting daily screener execution'}
            </p>
          </div>

          {/* Card 3: Average RSI */}
          <div className="relative overflow-hidden rounded-xl bg-[#0f172a] border border-slate-800/80 p-5 shadow-lg">
            <div className="absolute top-0 right-0 w-32 h-32 bg-purple-500/5 rounded-full blur-2xl -mr-10 -mt-10" />
            <div className="flex items-center justify-between text-slate-400 text-xs font-medium uppercase tracking-wider mb-3">
              <span>Average Setup RSI (14)</span>
              <TrendingUp className="w-4 h-4 text-purple-400" />
            </div>
            <div className="flex items-baseline space-x-3">
              <span className="text-3xl font-extrabold text-white tracking-tight font-mono">
                {loading ? '—' : avgRSI}
              </span>
              <span className="text-xs text-cyan-400 font-medium">Optimal Window</span>
            </div>
            <p className="text-xs text-slate-400 mt-2">
              Healthy momentum expansion without severe overbought exhaustion
            </p>
          </div>
        </section>

        {/* Filter & Search Bar */}
        <section className="bg-[#0f172a] border border-slate-800 rounded-xl p-4 flex flex-col md:flex-row gap-4 items-center justify-between shadow-md">
          <div className="flex-1 w-full flex items-center bg-[#090d16] border border-slate-700/80 rounded-lg px-3 py-2 text-sm focus-within:border-emerald-500 transition">
            <Search className="w-4 h-4 text-slate-400 mr-2 flex-shrink-0" />
            <input
              type="text"
              placeholder="Filter by symbol (e.g. AAPL) or company name..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="bg-transparent text-slate-100 placeholder-slate-500 focus:outline-none w-full text-xs sm:text-sm"
            />
          </div>

          <div className="flex flex-wrap items-center gap-3 w-full md:w-auto">
            {/* Sector Selector */}
            <div className="flex items-center space-x-2 text-xs">
              <span className="text-slate-400 font-medium">Sector:</span>
              <select
                value={selectedSector}
                onChange={(e) => setSelectedSector(e.target.value)}
                className="bg-[#090d16] text-slate-200 border border-slate-700 rounded-lg px-2.5 py-1.5 text-xs focus:outline-none focus:border-emerald-500"
              >
                <option value="ALL">All Sectors</option>
                {uniqueSectors.map((sec) => (
                  <option key={sec} value={sec}>
                    {sec}
                  </option>
                ))}
              </select>
            </div>

            {/* Min RVOL Slider / Indicator */}
            <div className="flex items-center space-x-2 text-xs">
              <span className="text-slate-400 font-medium">Min RVOL:</span>
              <select
                value={minRVOL}
                onChange={(e) => setMinRVOL(Number(e.target.value))}
                className="bg-[#090d16] text-slate-200 border border-slate-700 rounded-lg px-2.5 py-1.5 text-xs focus:outline-none focus:border-emerald-500 font-mono"
              >
                <option value={1.5}>&ge; 1.5x</option>
                <option value={2.0}>&ge; 2.0x</option>
                <option value={2.5}>&ge; 2.5x</option>
              </select>
            </div>
          </div>
        </section>

        {/* Signals Data Table */}
        <section className="bg-[#0f172a] border border-slate-800 rounded-xl overflow-hidden shadow-lg">
          <div className="px-6 py-4 border-b border-slate-800/80 flex items-center justify-between">
            <div>
              <h2 className="text-base font-bold text-white tracking-tight flex items-center gap-2">
                Triggered Momentum Signals
                <span className="text-xs font-mono font-normal text-slate-400 bg-slate-800/80 px-2 py-0.5 rounded">
                  {filteredSignals.length} results
                </span>
              </h2>
              <p className="text-xs text-slate-400 mt-0.5">
                Click any row to open the interactive TradingView candlestick chart with MA overlays
              </p>
            </div>
          </div>

          {loading ? (
            <div className="py-20 flex flex-col items-center justify-center text-slate-500 space-y-3">
              <RefreshCw className="w-8 h-8 animate-spin text-emerald-400" />
              <p className="text-xs">Loading momentum screener data...</p>
            </div>
          ) : error ? (
            <div className="py-16 flex flex-col items-center justify-center text-red-400 space-y-2">
              <Info className="w-8 h-8" />
              <p className="text-sm font-semibold">{error}</p>
              <p className="text-xs text-slate-500">
                Run python run_daily.py --dry-run to generate public/data/ payloads.
              </p>
            </div>
          ) : filteredSignals.length === 0 ? (
            <div className="py-16 text-center text-slate-500 space-y-2">
              <p className="text-sm font-semibold">No setups match the current filters.</p>
              <p className="text-xs">Try resetting search or adjusting minimum RVOL threshold.</p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs sm:text-sm">
                <thead>
                  <tr className="bg-[#0a0f1d] text-slate-400 border-b border-slate-800 text-[11px] uppercase tracking-wider font-semibold">
                    <th className="py-3 px-4 sm:px-6">Ticker / Company</th>
                    <th className="py-3 px-4">Date</th>
                    <th className="py-3 px-4 text-right">Close Price</th>
                    <th className="py-3 px-4 text-right">RVOL</th>
                    <th className="py-3 px-4 text-right">RSI (14)</th>
                    <th className="py-3 px-4">MA Alignment</th>
                    <th className="py-3 px-4 sm:px-6 text-center">Chart</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/60 font-mono">
                  {filteredSignals.map((sig) => {
                    const isAbove50 = sig.ma_alignment?.above_sma50 !== false;
                    const isAbove20 = sig.ma_alignment?.above_ema20 === true;
                    const isAbove200 = sig.ma_alignment?.above_sma200 === true;

                    return (
                      <tr
                        key={`${sig.symbol}-${sig.id}`}
                        onClick={() => setActiveSymbolForChart(sig)}
                        className="hover:bg-slate-800/40 cursor-pointer transition group"
                      >
                        {/* Ticker & Name */}
                        <td className="py-3.5 px-4 sm:px-6 font-sans">
                          <div className="flex items-center space-x-3">
                            <span className="font-bold text-white text-sm tracking-tight font-mono group-hover:text-emerald-400 transition">
                              {sig.symbol}
                            </span>
                            <span className="text-[11px] text-slate-400 truncate max-w-[180px] hidden sm:inline">
                              {sig.name}
                            </span>
                          </div>
                          <span className="text-[10px] text-slate-500 block sm:hidden">
                            {sig.sector}
                          </span>
                        </td>

                        {/* Date */}
                        <td className="py-3.5 px-4 text-slate-400 text-xs">
                          {sig.timestamp}
                        </td>

                        {/* Close Price */}
                        <td className="py-3.5 px-4 text-right font-semibold text-slate-100">
                          ${sig.close_price.toFixed(2)}
                        </td>

                        {/* RVOL */}
                        <td className="py-3.5 px-4 text-right">
                          <span className="inline-block px-2 py-0.5 rounded text-xs font-bold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                            {sig.rvol.toFixed(2)}x
                          </span>
                        </td>

                        {/* RSI */}
                        <td className="py-3.5 px-4 text-right text-cyan-400 font-semibold">
                          {sig.rsi.toFixed(1)}
                        </td>

                        {/* MA Alignment Badges */}
                        <td className="py-3.5 px-4 font-sans">
                          <div className="flex flex-wrap gap-1.5">
                            {isAbove50 && (
                              <span className="inline-flex items-center px-2 py-0.5 rounded text-[11px] font-medium bg-emerald-950/70 text-emerald-300 border border-emerald-500/30">
                                Above 50-SMA
                              </span>
                            )}
                            {isAbove20 && (
                              <span className="inline-flex items-center px-2 py-0.5 rounded text-[11px] font-medium bg-blue-950/70 text-blue-300 border border-blue-500/30">
                                Above 20-EMA
                              </span>
                            )}
                            {isAbove200 && (
                              <span className="inline-flex items-center px-2 py-0.5 rounded text-[11px] font-medium bg-purple-950/70 text-purple-300 border border-purple-500/30">
                                Above 200-SMA
                              </span>
                            )}
                          </div>
                        </td>

                        {/* View Action */}
                        <td className="py-3.5 px-4 sm:px-6 text-center">
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              setActiveSymbolForChart(sig);
                            }}
                            className="p-1.5 rounded-lg bg-slate-800/80 hover:bg-emerald-500/20 text-slate-300 hover:text-emerald-400 border border-slate-700/80 transition"
                            title="Open Candlestick Chart"
                          >
                            <ExternalLink className="w-3.5 h-3.5" />
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </main>

      {/* Interactive Stock Chart Modal */}
      {activeSymbolForChart && (
        <StockChart
          symbol={activeSymbolForChart.symbol}
          name={activeSymbolForChart.name}
          sector={activeSymbolForChart.sector}
          closePrice={activeSymbolForChart.close_price}
          rvol={activeSymbolForChart.rvol}
          rsi={activeSymbolForChart.rsi}
          bars={barsMap[activeSymbolForChart.symbol] || []}
          onClose={() => setActiveSymbolForChart(null)}
        />
      )}
    </div>
  );
}
