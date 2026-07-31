from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal, InvalidOperation
from pathlib import Path

from chainura.environment import load_project_environment
from logging_config import get_logger, setup_logging
from market import MarketProbe

LOGGER = get_logger("main")


def percent(value: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return parsed


async def run_symbols(output: Path, max_spread: Decimal) -> None:
    probe = MarketProbe(max_spread_percent=max_spread)
    await probe.discover()
    await probe.fetch_once()
    probe.export_symbols(output)

    analysis = probe.analyze_prices()
    LOGGER.info(
        "symbols completed common=%s ready=%s healthy=%s degraded=%s "
        "unresolved=%s insufficient=%s missing=%s stale=%s output=%s",
        len(probe.common_symbols),
        len(analysis["ready"]),
        len(analysis["healthy"]),
        len(analysis["degraded"]),
        len(analysis["unresolved"]),
        len(analysis["insufficient"]),
        len(analysis["missing"]),
        len(analysis["stale"]),
        output.resolve(),
    )
    print("Instrument counts:")
    for exchange, rows in probe.instruments.items():
        print(f"  {exchange}: {len(rows)}")
    print(f"  lbank raw: {probe.lbank.raw_instrument_count}")
    print(f"  lbank ignored numeric-only: {len(probe.lbank.ignored_numeric_symbols)}")
    print(f"  lbank preserved numeric: {probe.lbank.preserved_numeric_symbols}")
    print(f"Common symbols: {len(probe.common_symbols)}")
    print(f"Reference-ready symbols: {len(analysis['ready'])}")
    print(f"Healthy symbols: {len(analysis['healthy'])}")
    print(f"Degraded symbols: {len(analysis['degraded'])}")
    print(f"Unresolved symbols: {len(analysis['unresolved'])}")
    print(f"Insufficient symbols: {len(analysis['insufficient'])}")
    print(f"Missing-price symbols: {len(analysis['missing'])}")
    print(f"Stale-price symbols: {len(analysis['stale'])}")
    print(
        "Exchange health: "
        + ", ".join(
            f"{exchange}={probe.health[exchange].status.value}"
            for exchange in ("ourbit", "toobit", "lbank")
        )
    )
    print(f"Files written to: {output.resolve()}")


async def run_watch(seconds: int | None, max_spread: Decimal) -> None:
    probe = MarketProbe(poll_seconds=5.0, max_spread_percent=max_spread)
    await probe.discover()
    LOGGER.info("watch initialized common_symbols=%s poll_seconds=5", len(probe.common_symbols))
    print(f"Common symbols: {len(probe.common_symbols)}")
    print("Ourbit: REST/5s | Toobit: REST/5s | LBank: REST/5s")
    await probe.watch(seconds=seconds)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Simple REST market-data stability probe")
    parser.add_argument(
        "--max-spread",
        type=percent,
        default=Decimal("5"),
        help="Maximum allowed agreeing-source spread percent (default: 5)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    symbols = subparsers.add_parser(
        "symbols",
        help="Build, validate, and calculate reference prices for common futures symbols",
    )
    symbols.add_argument("--output", type=Path, default=Path("output"))

    watch = subparsers.add_parser("watch", help="Poll all three exchanges every 5 seconds")
    watch.add_argument(
        "--seconds",
        type=int,
        default=None,
        help="Stop after N seconds; omit to run until Ctrl+C",
    )
    return parser


def main() -> None:
    load_project_environment()
    args = build_parser().parse_args()
    log_path = setup_logging()
    LOGGER.info(
        "application started command=%s max_spread=%s log_file=%s",
        args.command,
        args.max_spread,
        log_path,
    )

    try:
        if args.command == "symbols":
            asyncio.run(run_symbols(args.output, args.max_spread))
        else:
            asyncio.run(run_watch(args.seconds, args.max_spread))
    except KeyboardInterrupt:
        LOGGER.info("application stopped by user")
        print("\nStopped.")
    except Exception:
        LOGGER.exception("fatal application error")
        raise
    finally:
        LOGGER.info("application finished command=%s", args.command)


if __name__ == "__main__":
    main()
