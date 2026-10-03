import datetime
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import pandas as pd

import screener
from outcomes import clear_signals_for_date, export_setup_stats, update_signal_outcomes
from run_daily import resolve_checkpoint_id
from screener import (
    _parse_sec_form4,
    get_performance_summary,
    persist_intraday_checkpoint_signals,
    process_single_ticker_screener,
)


class MomentumBreakdownTests(unittest.TestCase):
    def test_existing_breakout_rules_remain_available(self):
        closes = [100.0] * 200
        closes.extend([100.0 + 20.0 * step / 40 for step in range(1, 41)])
        price = closes[-1]
        for change in [-0.004, 0.006] * 9 + [0.004]:
            price *= 1 + change
            closes.append(price)
        frame = pd.DataFrame([
            {
                "timestamp": str(index),
                "open": close,
                "high": close * 1.01,
                "low": close * 0.99,
                "close": close,
                "volume": 200000 if index == len(closes) - 1 else 100000,
                "source": "polygon",
            }
            for index, close in enumerate(closes)
        ])

        result = process_single_ticker_screener(
            "BULLTEST", frame, "2026-10-01", rs_score=99.0, spy_return_63d=0.0
        )

        self.assertIsNotNone(result)
        self.assertEqual(result[2], "Momentum Breakout")
        details = json.loads(result[6])
        self.assertEqual(details["score_direction"], "bullish")

        checkpoint_result = process_single_ticker_screener(
            "BULLTEST",
            frame,
            "2026-10-01",
            rs_score=99.0,
            spy_return_63d=0.0,
            checkpoint_id="intraday_11am",
            allow_iex_checkpoint=True,
            checkpoint_rvol_baseline=100000.0,
        )
        self.assertIsNotNone(checkpoint_result)
        self.assertEqual(checkpoint_result[4], 2.0)
        checkpoint_details = json.loads(checkpoint_result[6])
        self.assertEqual(
            checkpoint_details["rvol_basis"],
            "partial_day_iex_vs_prior_20d_same_time_iex",
        )
        self.assertEqual(checkpoint_details["rvol_baseline_volume"], 100000.0)

    def test_breakdown_uses_inverted_rules_and_directional_score(self):
        closes = [350.0] * 198
        closes.extend([350.0 + (125.0 - 350.0) * step / 20 for step in range(1, 21)])
        price = closes[-1]
        for change in [-0.004, 0.0035] * 21:
            price *= 1 + change
            closes.append(price)
        closes.append(min(closes[-20:]) * 1.004)
        frame = pd.DataFrame([
            {
                "timestamp": str(index),
                "open": close,
                "high": close * 1.01,
                "low": close * 0.99,
                "close": close,
                "volume": 200000 if index == len(closes) - 1 else 100000,
                "source": "polygon",
            }
            for index, close in enumerate(closes)
        ])

        result = process_single_ticker_screener(
            "BEARTEST", frame, "2026-10-01", rs_score=1.0, spy_return_63d=8.0
        )

        self.assertIsNotNone(result)
        self.assertEqual(result[2], "Momentum Breakdown")
        details = json.loads(result[6])
        self.assertLessEqual(details["rs_vs_spy"], -70)
        self.assertLessEqual(details["dist_to_20d_low_pct"], 1)
        self.assertEqual(details["score_direction"], "bearish")
        self.assertTrue(all(value is False for key, value in details["ma_alignment"].items() if key.startswith("above_")))

    def test_sec_parser_accepts_only_open_market_purchase_and_sale_pairs(self):
        xml = """<ownershipDocument><reportingOwner><reportingOwnerId><rptOwnerName>Owner</rptOwnerName></reportingOwnerId></reportingOwner><nonDerivativeTable>
        <nonDerivativeTransaction><transactionCoding><transactionCode>P</transactionCode></transactionCoding><transactionAmounts><transactionShares><value>100</value></transactionShares><transactionPricePerShare><value>10</value></transactionPricePerShare><transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode></transactionAmounts><transactionDate><value>2026-10-01</value></transactionDate></nonDerivativeTransaction>
        <nonDerivativeTransaction><transactionCoding><transactionCode>S</transactionCode></transactionCoding><transactionAmounts><transactionShares><value>200</value></transactionShares><transactionPricePerShare><value>11</value></transactionPricePerShare><transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode></transactionAmounts><transactionDate><value>2026-10-01</value></transactionDate></nonDerivativeTransaction>
        <nonDerivativeTransaction><transactionCoding><transactionCode>S</transactionCode></transactionCoding><transactionAmounts><transactionShares><value>500</value></transactionShares><transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode></transactionAmounts><transactionDate><value>2026-10-01</value></transactionDate></nonDerivativeTransaction>
        </nonDerivativeTable></ownershipDocument>"""

        result = _parse_sec_form4(
            xml,
            "TEST",
            datetime.date(2026, 10, 1),
            datetime.date(2026, 9, 1),
            "2026-10-01",
            "https://www.sec.gov/Archives/filing.txt",
        )

        self.assertEqual(len(result["purchases"]), 1)
        self.assertEqual(len(result["sales"]), 1)
        self.assertEqual(result["sales"][0]["transaction_code"], "S")

    def test_performance_summary_splits_and_inverts_breakdowns(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(
            """CREATE TABLE buy_signals (
                timestamp TEXT, setup_name TEXT, return_5d_pct REAL,
                return_10d_pct REAL, return_20d_pct REAL, checkpoint_id TEXT
            )"""
        )
        conn.executemany(
            "INSERT INTO buy_signals VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("2026-09-28", "Momentum Breakout", 2.0, 3.0, 4.0, "eod"),
                ("2026-09-29", "Momentum Breakout", -1.0, -2.0, -3.0, "eod"),
                ("2026-09-28", "Momentum Breakdown", -2.0, -3.0, -4.0, "eod"),
                ("2026-09-29", "Momentum Breakdown", -5.0, -6.0, -7.0, "eod"),
                ("2026-09-29", "Momentum Breakdown", 90.0, 90.0, 90.0, "intraday_11am"),
            ],
        )

        summary = get_performance_summary(conn)

        self.assertEqual(summary["setups"]["Momentum Breakout"]["horizon_5d"]["win_rate"], 50.0)
        self.assertEqual(summary["setups"]["Momentum Breakdown"]["horizon_5d"]["win_rate"], 100.0)
        self.assertEqual(summary["setups"]["Momentum Breakdown"]["horizon_5d"]["avg_return"], 3.5)
        self.assertEqual(summary["setups"]["Momentum Breakdown"]["signal_count"], 2)
        conn.close()

    def test_track_record_export_keeps_setup_buckets_separate(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE buy_signals (id INTEGER, setup_name TEXT, checkpoint_id TEXT)")
        conn.execute("""CREATE TABLE signal_outcomes (
            signal_id INTEGER, ret_1d REAL, ret_5d REAL, ret_10d REAL, ret_20d REAL,
            max_drawdown_20d REAL, max_runup_20d REAL, spy_ret_5d REAL, spy_ret_20d REAL,
            excess_5d REAL, excess_20d REAL
        )""")
        conn.executemany(
            "INSERT INTO buy_signals VALUES (?, ?, ?)",
            [
                (1, "Momentum Breakout", "eod"),
                (2, "Momentum Breakdown", "eod"),
                (3, "Momentum Breakdown", "intraday_11am"),
            ],
        )
        conn.executemany(
            "INSERT INTO signal_outcomes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (1, 1, 2, 3, 4, -5, 7, 1, 2, 1, 2),
                (2, -1, -2, -3, -4, -8, 6, 1, 2, -3, -6),
                (3, 90, 90, 90, 90, -1, 1, 0, 0, 90, 90),
            ],
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            payload = export_setup_stats(
                output_path=str(Path(temp_dir) / "setup_stats.json"), conn=conn
            )

        self.assertEqual(set(payload["setups"]), {"Momentum Breakout", "Momentum Breakdown"})
        self.assertEqual(payload["setups"]["Momentum Breakdown"]["hit_rate_5d"], 100.0)
        self.assertEqual(payload["setups"]["Momentum Breakdown"]["avg_excess_5d"], 3.0)
        self.assertEqual(payload["setups"]["Momentum Breakdown"]["median_max_drawdown"], -6.0)
        self.assertEqual(payload["setups"]["Momentum Breakdown"]["signal_count"], 1)
        conn.close()

    def test_intraday_checkpoints_update_one_row_and_keep_eod_separate(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("""
            CREATE TABLE buy_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT, symbol TEXT, setup_name TEXT,
                close_price REAL, rvol REAL, rsi REAL, details TEXT,
                checkpoint_id TEXT NOT NULL DEFAULT 'eod'
            )
        """)
        first_details = {
            "checkpoint_id": "intraday_11am",
            "checkpoint_first_seen_id": "intraday_11am",
        }
        persist_intraday_checkpoint_signals(
            "2026-10-01",
            "intraday_11am",
            [("2026-10-01", "TEST", "Momentum Breakout", 10.0, 1.6, 60.0, json.dumps(first_details))],
            conn=conn,
        )
        persist_intraday_checkpoint_signals(
            "2026-10-01",
            "intraday_1pm",
            [("2026-10-01", "TEST", "Momentum Breakout", 11.0, 1.8, 62.0, "{}")],
            conn=conn,
        )
        conn.execute("""
            INSERT INTO buy_signals (timestamp, symbol, setup_name, close_price, rvol, rsi, details, checkpoint_id)
            VALUES ('2026-10-01', 'TEST', 'Momentum Breakout', 12.0, 2.0, 64.0, '{}', 'eod')
        """)

        checkpoint_rows = conn.execute(
            """SELECT close_price, rvol, details FROM buy_signals
               WHERE timestamp = '2026-10-01' AND checkpoint_id != 'eod'"""
        ).fetchall()
        self.assertEqual(len(checkpoint_rows), 1)
        self.assertEqual(checkpoint_rows[0][0:2], (11.0, 1.8))
        details = json.loads(checkpoint_rows[0][2])
        self.assertEqual(details["checkpoint_first_seen_id"], "intraday_11am")
        self.assertEqual(details["checkpoint_last_seen_id"], "intraday_1pm")
        self.assertEqual(details["checkpoint_status"], "active")
        self.assertEqual(details["checkpoints_seen"], ["intraday_11am", "intraday_1pm"])
        self.assertEqual(conn.execute(
            "SELECT count(*) FROM buy_signals WHERE timestamp = '2026-10-01'"
        ).fetchone()[0], 2)
        conn.close()

    def test_auto_checkpoint_resolution_matches_et_and_skips_companion_times(self):
        self.assertEqual(
            resolve_checkpoint_id(datetime.datetime(2026, 10, 1, 15, 0, tzinfo=datetime.timezone.utc)),
            "intraday_11am",
        )
        self.assertEqual(
            resolve_checkpoint_id(datetime.datetime(2026, 1, 5, 16, 0, tzinfo=datetime.timezone.utc)),
            "intraday_11am",
        )
        self.assertIsNone(
            resolve_checkpoint_id(datetime.datetime(2026, 10, 1, 16, 0, tzinfo=datetime.timezone.utc)),
        )
        self.assertIsNone(
            resolve_checkpoint_id(datetime.datetime(2026, 10, 1, 15, 10, tzinfo=datetime.timezone.utc)),
        )
        self.assertIsNone(
            resolve_checkpoint_id(datetime.datetime(2026, 10, 1, 14, 55, tzinfo=datetime.timezone.utc)),
        )
        self.assertEqual(
            resolve_checkpoint_id(
                datetime.datetime(2026, 10, 1, 13, 0, tzinfo=ZoneInfo("America/New_York"))
            ),
            "intraday_1pm",
        )

    def test_checkpoint_rvol_uses_paginated_same_time_iex_history(self):
        history_days = pd.bdate_range(end="2026-10-01", periods=21, inclusive="left")
        history_dates = [day.date().isoformat() for day in history_days]
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE daily_bars (timestamp TEXT)")
        conn.executemany(
            "INSERT INTO daily_bars VALUES (?)",
            [(day,) for day in history_dates],
        )
        ny_timezone = ZoneInfo("America/New_York")
        schedule_rows = []
        for day in history_days:
            session_date = day.date()
            market_open = datetime.datetime.combine(
                session_date, datetime.time(9, 30), tzinfo=ny_timezone
            ).astimezone(datetime.timezone.utc)
            market_close = datetime.datetime.combine(
                session_date, datetime.time(16, 0), tzinfo=ny_timezone
            ).astimezone(datetime.timezone.utc)
            schedule_rows.append((market_open, market_close))
        schedule = pd.DataFrame(
            schedule_rows,
            index=pd.DatetimeIndex(history_days, tz="UTC"),
            columns=["market_open", "market_close"],
        )

        def timestamp(session_date, hour, minute):
            return datetime.datetime.combine(
                datetime.date.fromisoformat(session_date),
                datetime.time(hour, minute),
                tzinfo=ny_timezone,
            ).astimezone(datetime.timezone.utc).isoformat().replace("+00:00", "Z")

        test_bars = []
        second_bars = []
        for day_index, session_date in enumerate(history_dates):
            test_bars.extend([
                {"t": timestamp(session_date, 9, 0), "v": 888},
                {"t": timestamp(session_date, 9, 30), "v": 100},
                {"t": timestamp(session_date, 10, 0), "v": 200},
                {"t": timestamp(session_date, 10, 30), "v": 300},
                {"t": timestamp(session_date, 11, 0), "v": 999},
            ])
            if day_index:
                second_bars.extend([
                    {"t": timestamp(session_date, 9, 30), "v": 50},
                    {"t": timestamp(session_date, 10, 0), "v": 50},
                    {"t": timestamp(session_date, 10, 30), "v": 50},
                ])

        first_page = Mock(
            status_code=200,
            headers={},
            json=Mock(return_value={
                "bars": {"TEST": test_bars, "SECOND": second_bars},
                "next_page_token": "next-page",
            }),
        )
        second_page = Mock(
            status_code=200,
            headers={},
            json=Mock(return_value={"bars": {}, "next_page_token": None}),
        )
        requests_mock = Mock(side_effect=[first_page, second_page])

        with (
            patch("screener.get_connection", return_value=conn),
            patch("screener.mcal.get_calendar") as calendar_mock,
            patch("screener.requests.get", requests_mock),
            patch("screener.time.sleep"),
            patch.object(screener, "ALPACA_API_KEY", "test-key"),
            patch.object(screener, "ALPACA_API_SECRET", "test-secret"),
        ):
            calendar_mock.return_value.schedule.return_value = schedule
            baselines = screener.fetch_alpaca_checkpoint_rvol_baselines(
                ["TEST", "SECOND"],
                "2026-10-01",
                "intraday_11am",
            )

        self.assertEqual(baselines["TEST"], 600.0)
        self.assertEqual(baselines["SECOND"], 142.5)
        self.assertEqual(requests_mock.call_count, 2)
        first_params = requests_mock.call_args_list[0].kwargs["params"]
        second_params = requests_mock.call_args_list[1].kwargs["params"]
        self.assertEqual(first_params["timeframe"], "30Min")
        self.assertEqual(first_params["feed"], "iex")
        self.assertEqual(second_params["page_token"], "next-page")
        self.assertEqual(second_params["feed"], "iex")
        conn.close()

    def test_forward_outcomes_and_backfill_cleanup_are_eod_only(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("""
            CREATE TABLE buy_signals (
                id INTEGER PRIMARY KEY, timestamp TEXT, symbol TEXT, setup_name TEXT,
                close_price REAL, checkpoint_id TEXT NOT NULL DEFAULT 'eod'
            )
        """)
        conn.execute("""
            CREATE TABLE daily_bars (
                symbol TEXT, timestamp TEXT, open REAL, high REAL,
                low REAL, close REAL
            )
        """)
        conn.executemany(
            "INSERT INTO buy_signals VALUES (?, ?, ?, ?, ?, ?)",
            [
                (1, "2026-09-01", "TEST", "Momentum Breakout", 100.0, "eod"),
                (2, "2026-09-01", "TEST", "Momentum Breakout", 101.0, "intraday_11am"),
            ],
        )
        subsequent_dates = [
            (datetime.date(2026, 9, 2) + datetime.timedelta(days=offset)).isoformat()
            for offset in range(20)
        ]
        conn.executemany(
            "INSERT INTO daily_bars VALUES (?, ?, ?, ?, ?, ?)",
            [
                (symbol, day, 100.0, 102.0, 99.0, 101.0)
                for symbol in ("TEST", "SPY")
                for day in subsequent_dates
            ],
        )

        self.assertEqual(update_signal_outcomes(conn), 1)
        self.assertEqual(
            conn.execute("SELECT signal_id FROM signal_outcomes").fetchall(),
            [(1,)],
        )
        clear_signals_for_date("2026-09-01", conn=conn)
        remaining = conn.execute(
            "SELECT id, checkpoint_id FROM buy_signals ORDER BY id"
        ).fetchall()
        self.assertEqual(remaining, [(2, "intraday_11am")])
        conn.close()


if __name__ == "__main__":
    unittest.main()