"""Market-data ingestion handlers (P3). Run only by the worker.

Bounds: <= MAX_EXPIRIES expiries and <= MAX_CONTRACTS_PER_EXPIRY contracts per
batch; daily bars limited to the requested window (<= 400 days).
Idempotency: natural-key unique constraints + insert-or-ignore, so a retried
or duplicated job writes nothing twice. Partial recovery: each expiry commits
on its own; the batch records which expiries failed (status "partial") and the
job's retry re-fetches only what is missing (dedupe skips the rest).
Synthetic data is refused: mock-provider output never enters these tables.
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.dialects import postgresql, sqlite

from app.analytics.chain import AnalyticsConfig, atm_iv, enrich_chain, forward_price, time_to_expiry
from app.analytics.snapshot import IV_METHOD
from app.analytics.vol import constant_maturity
from app.db.models import IngestBatch, IVDaily, OptionContract, OptionQuoteSnapshot, UnderlyingDailyBar, utcnow
from app.db.session import Database

MAX_EXPIRIES = 6
MAX_CONTRACTS_PER_EXPIRY = 500
MAX_BAR_DAYS = 400
NY = ZoneInfo("America/New_York")


class SyntheticDataRefused(RuntimeError):
    pass


def _insert_ignore(session, model, rows: list[dict], index_elements: list[str]) -> int:
    if not rows:
        return 0
    dialect = session.bind.dialect.name
    ins = (postgresql.insert if dialect == "postgresql" else sqlite.insert)(model.__table__)
    result = session.execute(ins.on_conflict_do_nothing(index_elements=index_elements), rows)
    return int(result.rowcount or 0)


def _batch(db: Database, provider: str, kind: str, params: dict) -> str:
    with db.session() as s:
        b = IngestBatch(provider=provider, kind=kind, params=params)
        s.add(b)
        s.flush()
        return b.id


def _finish(db: Database, batch_id: str, status: str, rows: int, error: str = "") -> None:
    with db.session() as s:
        b = s.get(IngestBatch, batch_id)
        b.status, b.rows_written, b.error, b.finished_at = status, rows, error[:2000], utcnow()


async def ingest_chain_snapshot(db: Database, provider, payload: dict, cfg: AnalyticsConfig,
                                should_continue=lambda: True) -> dict:
    if provider.data_status == "synthetic":
        raise SyntheticDataRefused("refusing to store synthetic quotes")
    ticker = str(payload["ticker"]).upper()
    max_exp = min(int(payload.get("max_expiries", 4)), MAX_EXPIRIES)
    now = dt.datetime.now(dt.UTC)
    batch_id = _batch(db, provider.name, "chain_snapshot", {"ticker": ticker, "max_expiries": max_exp})
    underlying = await provider.get_underlying_price(ticker)
    expirations = [e for e in await provider.list_expirations(ticker) if 7 <= time_to_expiry(e, now) * 365 <= 180][:max_exp]
    written, failed, term = 0, [], []
    for exp in expirations:
        if not should_continue():
            failed.append(f"{exp}:cancelled")
            break
        try:
            quotes = (await provider.get_chain(ticker, exp))[:MAX_CONTRACTS_PER_EXPIRY]
            with db.session() as s:
                contracts = [{"occ_symbol": q.symbol, "underlying": ticker, "expiration": dt.date.fromisoformat(exp),
                              "strike": q.strike, "option_type": q.option_type, "multiplier": q.multiplier,
                              "adjusted": q.adjusted, "currency": q.currency} for q in quotes if q.symbol]
                _insert_ignore(s, OptionContract, contracts, ["occ_symbol"])
                ids = dict(s.execute(select(OptionContract.occ_symbol, OptionContract.id).where(
                    OptionContract.occ_symbol.in_([c["occ_symbol"] for c in contracts]))).all())
                snaps = []
                for q in quotes:
                    if not q.symbol or q.symbol not in ids:
                        continue
                    as_of = dt.datetime.fromisoformat(q.quote_time) if q.quote_time else now
                    snaps.append({"contract_id": ids[q.symbol], "as_of": as_of, "provider": provider.name,
                                  "data_status": provider.data_status, "bid": q.bid, "ask": q.ask, "last": q.last,
                                  "volume": q.volume, "open_interest": q.open_interest,
                                  "implied_volatility": q.implied_volatility, "iv_source": q.iv_source or "",
                                  "underlying_price": underlying, "batch_id": batch_id})
                written += _insert_ignore(s, OptionQuoteSnapshot, snaps, ["contract_id", "as_of", "provider"])
            enriched = enrich_chain(quotes, underlying, exp, now, cfg)
            T = time_to_expiry(exp, now)
            atm = atm_iv(enriched, forward_price(underlying, T, cfg))
            if atm.value is not None:
                term.append((T * 365, atm.value))
        except Exception as exc:  # noqa: BLE001 - recorded; other expiries still commit
            failed.append(f"{exp}:{exc.__class__.__name__}")
    iv30 = constant_maturity(term, 30)
    if iv30.value is not None:
        with db.session() as s:
            written += _insert_ignore(s, IVDaily, [{"symbol": ticker, "date": now.astimezone(NY).date(), "iv": iv30.value,
                                                    "method": IV_METHOD, "provider": provider.name, "batch_id": batch_id}],
                                      ["symbol", "date", "method", "provider"])
    status = "succeeded" if not failed else ("partial" if written else "failed")
    _finish(db, batch_id, status, written, "; ".join(failed))
    if status == "failed":
        raise RuntimeError("no expiries ingested: " + "; ".join(failed))
    return {"batch_id": batch_id, "rows_written": written, "failed_expiries": failed, "iv_30d": iv30.value,
            "iv_30d_unavailable_reason": iv30.reason or None}


async def ingest_daily_bars(db: Database, provider, payload: dict) -> dict:
    if provider.data_status == "synthetic":
        raise SyntheticDataRefused("refusing to store synthetic bars")
    ticker = str(payload["ticker"]).upper()
    end = dt.date.fromisoformat(payload.get("end") or dt.date.today().isoformat())
    days = min(int(payload.get("days", 400)), MAX_BAR_DAYS)
    batch_id = _batch(db, provider.name, "daily_bars", {"ticker": ticker, "end": end.isoformat(), "days": days})
    bars = await provider.get_daily_bars(ticker, end - dt.timedelta(days=days), end)
    with db.session() as s:
        n = _insert_ignore(s, UnderlyingDailyBar, [{"symbol": ticker, "date": d, "close": c, "adj_close": None,
                                                    "provider": provider.name, "batch_id": batch_id} for d, c in bars],
                           ["symbol", "date", "provider"])
    _finish(db, batch_id, "succeeded", n)
    return {"batch_id": batch_id, "rows_written": n, "bars_seen": len(bars)}
