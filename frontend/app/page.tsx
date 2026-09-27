'use client';

import React, { useEffect, useState, useMemo, useCallback } from 'react';
import dynamic from 'next/dynamic';
import {
  TrendingUp,
  Activity,
  Flame,
  Search,
  RefreshCw,
  ExternalLink,
  ChevronDown,
  ChevronUp,
  ChevronRight,
  Info,
  Calendar,
  CheckCircle2,
  Clock,
  Sparkles,
  Copy,
  AlertTriangle,
  Bookmark,
  Trash2,
} from 'lucide-react';
import type { BarData } from '../components/StockChart';

// Dynamic imports to prevent SSR issues
const StockChart = dynamic(() => import('../components/StockChart'), { ssr: false });
const TrackRecord = dynamic(() => import('../components/TrackRecord'), { ssr: false });

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
  market_regime?: string;
  rs_score?: number | null;
  ma_alignment: MAAlignment;
  price_5d?: number | null;
  price_10d?: number | null;
  price_20d?: number | null;
  return_5d_pct?: number | null;
  return_10d_pct?: number | null;
  return_20d_pct?: number | null;
  pct_change_1d?: number | null;
  atr_pct?: number | null;
  avg_dollar_vol_20d?: number | null;
  rs_vs_spy?: number | null;
  dist_to_52w_high_pct?: number | null;
  near_earnings?: boolean;
  earnings_date?: string | null;
  composite_score?: number | null;
  signal_streak?: number | null;
  created_at: string;
}

interface RegimeData {
  date: string | null;
  regime: string;
  spy_close: number | null;
  spy_sma_200: number | null;
  spy_pct_vs_sma200: number | null;
  pct_universe_above_sma50: number | null;
  universe_breadth_n: number;
  generated_at: string;
}

interface SetupStats {
  generated_at: string;
  setups: Record<string, {
    signal_count: number;
    completed_5d_count: number;
    hit_rate_5d: number;
    completed_20d_count: number;
    hit_rate_20d: number;
    median_excess_5d: number;
    avg_excess_5d: number;
    median_excess_20d: number;
    avg_excess_20d: number;
    median_max_drawdown: number;
  }>;
}

interface FilterPreset {
  name: string;
  sector: string;
  minRVOL: number;
  minScore: number;
  hideEarnings: boolean;
}

const BUILTIN_PRESETS: FilterPreset[] = [
  { name: 'All Signals', sector: 'ALL', minRVOL: 1.5, minScore: 0, hideEarnings: false },
  { name: 'High Conviction', sector: 'ALL', minRVOL: 2.0, minScore: 55, hideEarnings: true },
  { name: 'Fresh Breakouts (No Earn)', sector: 'ALL', minRVOL: 1.5, minScore: 0, hideEarnings: true },
];

const PRESETS_STORAGE_KEY = 'ms_filter_presets_v1';

type SortColumn =
  | 'symbol'
  | 'timestamp'
  | 'close_price'
  | 'pct_change_1d'
  | 'rvol'
  | 'rsi'
  | 'atr_pct'
  | 'avg_dollar_vol_20d'
  | 'rs_vs_spy'
  | 'composite_score'
  | 'signal_streak'
  | 'near_earnings';

function formatTradingViewSymbol(sym: string, sectorOrExchange?: string | null): string {
  const s = (sectorOrExchange || '').toUpperCase();
  if (s.includes('NAS') || s.includes('XNAS')) return `NASDAQ:${sym}`;
  if (s.includes('NYS') || s.includes('XNYS')) return `NYSE:${sym}`;
  if (s.includes('AMEX') || s.includes('ARCX')) return `AMEX:${sym}`;

  const nasdaqSymbols = new Set([
    'AAPL', 'MSFT', 'NVDA', 'AMZN', 'GOOGL', 'GOOG', 'META', 'TSLA', 'AMD',
    'NFLX', 'INTC', 'AVGO', 'QCOM', 'CSCO', 'ADBE'
  ]);
  if (nasdaqSymbols.has(sym)) return `NASDAQ:${sym}`;

  const nyseSymbols = new Set([
    'SPY', 'JPM', 'V', 'UNH', 'HD', 'PG', 'DIS', 'MA', 'BAC', 'XOM', 'CVX', 'LLY'
  ]);
  if (nyseSymbols.has(sym)) return `NYSE:${sym}`;

  return `NASDAQ:${sym}`;
}

function formatDollarVol(v: number | null | undefined): string {
  if (v == null) return '—';
  if (v >= 1e9) return `$${(v / 1e9).toFixed(1)}B`;
  if (v >= 1e6) return `$${(v / 1e6).toFixed(0)}M`;
  if (v >= 1e3) return `$${(v / 1e3).toFixed(0)}K`;
  return `$${v.toFixed(0)}`;
}

function relativeTime(isoStr: string | null | undefined): string {
  if (!isoStr) return '—';
  try {
    const diff = Date.now() - new Date(isoStr).getTime();
    if (diff < 0) return 'just now';
    const mins = Math.floor(diff / 60000);
    if (mins < 60) return `${mins}m ago`;
    const hours = Math.floor(mins / 60);
    if (hours < 24) return `${hours}h ago`;
    const days = Math.floor(hours / 24);
    return `${days}d ago`;
  } catch {
    return '—';
  }
}

function ScorePill({ score }: { score: number | null | undefined }) {
  if (score == null) {
    return <span className="text-slate-500 font-mono text-xs">Pending</span>;
  }

  let colour = 'bg-slate-800 text-slate-400 border-slate-700';
  if (score >= 80) colour = 'bg-amber-500/15 text-amber-300 border-amber-500/30';
  else if (score >= 65) colour = 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30';
  else if (score >= 50) colour = 'bg-cyan-500/15 text-cyan-300 border-cyan-500/30';

  return (
    <span className={`inline-block px-2 py-0.5 rounded text-xs font-bold border font-mono ${colour}`}>
      {score.toFixed(0)}
    </span>
  );
}

