"""Stable public identifiers for persisted trades."""


def format_trade_id(signal_id: int) -> str:
    """Return the public ASCII Trade ID derived from a persisted signal ID."""
    if isinstance(signal_id, bool) or not isinstance(signal_id, int) or signal_id < 1:
        raise ValueError("signal_id must be a positive integer")
    return f"TR-{signal_id:06d}"
