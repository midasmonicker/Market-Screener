'use client';

import React from 'react';
import { Trophy, TrendingUp, TrendingDown, Minus } from 'lucide-react';

interface SetupStat {
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
}

interface SetupStats {
  generated_at: string;
  setups: Record<string, SetupStat>;
}

interface TrackRecordProps {
  setupStats: SetupStats | null;
}

function ColourNum({
  value,
  suffix = '%',
  decimals = 1,
}: {
  value: number | null | undefined;
  suffix?: string;
  decimals?: number;
}) {
  if (value == null) return <span className="text-slate-500 font-mono">—</span>;
  const positive = value > 0;
  const zero = value === 0;
  return (
    <span
      className={`font-mono font-semibold ${
        zero
          ? 'text-slate-400'
          : positive
          ? 'text-emerald-400'
          : 'text-rose-400'
      }`}
    >
      {positive ? '+' : ''}
      {value.toFixed(decimals)}
      {suffix}
    </span>
  );
}

function StatCell({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[10px] uppercase tracking-wider text-slate-500 font-medium">
        {label}
      </span>
      <div className="text-sm">{children}</div>
    </div>
  );
}

function HitRate({ rate, n }: { rate: number; n: number }) {
  const colour =
    rate >= 60
      ? 'text-emerald-400'
      : rate >= 50
      ? 'text-cyan-400'
      : 'text-rose-400';
  return (
    <span className={`font-mono font-semibold ${colour}`}>
      {rate.toFixed(1)}%{' '}
      <span className="text-slate-500 text-xs font-normal">(n={n})</span>
    </span>
  );
}

export default function TrackRecord({ setupStats }: TrackRecordProps) {
  const isEmpty =
    !setupStats ||
    !setupStats.setups ||
    Object.keys(setupStats.setups).length === 0;

  return (
    <section className="bg-[#0f172a] border border-slate-800 rounded-xl overflow-hidden shadow-lg">
      <div className="px-6 py-4 border-b border-slate-800/80 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Trophy className="w-4 h-4 text-yellow-400" />
          <h2 className="text-base font-bold text-white tracking-tight">
            Strategy Track Record
          </h2>
        </div>
        {setupStats?.generated_at && (
          <span className="text-[10px] text-slate-500 font-mono hidden sm:block">
            Updated:{' '}
            {new Date(setupStats.generated_at).toLocaleString('en-GB', {
              dateStyle: 'short',
              timeStyle: 'short',
            })}
          </span>
        )}
      </div>

      {isEmpty ? (
        <div className="py-14 text-center text-slate-500 space-y-2">
          <Trophy className="w-8 h-8 mx-auto stroke-[1.5] text-slate-700" />
          <p className="text-sm font-semibold">No track record data yet.</p>
          <p className="text-xs">
            Run{' '}
            <code className="bg-slate-800 px-1.5 py-0.5 rounded text-slate-300">
              python outcomes.py --backfill
            </code>{' '}
            to compute historical performance.
          </p>
        </div>
      ) : (
        <div className="p-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {Object.entries(setupStats!.setups).map(([name, stat]) => (
            <div
              key={name}
              className="bg-[#0a0f1a] border border-slate-800/80 rounded-xl p-5 flex flex-col gap-4"
            >
              <div className="flex items-center justify-between">
                <span className="font-bold text-white text-sm">{name}</span>
                <span className="text-[10px] bg-slate-800 text-slate-400 px-2 py-0.5 rounded font-mono">
                  {stat.signal_count} signals
                </span>
              </div>

              <div className="grid grid-cols-2 gap-x-4 gap-y-3">
                <StatCell label="5d Hit Rate">
                  <HitRate rate={stat.hit_rate_5d} n={stat.completed_5d_count} />
                </StatCell>
                <StatCell label="20d Hit Rate">
                  <HitRate
                    rate={stat.hit_rate_20d}
                    n={stat.completed_20d_count}
                  />
                </StatCell>
                <StatCell label="Avg Excess 5d">
                  <ColourNum value={stat.avg_excess_5d} />
                </StatCell>
                <StatCell label="Avg Excess 20d">
                  <ColourNum value={stat.avg_excess_20d} />
                </StatCell>
                <StatCell label="Median Excess 5d">
                  <ColourNum value={stat.median_excess_5d} />
                </StatCell>
                <StatCell label="Median Max DD">
                  <ColourNum value={stat.median_max_drawdown} />
                </StatCell>
              </div>

              <div className="pt-1 border-t border-slate-800/60 flex items-center gap-2 text-[10px] text-slate-500">
                {stat.avg_excess_20d >= 0 ? (
                  <TrendingUp className="w-3 h-3 text-emerald-500" />
                ) : (
                  <TrendingDown className="w-3 h-3 text-rose-500" />
                )}
                <span>
                  {stat.avg_excess_20d >= 0
                    ? 'Positive edge vs SPY over 20 days'
                    : 'Underperforming SPY over 20 days'}
                </span>
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