export default function DashboardPage() {
  const [signals, setSignals] = useState<Signal[]>([]);
  const [barsMap, setBarsMap] = useState<Record<string, BarData[]>>({});
  const [regimeData, setRegimeData] = useState<RegimeData | null>(null);
  const [setupStats, setSetupStats] = useState<SetupStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [errorUrl, setErrorUrl] = useState<string | null>(null);
  const [toastMessage, setToastMessage] = useState<string | null>(null);

  // Filters
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedSector, setSelectedSector] = useState<string>('ALL');
  const [minRVOL, setMinRVOL] = useState<number>(1.5);
  const [minScore, setMinScore] = useState<number>(0);
  const [hideEarnings, setHideEarnings] = useState<boolean>(false);

  // Presets
  const [savedPresets, setSavedPresets] = useState<FilterPreset[]>([]);
  const [selectedPresetName, setSelectedPresetName] = useState<string>('All Signals');

  // Sorting
  const [sortCol, setSortCol] = useState<SortColumn>('timestamp');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');

  // Chart modal
  const [activeSymbolForChart, setActiveSymbolForChart] = useState<Signal | null>(null);

  // Responsive mobile row expansion: stores signal keys (e.g. `${symbol}-${id}`)
  const [expandedRowIds, setExpandedRowIds] = useState<Set<string>>(new Set());

  const toggleRowExpand = (rowKey: string) => {
    setExpandedRowIds((prev) => {
      const next = new Set(prev);
      if (next.has(rowKey)) {
        next.delete(rowKey);
      } else {
        next.add(rowKey);
      }
      return next;
    });
  };

  // Load presets from localStorage
  useEffect(() => {
    try {
      const raw = localStorage.getItem(PRESETS_STORAGE_KEY);
      if (raw) {
        const parsed = JSON.parse(raw);
        if (Array.isArray(parsed)) setSavedPresets(parsed);
      }
    } catch {
      // ignore
    }
  }, []);

  const saveCurrentPreset = () => {
    const name = prompt('Preset name:');
    if (!name || !name.trim()) return;
    const newPreset: FilterPreset = {
      name: name.trim(),
      sector: selectedSector,
      minRVOL,
      minScore,
      hideEarnings,
    };
    const updated = [...savedPresets.filter((p) => p.name !== newPreset.name), newPreset];
    setSavedPresets(updated);
    setSelectedPresetName(newPreset.name);
    try {
      localStorage.setItem(PRESETS_STORAGE_KEY, JSON.stringify(updated));
    } catch {
      // ignore
    }
    setToastMessage(`Saved preset "${newPreset.name}"`);
    setTimeout(() => setToastMessage(null), 3000);
  };

  const deletePreset = (name: string) => {
    const updated = savedPresets.filter((p) => p.name !== name);
    setSavedPresets(updated);
    if (selectedPresetName === name) setSelectedPresetName('All Signals');
    try {
      localStorage.setItem(PRESETS_STORAGE_KEY, JSON.stringify(updated));
    } catch {
      // ignore
    }
    setToastMessage(`Deleted preset "${name}"`);
    setTimeout(() => setToastMessage(null), 2500);
  };

  const applyPreset = (preset: FilterPreset) => {
    setSelectedSector(preset.sector);
    setMinRVOL(preset.minRVOL);
    setMinScore(preset.minScore);
    setHideEarnings(preset.hideEarnings);
    setSelectedPresetName(preset.name);
  };

  // Fetch all data
  useEffect(() => {
    async function loadData() {
      try {
        setLoading(true);
        setError(null);
        setErrorUrl(null);

        // Fetch primary signals first to report exact failing URL
        const signalsUrl = '/data/latest_signals.json';
        const signalsRes = await fetch(signalsUrl, { cache: 'no-store' });
        if (!signalsRes.ok) {
          setErrorUrl(signalsUrl);
          throw new Error(`Failed to load signals: HTTP ${signalsRes.status}`);
        }
        const signalsData: Signal[] = await signalsRes.json();

        // Optional parallel fetches
        const [barsRes, regimeRes, statsRes] = await Promise.all([
          fetch('/data/signal_bars.json', { cache: 'no-store' }).catch(() => null),
          fetch('/data/regime.json', { cache: 'no-store' }).catch(() => null),
          fetch('/data/setup_stats.json', { cache: 'no-store' }).catch(() => null),
        ]);

        if (barsRes && barsRes.ok) {
          const barsData = await barsRes.json();
          setBarsMap(barsData);
        }

        if (regimeRes && regimeRes.ok) {
          const regData: RegimeData = await regimeRes.json();
          setRegimeData(regData);
        }

        if (statsRes && statsRes.ok) {
          const statsData: SetupStats = await statsRes.json();
          setSetupStats(statsData);
        }

        setSignals(signalsData);
      } catch (err: any) {
        console.error('Data load error:', err);
        setError(err.message || 'Error loading screener data');
      } finally {
        setLoading(false);
      }
    }

    loadData();
  }, []);

  // Unique sectors
  const uniqueSectors = useMemo(() => {
    const s = new Set<string>();
    signals.forEach((sig) => {
      if (sig.sector && sig.sector !== 'Unknown') s.add(sig.sector);
    });
    return Array.from(s).sort();
  }, [signals]);

  // Card 2: signals today vs 30-day average
  const { todayCount, avg30Count } = useMemo(() => {
    if (signals.length === 0) return { todayCount: 0, avg30Count: '—' };
    const latestDate = signals[0]?.timestamp;
    const today = signals.filter((s) => s.timestamp === latestDate).length;

    // Filter to last 30 calendar days from latestDate
    const latestTime = new Date(latestDate).getTime();
    const thirtyDaysAgo = latestTime - 30 * 24 * 60 * 60 * 1000;

    const signalsIn30d = signals.filter(
      (s) => new Date(s.timestamp).getTime() >= thirtyDaysAgo
    );
    const uniqueDates = new Set(signalsIn30d.map((s) => s.timestamp)).size;
    const avg = uniqueDates > 0 ? (signalsIn30d.length / uniqueDates).toFixed(1) : '—';

    return { todayCount: today, avg30Count: avg };
  }, [signals]);

  // Card 3: avg breakout gain
  const avgBreakoutGain = useMemo(() => {
    const returns: number[] = [];
    signals.forEach((s) => {
      if (s.return_20d_pct != null) returns.push(s.return_20d_pct);
      else if (s.return_10d_pct != null) returns.push(s.return_10d_pct);
      else if (s.return_5d_pct != null) returns.push(s.return_5d_pct);
    });
    if (returns.length === 0) return '—';
    const positive = returns.filter((r) => r > 0);
    if (positive.length > 0) {
      const avg = positive.reduce((a, b) => a + b, 0) / positive.length;
      return `+${avg.toFixed(1)}%`;
    }
    const avg = returns.reduce((a, b) => a + b, 0) / returns.length;
    return `${avg >= 0 ? '+' : ''}${avg.toFixed(1)}%`;
  }, [signals]);

  // Handle column sort toggle
  const handleSort = (col: SortColumn) => {
    if (sortCol === col) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortCol(col);
      setSortDir('desc');
    }
  };

  // Filtered and sorted signals
  const displaySignals = useMemo(() => {
    const filtered = signals.filter((s) => {
      const matchesSearch =
        s.symbol.toLowerCase().includes(searchQuery.toLowerCase()) ||
        s.name.toLowerCase().includes(searchQuery.toLowerCase());
      const matchesSector = selectedSector === 'ALL' || s.sector === selectedSector;
      const matchesRVOL = s.rvol >= minRVOL;
      const matchesScore =
        s.composite_score == null || minScore === 0 || s.composite_score >= minScore;
      const matchesEarnings = !hideEarnings || !s.near_earnings;

      return matchesSearch && matchesSector && matchesRVOL && matchesScore && matchesEarnings;
    });

    return [...filtered].sort((a, b) => {
      let valA: any = a[sortCol as keyof Signal];
      let valB: any = b[sortCol as keyof Signal];

      // Handle nulls: push nulls to the bottom regardless of sortDir
      if (valA == null && valB == null) return 0;
      if (valA == null) return 1;
      if (valB == null) return -1;

      let cmp = 0;
      if (typeof valA === 'string') {
        cmp = valA.localeCompare(valB);
      } else if (typeof valA === 'boolean') {
        cmp = valA === valB ? 0 : valA ? 1 : -1;
      } else {
        cmp = Number(valA) - Number(valB);
      }

      return sortDir === 'asc' ? cmp : -cmp;
    });
  }, [signals, searchQuery, selectedSector, minRVOL, minScore, hideEarnings, sortCol, sortDir]);

  // Copy TradingView Watchlist
  const handleCopyTradingViewWatchlist = async () => {
    const activeList = displaySignals.length > 0 ? displaySignals : signals;
    if (activeList.length === 0) {
      setToastMessage('No breakout tickers available to copy.');
      setTimeout(() => setToastMessage(null), 3000);
      return;
    }

    const formattedList = activeList
      .map((s) => formatTradingViewSymbol(s.symbol, s.sector))
      .join(', ');

    try {
      if (navigator?.clipboard?.writeText) {
        await navigator.clipboard.writeText(formattedList);
      } else {
        const textarea = document.createElement('textarea');
        textarea.value = formattedList;
        document.body.appendChild(textarea);
        textarea.select();
        document.execCommand('copy');
        document.body.removeChild(textarea);
      }
      setToastMessage(
        `Copied ${activeList.length} ticker${activeList.length > 1 ? 's' : ''} for TradingView`
      );
    } catch (err) {
      console.error('Clipboard error:', err);
      setToastMessage('Failed to copy to clipboard.');
    }
    setTimeout(() => setToastMessage(null), 3500);
  };

  const SortHeader = ({
    col,
    label,
    align = 'left',
    className = '',
  }: {
    col: SortColumn;
    label: string;
    align?: 'left' | 'right' | 'center';
    className?: string;
  }) => {
    const active = sortCol === col;
    return (
      <th
        onClick={() => handleSort(col)}
        className={`py-3 px-3 cursor-pointer select-none hover:text-white transition group ${
          align === 'right' ? 'text-right' : align === 'center' ? 'text-center' : 'text-left'
        } ${className}`}
      >
        <span className="inline-flex items-center gap-1 font-semibold text-[11px] uppercase tracking-wider">
          {label}
          {active ? (
            sortDir === 'asc' ? (
              <ChevronUp className="w-3 h-3 text-emerald-400" />
            ) : (
              <ChevronDown className="w-3 h-3 text-emerald-400" />
            )
          ) : (
            <ChevronDown className="w-3 h-3 opacity-0 group-hover:opacity-40 transition" />
          )}
        </span>
      </th>
    );
  };

  // Freshness age colour
  const freshnessAgeHours = useMemo(() => {
    if (!regimeData?.generated_at) return null;
    const diff = Date.now() - new Date(regimeData.generated_at).getTime();
    return diff / (1000 * 60 * 60);
  }, [regimeData]);

  const allPresets = [...BUILTIN_PRESETS, ...savedPresets];
  const hasUnscoredSignals = displaySignals.some((s) => s.composite_score == null);
  const activeFilterSummary =
    selectedPresetName === 'Custom'
      ? `Custom • Min ${minScore === 0 ? 'Any' : minScore} • RVOL ${minRVOL}x`
      : selectedPresetName;

  return (
    <div className="min-h-screen bg-[#090d16] text-slate-100 flex flex-col">
      {/* Toast */}
      {toastMessage && (
        <div className="fixed bottom-6 right-6 z-50 flex items-center space-x-2.5 bg-slate-900/95 border border-emerald-500/50 text-white px-4 py-3 rounded-xl shadow-2xl backdrop-blur animate-in fade-in slide-in-from-bottom-2 duration-200">
          <div className="w-5 h-5 rounded-full bg-emerald-500/20 text-emerald-400 flex items-center justify-center flex-shrink-0">
            <CheckCircle2 className="w-3.5 h-3.5" />
          </div>
          <span className="text-xs font-medium text-slate-200">{toastMessage}</span>
        </div>
      )}

      {/* Header */}
      <header className="border-b border-slate-800/80 bg-[#0d1424]/90 backdrop-blur sticky top-0 z-40">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
          <div className="flex items-center space-x-3">
            <div className="w-9 h-9 rounded-lg bg-emerald-500/10 border border-emerald-500/30 flex items-center justify-center text-emerald-400">
              <TrendingUp className="w-5 h-5" />
            </div>
            <div>
              <div className="flex items-center space-x-2">
                <span className="font-bold text-base tracking-tight text-white">Market Screener</span>
                <span className="text-[10px] uppercase font-bold tracking-wider px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/30">
                  Live Daily
                </span>
              </div>
              <p className="text-[11px] text-slate-400">US Equities Momentum & Breakout Engine</p>
            </div>
          </div>

          <div className="flex items-center space-x-3 text-xs text-slate-400">
            {/* Freshness Badge from regime.json */}
            {regimeData?.date && (
              <div className="flex items-center space-x-1.5 bg-slate-900/80 px-3 py-1.5 rounded-lg border border-slate-700/80">
                <Calendar className="w-3.5 h-3.5 text-emerald-400" />
                <span className="text-slate-300 font-mono text-[11px]">
                  As of <strong className="text-white">{regimeData.date}</strong>
                </span>
              </div>
            )}

            <div className="flex items-center space-x-1.5 text-emerald-400 bg-emerald-500/10 border border-emerald-500/20 px-2.5 py-1.5 rounded-lg">
              <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
              <span className="font-semibold text-xs">Automated</span>
            </div>
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main className="flex-1 max-w-7xl w-full mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-8">
        {/* Stat Cards */}
        <section className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          {/* Card 1: Market Regime (from regime.json) */}
          <div className="relative overflow-hidden rounded-xl bg-[#0f172a] border border-slate-800/80 p-5 shadow-lg">
            <div className="absolute top-0 right-0 w-32 h-32 bg-emerald-500/5 rounded-full blur-2xl -mr-10 -mt-10" />
            <div className="flex items-center justify-between text-slate-400 text-xs font-medium uppercase tracking-wider mb-2">
              <span>Market Regime</span>
              <TrendingUp
                className={`w-4 h-4 ${
                  regimeData?.regime === 'Caution'
                    ? 'text-amber-400'
                    : regimeData?.regime === 'Bullish'
                    ? 'text-emerald-400'
                    : 'text-slate-500'
                }`}
              />
            </div>
            <div className="flex items-baseline space-x-2.5">
              <span
                className={`inline-flex items-center space-x-1.5 px-3 py-1 rounded-lg text-lg font-bold border ${
                  regimeData?.regime === 'Bullish'
                    ? 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30'
                    : regimeData?.regime === 'Caution'
                    ? 'bg-amber-500/15 text-amber-400 border-amber-500/30'
                    : 'bg-slate-800 text-slate-400 border-slate-700'
                }`}
              >
                <span
                  className={`w-2 h-2 rounded-full animate-pulse ${
                    regimeData?.regime === 'Bullish'
                      ? 'bg-emerald-400'
                      : regimeData?.regime === 'Caution'
                      ? 'bg-amber-400'
                      : 'bg-slate-500'
                  }`}
                />
                <span>{regimeData?.regime || 'Bullish'}</span>
              </span>
              {regimeData?.spy_pct_vs_sma200 != null && (
                <span className="text-[10px] font-semibold px-2 py-0.5 rounded bg-blue-500/10 text-blue-300 border border-blue-500/20 font-mono">
                  SPY {regimeData.spy_pct_vs_sma200 >= 0 ? '+' : ''}
                  {regimeData.spy_pct_vs_sma200.toFixed(1)}% vs 200d
                </span>
              )}
            </div>
            <p className="text-[11px] text-slate-400 mt-2 truncate font-mono">
              {regimeData?.pct_universe_above_sma50 != null
                ? `${regimeData.pct_universe_above_sma50.toFixed(0)}% of universe > 50-SMA`
                : 'SPY vs 200-SMA regime active'}
            </p>
          </div>

          {/* Card 2: Signals Today vs 30-Day Avg */}
          <div className="relative overflow-hidden rounded-xl bg-[#0f172a] border border-slate-800/80 p-5 shadow-lg">
            <div className="absolute top-0 right-0 w-32 h-32 bg-cyan-500/5 rounded-full blur-2xl -mr-10 -mt-10" />
            <div className="flex items-center justify-between text-slate-400 text-xs font-medium uppercase tracking-wider mb-2">
              <span>Signals Today vs 30d Avg</span>
              <Activity className="w-4 h-4 text-cyan-400" />
            </div>
            <div className="flex items-baseline space-x-3">
              <span className="text-3xl font-extrabold text-white tracking-tight font-mono">
                {loading ? '—' : todayCount}
              </span>
              <span className="text-xs text-cyan-400 font-mono">
                / {avg30Count} daily avg (30d)
              </span>
            </div>
            <p className="text-[11px] text-slate-400 mt-2">
              {signals.length} total historical signals loaded
            </p>
          </div>

          {/* Card 3: Average Breakout Gain */}
          <div className="relative overflow-hidden rounded-xl bg-[#0f172a] border border-slate-800/80 p-5 shadow-lg">
            <div className="absolute top-0 right-0 w-32 h-32 bg-purple-500/5 rounded-full blur-2xl -mr-10 -mt-10" />
            <div className="flex items-center justify-between text-slate-400 text-xs font-medium uppercase tracking-wider mb-2">
              <span>Avg Breakout Gain</span>
              <Sparkles className="w-4 h-4 text-purple-400" />
            </div>
            <div className="flex items-baseline space-x-3">
              <span className="text-3xl font-extrabold text-emerald-400 tracking-tight font-mono">
                {avgBreakoutGain}
              </span>
              <span className="text-xs text-purple-300 font-medium">Winning Setups</span>
            </div>
            <p className="text-[11px] text-slate-400 mt-2">
              Best available return horizon (5d / 10d / 20d)
            </p>
          </div>

          {/* Card 4: Data Freshness */}
          <div className="relative overflow-hidden rounded-xl bg-[#0f172a] border border-slate-800/80 p-5 shadow-lg">
            <div className="absolute top-0 right-0 w-32 h-32 bg-orange-500/5 rounded-full blur-2xl -mr-10 -mt-10" />
            <div className="flex items-center justify-between text-slate-400 text-xs font-medium uppercase tracking-wider mb-2">
              <span>Data Freshness</span>
              <Clock className="w-4 h-4 text-orange-400" />
            </div>
            <div className="flex items-baseline space-x-3">
              <span
                className={`text-2xl font-extrabold tracking-tight font-mono ${
                  freshnessAgeHours == null
                    ? 'text-slate-400'
                    : freshnessAgeHours < 6
                    ? 'text-emerald-400'
                    : freshnessAgeHours < 24
                    ? 'text-amber-400'
                    : 'text-rose-400'
                }`}
              >
                {relativeTime(regimeData?.generated_at)}
              </span>
              {regimeData?.date && (
                <span className="text-xs text-slate-400 font-mono">({regimeData.date})</span>
              )}
            </div>
            <p className="text-[11px] text-slate-400 mt-2 truncate">
              {regimeData?.generated_at
                ? `Exported ${relativeTime(regimeData.generated_at)} • Latest signal: ${signals[0]?.timestamp ?? regimeData.date ?? 'n/a'}`
                : 'Awaiting pipeline export'}
            </p>
          </div>
        </section>

        {/* Filter & Search Bar */}
        <section className="bg-[#0f172a] border border-slate-800 rounded-xl p-4 flex flex-col gap-3 shadow-md">
          <div className="flex flex-col md:flex-row gap-3 items-center justify-between">
            {/* Search */}
            <div className="flex-1 w-full flex items-center bg-[#090d16] border border-slate-700/80 rounded-lg px-3 py-2 text-xs focus-within:border-emerald-500 transition">
              <Search className="w-3.5 h-3.5 text-slate-400 mr-2 flex-shrink-0" />
              <input
                type="text"
                placeholder="Filter by ticker (e.g. AAPL) or company name..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="bg-transparent text-slate-100 placeholder-slate-500 focus:outline-none w-full text-xs"
              />
            </div>

            {/* Presets Control */}
            <div className="flex items-center space-x-2 text-xs w-full md:w-auto">
              <Bookmark className="w-3.5 h-3.5 text-slate-400 flex-shrink-0" />
              <span className="text-slate-400 font-medium whitespace-nowrap">Preset:</span>
              <select
                value={selectedPresetName}
                onChange={(e) => {
                  const p = allPresets.find((x) => x.name === e.target.value);
                  if (p) applyPreset(p);
                }}
                className="bg-[#090d16] text-slate-200 border border-slate-700 rounded-lg px-2.5 py-1.5 text-xs focus:outline-none focus:border-emerald-500"
              >
                {allPresets.map((p) => (
                  <option key={p.name} value={p.name}>
                    {p.name}
                  </option>
                ))}
              </select>
              <button
                onClick={saveCurrentPreset}
                className="px-2.5 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 text-xs font-medium whitespace-nowrap transition"
                title="Save current filters as a new preset"
              >
                Save
              </button>
              {savedPresets.some((p) => p.name === selectedPresetName) && (
                <button
                  onClick={() => deletePreset(selectedPresetName)}
                  className="p-1.5 rounded-lg bg-rose-950/40 hover:bg-rose-900/60 text-rose-400 border border-rose-800/40 transition"
                  title="Delete this custom preset"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              )}
            </div>
          </div>

          {/* Row 2: Detailed Filters */}
          <div className="flex flex-wrap items-center gap-3 pt-2 border-t border-slate-800/60 text-xs">
            <div className="flex items-center space-x-1.5 rounded-lg border border-emerald-500/20 bg-emerald-500/5 px-2 py-1 text-[11px] text-emerald-300">
              <span className="text-slate-400">Active:</span>
              <span className="font-semibold text-emerald-200">{activeFilterSummary}</span>
            </div>

            {/* Sector */}
            <div className="flex items-center space-x-1.5">
              <span className="text-slate-400">Sector:</span>
              <select
                value={selectedSector}
                onChange={(e) => {
                  setSelectedSector(e.target.value);
                  setSelectedPresetName('Custom');
                }}
                className="bg-[#090d16] text-slate-200 border border-slate-700 rounded-lg px-2.5 py-1 text-xs focus:outline-none focus:border-emerald-500"
              >
                <option value="ALL">All Sectors</option>
                {uniqueSectors.map((sec) => (
                  <option key={sec} value={sec}>
                    {sec}
                  </option>
                ))}
              </select>
            </div>

            {/* Min RVOL */}
            <div className="flex items-center space-x-1.5">
              <span className="text-slate-400">Min RVOL:</span>
              <select
                value={minRVOL}
                onChange={(e) => {
                  setMinRVOL(Number(e.target.value));
                  setSelectedPresetName('Custom');
                }}
                className="bg-[#090d16] text-slate-200 border border-slate-700 rounded-lg px-2 py-1 text-xs focus:outline-none focus:border-emerald-500 font-mono"
              >
                <option value={1.5}>≥ 1.5x</option>
                <option value={2.0}>≥ 2.0x</option>
                <option value={2.5}>≥ 2.5x</option>
                <option value={3.0}>≥ 3.0x</option>
              </select>
            </div>

            {/* Min Score */}
            <div className="flex items-center space-x-1.5">
              <span className="text-slate-400">Min Score:</span>
              <select
                value={minScore}
                onChange={(e) => {
                  setMinScore(Number(e.target.value));
                  setSelectedPresetName('Custom');
                }}
                className="bg-[#090d16] text-slate-200 border border-slate-700 rounded-lg px-2 py-1 text-xs focus:outline-none focus:border-emerald-500 font-mono"
              >
                <option value={0}>Any</option>
                <option value={40}>≥ 40</option>
                <option value={55}>≥ 55</option>
                <option value={70}>≥ 70</option>
              </select>
            </div>

            {/* Hide Earnings Toggle */}
            <label className="flex items-center space-x-2 text-slate-300 cursor-pointer select-none ml-1">
              <input
                type="checkbox"
                checked={hideEarnings}
                onChange={(e) => {
                  setHideEarnings(e.target.checked);
                  setSelectedPresetName('Custom');
                }}
                className="w-3.5 h-3.5 accent-emerald-500 rounded bg-[#090d16] border-slate-700"
              />
              <span className="text-xs">Hide earnings-flagged</span>
            </label>

            <span className="text-slate-600 ml-auto hidden sm:inline text-[11px] font-mono">
              Showing {displaySignals.length} of {signals.length} setups
            </span>
          </div>
        </section>

        {/* Signals Table */}
        <section className="bg-[#0f172a] border border-slate-800 rounded-xl overflow-hidden shadow-lg">
          {hasUnscoredSignals && (
            <div className="px-4 py-3 border-b border-amber-500/20 bg-amber-500/10 text-amber-200 text-xs font-medium">
              Scoring not yet computed for some signals — showing all matches.
            </div>
          )}

          <div className="px-6 py-4 border-b border-slate-800/80 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
            <div>
              <h2 className="text-base font-bold text-white tracking-tight flex items-center gap-2">
                Triggered Momentum Signals
                <span className="text-xs font-mono font-normal text-slate-400 bg-slate-800/80 px-2 py-0.5 rounded">
                  {displaySignals.length} results
                </span>
              </h2>
              <p className="text-xs text-slate-400 mt-0.5">
                Sort any column by clicking its header &bull; Click any row to view candlestick chart
              </p>
            </div>

            <button
              onClick={handleCopyTradingViewWatchlist}
              className="inline-flex items-center space-x-1.5 px-3.5 py-1.5 rounded-lg bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-400 border border-emerald-500/30 text-xs font-semibold transition active:scale-95 shadow-sm"
              title="Copy watchlist formatted for TradingView import"
            >
              <Copy className="w-3.5 h-3.5" />
              <span>Copy TradingView Watchlist</span>
            </button>
          </div>

          {loading ? (
            <div className="py-20 flex flex-col items-center justify-center text-slate-500 space-y-3">
              <RefreshCw className="w-8 h-8 animate-spin text-emerald-400" />
              <p className="text-xs">Loading momentum screener data...</p>
            </div>
          ) : error ? (
            <div className="py-16 flex flex-col items-center justify-center text-rose-400 space-y-3 px-4 text-center">
              <Info className="w-8 h-8" />
              <div>
                <p className="text-sm font-semibold">{error}</p>
                {errorUrl && (
                  <p className="text-xs text-slate-500 mt-1 font-mono">
                    Failed URL: <code className="text-rose-300 bg-slate-900 px-1 py-0.5 rounded">{errorUrl}</code>
                  </p>
                )}
              </div>
              <p className="text-xs text-slate-500 max-w-sm">
                Run <code className="text-slate-300 bg-slate-800 px-1 py-0.5 rounded">python run_daily.py --dry-run</code> to generate static JSON payloads.
              </p>
            </div>
          ) : displaySignals.length === 0 ? (
            <div className="py-16 text-center text-slate-500 space-y-2">
              <TrendingUp className="w-8 h-8 mx-auto stroke-[1.5] text-slate-700" />
              <p className="text-sm font-semibold text-slate-400">No breakout setups found</p>
              <p className="text-xs">
                {signals.length === 0
                  ? 'Screener ran but no signals met all criteria today — try an older date.'
                  : 'No signals match your current filter settings. Try relaxing the filters.'}
              </p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs sm:text-sm">
                <thead>
                  <tr className="bg-[#0a0f1d] text-slate-400 border-b border-slate-800 text-[11px] uppercase tracking-wider font-semibold">
                    <SortHeader col="symbol" label="Ticker" />
                    <SortHeader col="timestamp" label="Date" className="hidden md:table-cell" />
                    <SortHeader col="close_price" label="Price" align="right" />
                    <SortHeader col="pct_change_1d" label="Chg%" align="right" className="hidden md:table-cell" />
                    <SortHeader col="rvol" label="RVOL" align="right" />
                    <SortHeader col="rsi" label="RSI" align="right" className="hidden md:table-cell" />
                    <SortHeader col="atr_pct" label="ATR%" align="right" className="hidden md:table-cell" />
                    <SortHeader col="avg_dollar_vol_20d" label="$ Vol (20d)" align="right" className="hidden md:table-cell" />
                    <SortHeader col="rs_vs_spy" label="RS vs SPY" align="center" className="hidden md:table-cell" />
                    <SortHeader col="composite_score" label="Score" align="center" />
                    <SortHeader col="signal_streak" label="Streak" align="center" className="hidden md:table-cell" />
                    <SortHeader col="near_earnings" label="Earnings" align="center" className="hidden md:table-cell" />
                    <th className="py-3 px-3 text-left hidden md:table-cell">MA Alignment</th>
                    <th className="py-3 px-3 text-center w-10 sm:w-12" aria-label="Details and Chart">
                      <span className="md:hidden text-[10px] text-slate-500 font-normal">More</span>
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/60 font-mono text-xs">
                  {displaySignals.map((sig) => {
                    const rowKey = `${sig.symbol}-${sig.id}-${sig.timestamp}`;
                    const isExpanded = expandedRowIds.has(rowKey);
                    const isAbove50 = sig.ma_alignment?.above_sma50 !== false;
                    const isAbove20 = sig.ma_alignment?.above_ema20 === true;
                    const isAbove200 = sig.ma_alignment?.above_sma200 === true;
                    const hasEarnings = sig.near_earnings === true;

                    return (
                      <React.Fragment key={rowKey}>
                        <tr
                          onClick={() => setActiveSymbolForChart(sig)}
                          className={`hover:bg-slate-800/40 cursor-pointer transition group ${
                            isExpanded ? 'bg-slate-800/20' : ''
                          }`}
                        >
                          {/* 1. Ticker / Company (Mobile: always visible) */}
                          <td className="py-3 px-3 font-sans">
                            <div className="flex items-center space-x-2">
                              <span className="font-bold text-white text-sm tracking-tight font-mono group-hover:text-emerald-400 transition">
                                {sig.symbol}
                              </span>
                              <span className="text-[11px] text-slate-400 truncate max-w-[140px] hidden lg:inline">
                                {sig.name}
                              </span>
                            </div>
                            <span className="text-[10px] text-slate-500 block lg:hidden font-mono">
                              {sig.sector}
                            </span>
                          </td>

                          {/* 2. Date (Desktop only) */}
                          <td className="py-3 px-3 text-slate-400 font-mono whitespace-nowrap hidden md:table-cell">
                            {sig.timestamp}
                          </td>

                          {/* 3. Close Price (Mobile: always visible) */}
                          <td className="py-3 px-3 text-right font-semibold text-slate-100">
                            ${sig.close_price?.toFixed(2)}
                          </td>

                          {/* 4. % Change 1d (Desktop only) */}
                          <td className="py-3 px-3 text-right hidden md:table-cell">
                            {sig.pct_change_1d != null ? (
                              <span
                                className={`font-semibold ${
                                  sig.pct_change_1d >= 0 ? 'text-emerald-400' : 'text-rose-400'
                                }`}
                              >
                                {sig.pct_change_1d >= 0 ? '+' : ''}
                                {sig.pct_change_1d.toFixed(2)}%
                              </span>
                            ) : (
                              <span className="text-slate-600">—</span>
                            )}
                          </td>

                          {/* 5. RVOL (Mobile: always visible) */}
                          <td className="py-3 px-3 text-right">
                            <span className="inline-block px-1.5 py-0.5 rounded text-[11px] font-bold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                              {sig.rvol?.toFixed(2)}x
                            </span>
                          </td>

                          {/* 6. RSI (Desktop only) */}
                          <td className="py-3 px-3 text-right text-cyan-400 font-semibold hidden md:table-cell">
                            {sig.rsi?.toFixed(1)}
                          </td>

                          {/* 7. ATR% (Desktop only) */}
                          <td className="py-3 px-3 text-right hidden md:table-cell">
                            {sig.atr_pct != null ? (
                              <span
                                className={
                                  sig.atr_pct > 5 ? 'text-amber-400' : 'text-slate-300'
                                }
                              >
                                {sig.atr_pct.toFixed(1)}%
                              </span>
                            ) : (
                              <span className="text-slate-600">—</span>
                            )}
                          </td>

                          {/* 8. Dollar Volume (20d) (Desktop only) */}
                          <td className="py-3 px-3 text-right text-slate-300 hidden md:table-cell">
                            {formatDollarVol(sig.avg_dollar_vol_20d)}
                          </td>

                          {/* 9. RS vs SPY (Desktop only) */}
                          <td className="py-3 px-3 text-center hidden md:table-cell">
                            {sig.rs_vs_spy != null ? (
                              <span
                                className={`inline-block px-1.5 py-0.5 rounded text-[11px] font-semibold ${
                                  sig.rs_vs_spy >= 0
                                    ? 'bg-emerald-950/60 text-emerald-300 border border-emerald-500/30'
                                    : 'bg-rose-950/60 text-rose-300 border border-rose-500/30'
                                }`}
                              >
                                {sig.rs_vs_spy >= 0 ? '+' : ''}
                                {sig.rs_vs_spy.toFixed(1)}pp
                              </span>
                            ) : (
                              <span className="text-slate-600">—</span>
                            )}
                          </td>

                          {/* 10. Composite Score (Mobile: always visible) */}
                          <td className="py-3 px-3 text-center">
                            <ScorePill score={sig.composite_score} />
                          </td>

                          {/* 11. Signal Streak (Desktop only) */}
                          <td className="py-3 px-3 text-center hidden md:table-cell">
                            {sig.signal_streak != null && sig.signal_streak > 1 ? (
                              <span className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[11px] font-bold bg-orange-500/15 text-orange-400 border border-orange-500/30">
                                <Flame className="w-3 h-3" />
                                {sig.signal_streak}d
                              </span>
                            ) : (
                              <span className="text-slate-500 text-xs">1d</span>
                            )}
                          </td>

                          {/* 12. Earnings Flag (Desktop only) */}
                          <td className="py-3 px-3 text-center hidden md:table-cell">
                            {hasEarnings ? (
                              <span
                                className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold bg-amber-500/15 text-amber-300 border border-amber-500/30 whitespace-nowrap"
                                title={`Earnings on ${sig.earnings_date ?? 'upcoming'}`}
                              >
                                <AlertTriangle className="w-2.5 h-2.5" />
                                {sig.earnings_date ? sig.earnings_date.slice(5) : 'Near'}
                              </span>
                            ) : (
                              <span className="text-slate-600">—</span>
                            )}
                          </td>

                          {/* 13. MA Alignment Badges (Desktop only) */}
                          <td className="py-3 px-3 font-sans hidden md:table-cell">
                            <div className="flex flex-wrap gap-1">
                              {isAbove20 && (
                                <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[9px] font-medium bg-blue-950/70 text-blue-300 border border-blue-500/30">
                                  20
                                </span>
                              )}
                              {isAbove50 && (
                                <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[9px] font-medium bg-yellow-950/70 text-yellow-300 border border-yellow-500/30">
                                  50
                                </span>
                              )}
                              {isAbove200 && (
                                <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[9px] font-medium bg-purple-950/70 text-purple-300 border border-purple-500/30">
                                  200
                                </span>
                              )}
                            </div>
                          </td>

                          {/* 14. Action Column: Mobile chevron expand toggle / Desktop chart button */}
                          <td className="py-3 px-3 text-center">
                            {/* Mobile: Expand / Collapse chevron toggle */}
                            <button
                              type="button"
                              onClick={(e) => {
                                e.stopPropagation();
                                toggleRowExpand(rowKey);
                              }}
                              className="md:hidden p-1.5 rounded-lg bg-slate-800/80 hover:bg-slate-700 text-slate-300 hover:text-white border border-slate-700/80 transition"
                              aria-expanded={isExpanded}
                              aria-label="Toggle details"
                            >
                              {isExpanded ? (
                                <ChevronUp className="w-3.5 h-3.5 text-emerald-400" />
                              ) : (
                                <ChevronRight className="w-3.5 h-3.5" />
                              )}
                            </button>

                            {/* Desktop: Chart modal button */}
                            <button
                              type="button"
                              onClick={(e) => {
                                e.stopPropagation();
                                setActiveSymbolForChart(sig);
                              }}
                              className="hidden md:inline-flex p-1 rounded-lg bg-slate-800/80 hover:bg-emerald-500/20 text-slate-300 hover:text-emerald-400 border border-slate-700/80 transition"
                              title="Open Candlestick Chart"
                            >
                              <ExternalLink className="w-3.5 h-3.5" />
                            </button>
                          </td>
                        </tr>

                        {/* Mobile Expanded Details Drawer (<768px only) */}
                        {isExpanded && (
                          <tr className="md:hidden bg-[#090d16]/95 border-b border-slate-800 font-sans">
                            <td colSpan={5} className="p-3">
                              <div className="bg-[#0c1322] border border-slate-800/90 rounded-lg p-3.5 space-y-3">
                                <div className="flex items-center justify-between pb-2 border-b border-slate-800">
                                  <div>
                                    <span className="font-bold text-white text-xs">{sig.symbol}</span>
                                    <span className="text-[11px] text-slate-400 ml-2">{sig.name}</span>
                                  </div>
                                  <button
                                    type="button"
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      setActiveSymbolForChart(sig);
                                    }}
                                    className="inline-flex items-center space-x-1 px-2.5 py-1 rounded-md bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-400 border border-emerald-500/30 text-[11px] font-semibold transition"
                                  >
                                    <ExternalLink className="w-3 h-3" />
                                    <span>Open Chart</span>
                                  </button>
                                </div>

                                <div className="grid grid-cols-2 gap-x-4 gap-y-2 text-xs">
                                  {/* Sector */}
                                  <div className="flex justify-between items-center py-0.5 border-b border-slate-800/50">
                                    <span className="text-slate-400 text-[11px]">Sector</span>
                                    <span className="text-slate-200 font-mono text-[11px]">{sig.sector || 'Unknown'}</span>
                                  </div>

                                  {/* Date */}
                                  <div className="flex justify-between items-center py-0.5 border-b border-slate-800/50">
                                    <span className="text-slate-400 text-[11px]">Date</span>
                                    <span className="text-slate-300 font-mono text-[11px]">{sig.timestamp}</span>
                                  </div>

                                  {/* % Change 1d */}
                                  <div className="flex justify-between items-center py-0.5 border-b border-slate-800/50">
                                    <span className="text-slate-400 text-[11px]">1d Chg</span>
                                    <span className="font-mono text-[11px]">
                                      {sig.pct_change_1d != null ? (
                                        <span className={`font-semibold ${sig.pct_change_1d >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                                          {sig.pct_change_1d >= 0 ? '+' : ''}{sig.pct_change_1d.toFixed(2)}%
                                        </span>
                                      ) : (
                                        <span className="text-slate-600">—</span>
                                      )}
                                    </span>
                                  </div>

                                  {/* RSI */}
                                  <div className="flex justify-between items-center py-0.5 border-b border-slate-800/50">
                                    <span className="text-slate-400 text-[11px]">RSI (14)</span>
                                    <span className="text-cyan-400 font-mono font-semibold text-[11px]">
                                      {sig.rsi?.toFixed(1) ?? '—'}
                                    </span>
                                  </div>

                                  {/* ATR% */}
                                  <div className="flex justify-between items-center py-0.5 border-b border-slate-800/50">
                                    <span className="text-slate-400 text-[11px]">ATR%</span>
                                    <span className="font-mono text-[11px]">
                                      {sig.atr_pct != null ? (
                                        <span className={sig.atr_pct > 5 ? 'text-amber-400' : 'text-slate-300'}>
                                          {sig.atr_pct.toFixed(1)}%
                                        </span>
                                      ) : (
                                        <span className="text-slate-600">—</span>
                                      )}
                                    </span>
                                  </div>

                                  {/* 20d Dollar Volume */}
                                  <div className="flex justify-between items-center py-0.5 border-b border-slate-800/50">
                                    <span className="text-slate-400 text-[11px]">$ Vol (20d)</span>
                                    <span className="text-slate-300 font-mono text-[11px]">
                                      {formatDollarVol(sig.avg_dollar_vol_20d)}
                                    </span>
                                  </div>

                                  {/* RS vs SPY */}
                                  <div className="flex justify-between items-center py-0.5 border-b border-slate-800/50">
                                    <span className="text-slate-400 text-[11px]">RS vs SPY</span>
                                    <span className="font-mono text-[11px]">
                                      {sig.rs_vs_spy != null ? (
                                        <span className={sig.rs_vs_spy >= 0 ? 'text-emerald-400 font-semibold' : 'text-rose-400 font-semibold'}>
                                          {sig.rs_vs_spy >= 0 ? '+' : ''}{sig.rs_vs_spy.toFixed(1)}pp
                                        </span>
                                      ) : (
                                        <span className="text-slate-600">—</span>
                                      )}
                                    </span>
                                  </div>

                                  {/* Signal Streak */}
                                  <div className="flex justify-between items-center py-0.5 border-b border-slate-800/50">
                                    <span className="text-slate-400 text-[11px]">Streak</span>
                                    <span className="font-mono text-[11px]">
                                      {sig.signal_streak != null && sig.signal_streak > 1 ? (
                                        <span className="inline-flex items-center gap-0.5 text-orange-400 font-bold">
                                          <Flame className="w-3 h-3" />
                                          {sig.signal_streak}d
                                        </span>
                                      ) : (
                                        <span className="text-slate-400">1d</span>
                                      )}
                                    </span>
                                  </div>

                                  {/* Earnings */}
                                  <div className="flex justify-between items-center py-0.5 border-b border-slate-800/50">
                                    <span className="text-slate-400 text-[11px]">Earnings</span>
                                    <span className="text-[11px]">
                                      {hasEarnings ? (
                                        <span className="inline-flex items-center gap-1 text-amber-300 font-semibold">
                                          <AlertTriangle className="w-2.5 h-2.5" />
                                          {sig.earnings_date ? sig.earnings_date.slice(5) : 'Near'}
                                        </span>
                                      ) : (
                                        <span className="text-slate-500 font-mono">None</span>
                                      )}
                                    </span>
                                  </div>

                                  {/* 52w High Distance */}
                                  <div className="flex justify-between items-center py-0.5 border-b border-slate-800/50">
                                    <span className="text-slate-400 text-[11px]">52w High Dist</span>
                                    <span className="text-slate-300 font-mono text-[11px]">
                                      {sig.dist_to_52w_high_pct != null
                                        ? `${sig.dist_to_52w_high_pct >= 0 ? '+' : ''}${sig.dist_to_52w_high_pct.toFixed(1)}%`
                                        : '—'}
                                    </span>
                                  </div>
                                </div>

                                {/* MA Alignment in Drawer */}
                                <div className="flex items-center justify-between pt-1">
                                  <span className="text-slate-400 text-[11px]">MA Alignment</span>
                                  <div className="flex items-center gap-1">
                                    {isAbove20 && (
                                      <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[9px] font-medium bg-blue-950/70 text-blue-300 border border-blue-500/30">
                                        20-EMA
                                      </span>
                                    )}
                                    {isAbove50 && (
                                      <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[9px] font-medium bg-yellow-950/70 text-yellow-300 border border-yellow-500/30">
                                        50-SMA
                                      </span>
                                    )}
                                    {isAbove200 && (
                                      <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[9px] font-medium bg-purple-950/70 text-purple-300 border border-purple-500/30">
                                        200-SMA
                                      </span>
                                    )}
                                  </div>
                                </div>
                              </div>
                            </td>
                          </tr>
                        )}
                      </React.Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>

        {/* Strategy Track Record Section */}
        <TrackRecord setupStats={setupStats} />
      </main>

      {/* Chart Modal */}
      {activeSymbolForChart && (
        <StockChart
          symbol={activeSymbolForChart.symbol}
          name={activeSymbolForChart.name}
          sector={activeSymbolForChart.sector}
          closePrice={activeSymbolForChart.close_price}
          rvol={activeSymbolForChart.rvol}
          rsi={activeSymbolForChart.rsi}
          bars={barsMap[activeSymbolForChart.symbol] || []}
          signal={activeSymbolForChart}
          onClose={() => setActiveSymbolForChart(null)}
        />
      )}
    </div>
  );
}
