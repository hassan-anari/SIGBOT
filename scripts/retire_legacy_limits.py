"""One-time SQLite maintenance for the six audited November 23 Limit Signals."""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys

TARGETS = {
    551: "SKYUSDT", 556: "AUSDT", 572: "ARUSDT",
    703: "MAGMAUSDT", 798: "ALPINEUSDT", 800: "LSKUSDT",
}
CONFIRMATION = "RETIRE-551-556-572-703-798-800"
LEGACY_REVISION = "20261026_01"
AUTOMATIC_REVISION = "20261123_01"
IDS = ",".join("?" for _ in TARGETS)
UNACTIVATED_NO_ROWS = (
    "destination_publications", "initial_publication_requests",
    "initial_publication_notifications", "signal_details_publications",
    "limit_cancellation_publications", "signal_cancellation_publications",
    "destination_update_publications", "update_publication_requests",
    "signal_update_candidates", "signal_events", "breakeven_prompts",
    "signal_monitor_states", "signal_position_states", "signal_position_actions",
    "signal_position_action_publications", "signal_comment_requests",
    "signal_comment_publications",
)
SIGNAL_OWNED_TABLES = set(UNACTIVATED_NO_ROWS) | {
    "destination_trade_locks", "limit_admission_requests", "limit_entry_triggers",
    "limit_entry_trigger_source_prices", "limit_pending_publications",
    "signal_exchange_symbols", "signal_initial_prices", "signal_take_profits",
}
EXPECTED_TABLES = SIGNAL_OWNED_TABLES | {
    "admin_alert_outbox", "alembic_version", "destination_exchange_priorities",
    "destination_message_templates", "destination_signalists", "destination_warmup_cooldowns",
    "destinations", "initial_publication_daily_slots", "outage_incidents",
    "service_health_states", "signal_event_source_prices", "signal_templates",
    "signalist_notifications", "signalists", "signals", "warmup_publications", "warmup_requests",
}
UNSENT = {"pending", "blocked_template"}
TERMINAL_UNSENT = {"failed", "superseded", "skipped_daily_limit", "skipped_inactive",
                   "skipped_unassigned"}


class RetirementRefused(RuntimeError):
    pass


def require(condition, reason):
    if not condition:
        raise RetirementRefused(reason)


