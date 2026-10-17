"""Persistência SQLite e regra de desconto sobre histórico anterior."""
import sqlite3
import statistics
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from .money import cents


def connect(db_path: str, *, dry_run: bool = False) -> sqlite3.Connection:
    path = Path(db_path)
    if dry_run:
        db = sqlite3.connect(":memory:")
        if path.exists():
            from contextlib import closing
            with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as source:
                source.backup(db)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(path)
    db.execute("CREATE TABLE IF NOT EXISTS prices (product_id TEXT NOT NULL, observed_at TEXT NOT NULL, price_cents INTEGER NOT NULL, PRIMARY KEY (product_id, observed_at))")
    db.execute("CREATE INDEX IF NOT EXISTS ix_prices_product_time ON prices(product_id, observed_at)")
    db.execute("CREATE TABLE IF NOT EXISTS alerts (product_id TEXT NOT NULL, price_cents INTEGER NOT NULL, sent_at TEXT NOT NULL, PRIMARY KEY(product_id, price_cents))")
    db.commit()
    return db


def record(db: sqlite3.Connection, product_id: str, price: Decimal, now: datetime):
    db.execute("INSERT INTO prices VALUES (?, ?, ?)", (product_id, now.isoformat(), cents(price)))
    db.commit()


def assess(db: sqlite3.Connection, product_id: str, current: Decimal, now: datetime, min_discount: Decimal, min_samples: int, min_days: int):
    """Compara a mediana de preços ANTERIORES, após histórico cobrindo min_days."""
    rows = db.execute("SELECT observed_at, price_cents FROM prices WHERE product_id=? AND observed_at<? ORDER BY observed_at", (product_id, now.isoformat())).fetchall()
    if len(rows) < min_samples:
        return None
    first = datetime.fromisoformat(rows[0][0])
    if now - first < timedelta(days=min_days):
        return None
    baseline = Decimal(str(statistics.median([r[1] for r in rows]))) / 100
    if baseline <= 0:
        return None
    discount = (baseline - current) / baseline
    if discount < min_discount:
        return None
    return baseline, discount


def was_alerted(db: sqlite3.Connection, product_id: str, price: Decimal) -> bool:
    return db.execute("SELECT 1 FROM alerts WHERE product_id=? AND price_cents=?", (product_id, cents(price))).fetchone() is not None


def mark_alerted(db: sqlite3.Connection, product_id: str, price: Decimal, now: datetime):
    db.execute("INSERT OR IGNORE INTO alerts VALUES (?, ?, ?)", (product_id, cents(price), now.isoformat()))
    db.commit()