def connect(path, *, writable=False):
    db = sqlite3.connect(path.as_uri() + ("?mode=rw" if writable else "?mode=ro"),
                         uri=True, timeout=0, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    if not writable:
        db.execute("PRAGMA query_only = ON")
    return db


def check_integrity(db):
    require(db.execute("PRAGMA foreign_keys").fetchone()[0] == 1, "foreign keys are disabled")
    require([row[0] for row in db.execute("PRAGMA integrity_check")] == ["ok"],
            "SQLite integrity_check failed")
    require(db.execute("PRAGMA foreign_key_check").fetchone() is None, "foreign_key_check failed")


def check_schema(db):
    revisions = [row[0] for row in db.execute("SELECT version_num FROM alembic_version")]
    require(revisions in ([LEGACY_REVISION], [AUTOMATIC_REVISION]), "unsupported Alembic revision")
    schema = db.execute("SELECT name, type FROM sqlite_schema "
                        "WHERE type IN ('table', 'trigger') AND name NOT GLOB 'sqlite_*'").fetchall()
    require(all(kind == "table" for _, kind in schema), "unexpected SQLite trigger")
    require({name for name, _ in schema} == EXPECTED_TABLES, "unexpected SQLite tables")
    for name in SIGNAL_OWNED_TABLES:
        require(any(row[1] == "signal_id" for row in db.execute(
            "SELECT * FROM pragma_table_info(?)", (name,))), f"unexpected Signal ownership in {name}")
    columns = {row[1] for row in db.execute("PRAGMA table_info(signals)")}
    require(("auto_activate_limit" in columns) == (revisions == [AUTOMATIC_REVISION]),
            "unexpected automatic activation column")
    return revisions[0]


def target_rows(db, table, key="signal_id"):
    # Table and column names are fixed by this module.
    return db.execute(f"SELECT * FROM {table} WHERE {key} IN ({IDS}) ORDER BY {key}",
                      tuple(TARGETS)).fetchall()


def positive_price(raw):
    try:
        price = Decimal(raw)
    except (InvalidOperation, TypeError, ValueError):
        raise RetirementRefused("invalid persisted price") from None
    require(price.is_finite() and price > 0, "invalid persisted price")
    return price


def inspect(db):
    revision = check_schema(db)
    check_integrity(db)
    signals = target_rows(db, "signals", "id")
    require({row["id"]: row["canonical_symbol"] for row in signals} == TARGETS,
            "audited Signal IDs or symbols differ")
    states = {row["status"] for row in signals}
    require(states in ({"entry_triggered"}, {"cancelled"}), "unexpected or mixed lifecycle states")
    completed = states == {"cancelled"}
    timestamps = {row["cancelled_at"] for row in signals}
    require(all(row["entry_type"] == "limit" and
                (revision == LEGACY_REVISION or row["auto_activate_limit"] == 0)
                for row in signals), "expected historical Limit Signals")
    require(timestamps == {None} if not completed else
            len(timestamps) == 1 and None not in timestamps and
            all(row["updated_at"] == row["cancelled_at"] for row in signals),
            "unexpected cancellation timestamps")

    for table in UNACTIVATED_NO_ROWS:
        require(not target_rows(db, table), f"unexpected records in {table}")

    report = []
    preserved = []
    unsent_ids = []
    admission_ids = []
    for signal in signals:
        signal_id = signal["id"]
        def rows(table, key="signal_id"):
            return db.execute(f"SELECT * FROM {table} WHERE {key} = ?",
                              (signal_id,)).fetchall()

        triggers = rows("limit_entry_triggers")
        notes = rows("signalist_notifications", "limit_trigger_signal_id")
        require(len(triggers) == len(notes) == 1, f"Signal {signal_id} lacks Trigger or notification evidence")
        trigger, note = triggers[0], notes[0]
        entry = positive_price(signal["entry_price"])
        observed = positive_price(trigger["observed_reference_price"])
        require(entry == positive_price(trigger["requested_entry"]) and
                trigger["price_source_type"] == signal["price_source_type"] and
                ((signal["direction"] == "long" and observed <= entry) or
                 (signal["direction"] == "short" and observed >= entry)),
                f"Signal {signal_id} has inconsistent Trigger prices")
        require(note["kind"] == "limit_trigger" and note["status"] == "sent" and
                note["signalist_id"] == signal["signalist_id"] and
                note["telegram_message_id"] and note["telegram_message_id"] > 0 and
                note["sent_at"],
                f"Signal {signal_id} lacks definitely delivered Trigger notification")

        source_prices = rows("limit_entry_trigger_source_prices")
        mappings = rows("signal_exchange_symbols")
        initial_prices = rows("signal_initial_prices")
        require(source_prices and {r["exchange"] for r in source_prices} <=
                {r["exchange"] for r in mappings}, f"Signal {signal_id} has inconsistent Trigger sources")
        for source in source_prices:
            positive_price(source["price"])

        pending = rows("limit_pending_publications")
        admissions = rows("limit_admission_requests")
        require(len(admissions) == 1, f"Signal {signal_id} lacks admission evidence")
        admission = admissions[0]
        if admission["status"] == "pending" and not completed:
            admission_ids.append(admission["id"])
        else:
            require(admission["status"] in {"planned", "superseded"},
                    f"Signal {signal_id} has unsafe admission state")
        require(bool(admission["planned_at"]) == (admission["status"] == "planned") and
                (not pending or admission["status"] == "planned"),
                f"Signal {signal_id} has inconsistent admission evidence")

        locks = rows("destination_trade_locks")
        lock_destinations = {row["destination_id"] for row in locks}
        require(all(row["canonical_symbol"] == signal["canonical_symbol"] for row in locks),
                f"Signal {signal_id} owns a lock for an unexpected symbol")
        eligible_locks = set()
        sent_count = 0
        kept_publications = []
        for publication in pending:
            publication_id = publication["id"]
            destination = publication["destination_id"]
            status = publication["status"]
            slots = db.execute("SELECT * FROM initial_publication_daily_slots WHERE limit_publication_id = ?",
                               (publication_id,)).fetchall()
            require(all(row["destination_id"] == destination for row in slots),
                    f"Signal {signal_id} has a mismatched daily slot")
            if status == "sent":
                require(len(slots) == 1 and publication["retry_at"] is None,
                        f"Signal {signal_id} has inconsistent sent Pending admission")
                kept_publications.append((tuple(publication), tuple(slots[0])))
                eligible_locks.add(destination)
                sent_count += 1
            elif status in UNSENT and not completed:
                require(publication["attempt_count"] == 0 and publication["retry_at"] is None and
                        len(slots) == 1 and destination in lock_destinations,
                        f"Signal {signal_id} has Pending work with uncertain delivery or admission")
                unsent_ids.append(publication_id)
                eligible_locks.add(destination)
            else:
                require(status in TERMINAL_UNSENT and not slots,
                        f"Signal {signal_id} has unsafe Pending delivery state {status}")
        require(lock_destinations <= eligible_locks, f"Signal {signal_id} has orphaned admission locks")

        original = tuple((key, signal[key]) for key in signal.keys()
                         if key not in {"status", "updated_at", "cancelled_at"})
        preserved.append((original, tuple(trigger), tuple(note), sorted(map(tuple, source_prices)),
                          sorted(map(tuple, initial_prices)), sorted(kept_publications)))
        report.append({"id": signal_id, "symbol": signal["canonical_symbol"],
                       "status": signal["status"], "cancelled_at": signal["cancelled_at"],
                       "sent_pending": sent_count, "trade_locks": len(locks),
                       "unsent_pending": sum(row["status"] in UNSENT for row in pending),
                       "admission": admission["status"], "trigger_notification": note["status"],
                       "cancellation_intents": 0})
    if completed:
        require(all(row["trade_locks"] == 0 for row in report), "cancelled Signals still own locks")
    else:
        require(sum(row["trade_locks"] for row in report) == 38 and
                {row["id"] for row in report if not row["trade_locks"]} == {551},
                "Trade Lock ownership differs from the audit")
    return completed, report, preserved, unsent_ids, admission_ids


def validate_backup(path):
    with closing(connect(path)) as snapshot:
        check_schema(snapshot)
        check_integrity(snapshot)


def backup(source_path, backup_path):
    with closing(connect(source_path)) as source:
        descriptor = os.open(backup_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        with closing(connect(backup_path, writable=True)) as snapshot:
            def check_progress(status, _remaining, _total):
                require(status in (sqlite3.SQLITE_OK, sqlite3.SQLITE_DONE), "SQLite backup was busy")
            source.backup(snapshot, progress=check_progress)
    validate_backup(backup_path)


def require_stopped(path, proc=Path("/proc")):
    """Check the normal Linux entry point and same-account open SQLite handles."""
    require(sys.platform.startswith("linux") and (proc / "self/fd").is_dir(),
            "apply requires a local Linux maintenance window with /proc")
    watched = {path, Path(str(path) + "-wal"), Path(str(path) + "-shm")}
    identities = set()
    for file in watched:
        try:
            metadata = file.stat()
        except FileNotFoundError:
            continue
        identities.add((metadata.st_dev, metadata.st_ino))
    for process in proc.iterdir():
        if not process.name.isdecimal() or int(process.name) == os.getpid():
            continue
        try:
            if process.stat().st_uid != os.geteuid():
                continue
            args = (process / "cmdline").read_bytes().split(b"\0")
            require(b"chainura.telegram" not in args and
                    not any(arg.endswith(b"/chainura/telegram/__main__.py") for arg in args),
                    f"SIGBOT runtime is still active (PID {process.name})")
            for fd in (process / "fd").iterdir():
                try:
                    name = os.readlink(fd).removesuffix(" (deleted)")
                    metadata = fd.stat()
                except FileNotFoundError:
                    continue
                require(Path(name) not in watched and (metadata.st_dev, metadata.st_ino) not in identities,
                        f"SQLite is open in another process (PID {process.name})")
        except (FileNotFoundError, ProcessLookupError):
            continue
        except PermissionError:
            raise RetirementRefused(f"could not verify process {process.name} is stopped") from None


def retire(path, *, backup_path=None):
    """Inspect read-only, or back up and retire under one SQLite write transaction."""
    applying = backup_path is not None
    if applying:
        require_stopped(path)
    with closing(connect(path, writable=applying)) as db:
        db.execute("BEGIN IMMEDIATE" if applying else "BEGIN")
        completed, before, preserved, unsent_ids, admission_ids = inspect(db)
        if not applying or completed:
            db.rollback()
            return {"result": "already_retired" if completed else "dry_run", "signals": before}

        # The reserved writer lock excludes concurrent changes while SQLite's
        # backup connection reads the exact preflight snapshot (including WAL).
        backup(path, backup_path)
        require_stopped(path)
        now = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        for publication_id in unsent_ids:
            db.execute("DELETE FROM initial_publication_daily_slots WHERE limit_publication_id = ?",
                       (publication_id,))
            db.execute("UPDATE limit_pending_publications SET status = 'superseded', updated_at = ?, "
                       "last_error_category = 'silent_legacy_retirement' WHERE id = ?",
                       (now, publication_id))
        for admission_id in admission_ids:
            db.execute("UPDATE limit_admission_requests SET status = 'superseded', updated_at = ? WHERE id = ?",
                       (now, admission_id))
        released = db.execute(f"DELETE FROM destination_trade_locks WHERE signal_id IN ({IDS})",
                              tuple(TARGETS)).rowcount
        changed = db.execute(f"UPDATE signals SET status = 'cancelled', cancelled_at = ?, updated_at = ? "
                             f"WHERE id IN ({IDS}) AND status = 'entry_triggered'",
                             (now, now, *TARGETS)).rowcount
        require(changed == len(TARGETS) and released == 38, "unexpected mutation counts")
        completed, after, preserved_after, _, _ = inspect(db)
        require(completed and preserved_after == preserved, "postflight verification failed")
        require_stopped(path)
        db.commit()
        return {"result": "retired", "backup": str(backup_path), "trade_locks_released": released,
                "before": before, "after": after}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path, help="absolute or relative path to an existing local SQLite file")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--apply", action="store_true")
    modes.add_argument("--backup-only", action="store_true")
    modes.add_argument("--validate-backup", action="store_true", help="validate the file given as database")
    parser.add_argument("--backup", type=Path, help="new backup file for --apply or --backup-only")
    parser.add_argument("--confirm", help=f"required for --apply: {CONFIRMATION}")
    parser.add_argument("--maintenance-window", action="store_true",
                        help="attest that the bot and every writer are stopped and cannot restart")
    args = parser.parse_args(argv)
    if args.apply and (args.confirm != CONFIRMATION or not args.maintenance_window):
        parser.error("--apply requires the exact --confirm value and --maintenance-window")
    if (args.apply or args.backup_only) != (args.backup is not None):
        parser.error("--backup is required for --apply and --backup-only")
    try:
        path = args.database.resolve(strict=True)
        require(stat.S_ISREG(path.stat().st_mode), "database must be a regular file")
        if args.validate_backup:
            validate_backup(path)
            outcome = {"result": "backup_valid"}
        elif args.backup_only:
            backup(path, args.backup.resolve())
            outcome = {"result": "backup_valid", "backup": str(args.backup.resolve())}
        else:
            outcome = retire(path, backup_path=args.backup.resolve() if args.apply else None)
    except RetirementRefused as exc:
        print(f"Refused: {exc}", file=sys.stderr)
        return 1
    except (IndexError, KeyError):
        print("Refused: unexpected SQLite columns.", file=sys.stderr)
        return 1
    except (OSError, sqlite3.Error) as exc:
        error = getattr(exc, "sqlite_errorname", type(exc).__name__)
        print(f"Refused: {error}. Inspect paths, permissions, schema and concurrent writers.", file=sys.stderr)
        return 1
    print(json.dumps(outcome, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
