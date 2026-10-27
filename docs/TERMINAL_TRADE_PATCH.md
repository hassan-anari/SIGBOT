# Targeted trade lifecycle patch

This patch changes reporting, panel-only manual Full Close, and terminal lock
release. It does not place exchange orders or change TP/SL thresholds, entry,
leverage, destination admission, market aggregation, or Signalist privacy.

## Root causes and boundaries

- `application/reporting.py` classified closed modern positions by the sign of
  actual realized ROI, losing the earlier TP reporting policy. All existing
  reports now share a read-only projection over recorded TP evidence and slices.
- `application/monitoring.py` waited for final-TP position actions and offered
  Full Close only against TP candidates. It now closes final TP automatically,
  preserves publication approval separately, and supports audited manual closes.
- `application/publication.py`, persistence outboxes, active-trade routes, and
  runtime wiring now support independent Full Close text using existing workers.

## Reporting and the operational ledger

Valid TP evidence must match a configured TP index, threshold, ROI basis, and
exact direction/leverage ROI, and occur no later than durable terminal closure.
The highest such TP qualifies as a reporting Win regardless of button choice,
publication failure, or absent/declined approval. It supersedes a subsequent SL or
Risk Free hit **in the report only**.

Reporting ROI is:

```
actual realized partial slices + residual fraction × highest verified TP ROI
```

For a closed position, the residual is the terminal ledger slice's closed
fraction, and its actual weighted delta is removed from the reporting sum before
substitution. For an open position it is `remaining_fraction`. The ledger must
have continuous fractions and cumulative weighted ROI matching Position State.
Example: Save50 at +20, TP2 at +40, then SL at -20 gives operational realized ROI
`0.5×20 + 0.5×(-20) = 0`; report ROI is `0.5×20 + 0.5×40 = 30` and Win.
No ledger value, historical event, or Signal row is rewritten for reporting.

Manual close **before TP1** retains its actual weighted close ROI and positive,
negative, or zero result classification; no TP is fabricated. With a verified TP,
manual closure uses the same target-based reporting policy as other closures.

Compatibility is conservative: without verifiable TP events the previous modern
actual-weighted/legacy-monitor fallback remains. If a TP is verified but a modern
ledger is incomplete, classification is Win while ROI retains its existing actual
value; no missing fractions are invented. Legacy rows without Position State and
with valid TP evidence use that recorded TP ROI. Legacy monitor-only rows remain
readable using their existing result/ROI. Cancelled trades remain excluded from
performance; report date windows, private scopes, and root-admission cohorts are
unchanged.

## Manual close and delivery

Only eligible ACTIVE trades with modern open Position State and PRE_TP1 or
POST_TP1 monitoring expose Full Close, next to Comment in the detail panel. Market
and explicitly activated Limit trades qualify. The owner confirms trade identity
using an ephemeral confirmation token and the position version. A fresh batch is
then fetched outside any SQLite transaction through the established persisted
source mappings and reference rules. Invalid/unavailable prices leave state and
locks unchanged. A monitor CAS rejects intervening TP/SL/cancellation/action races;
the existing Decimal FULL_CLOSE reducer closes exactly the remaining fraction.

A distinct `manual_close` event stores the captured reference, exact ROI, source
prices, and observation time. Its FULL_CLOSE ledger action identifies the owned
Signal. Position closure, terminal monitor state, owned lock deletion, and eligible
text delivery intents commit together. Undecided earlier TP actions become stale;
already approved publication history is preserved.

Independent text uses the existing position-action outbox, configurable Full Close
template precedence, active destination/assignment/owner checks, and latest
**definitely sent** destination-specific parent, including Limit Pending roots.
No confirmed root means no delivery intent. Earlier approved/in-flight updates
and ambiguous chain deliveries block the close text until safely resolved; no
message ID or TP event is invented. Rate limits retry under existing attempt
limits; interrupted/ambiguous sends become delivery_unknown and are never blindly
resent. Delivery cannot reopen accounting or reacquire locks. Historical TP-bound
Full Close/Risk Free/Save50 follow-ups retain their existing image dependency.

## Automatic reconciliation: selection and safety

Reconciliation runs at startup and in existing monitoring/notification worker
cycles, including when market monitoring is disabled. It is application behavior,
not a cleanup script; do not run a separate manual Production data repair.

A stuck trade is selected only when Signal is ACTIVE, monitor is
AWAITING_FINAL_TP_ACTION, and modern Position State is still open. It must have:

- A configured final target and matching highest/next monitor indices.
- A genuine final TP event matching final threshold, ROI basis, exact leveraged
  ROI, source type, favorable observed price, and valid recorded source evidence.
- No competing terminal event, existing final-event action, or terminal ledger
  slice, and a continuous ledger matching the current remaining fraction and ROI.
- A consistent final publication candidate, if one exists. Pending, approved,
  declined, and inactive-owner histories are preserved rather than guessed away.

Within one short transaction a monitor/version CAS precedes remaining-volume
FINAL_TP_CLOSE accounting, terminal monitoring, and owned-lock deletion. The new
ledger row references the genuine event with **no candidate ID**; publication
approval is a separate operation and still creates at most one update request.
Pending final notifications use publication-only controls after closure.

Missing/contradictory evidence is a no-op: log
`final TP reconciliation skipped` with Signal ID and a sanitized category, retain
its open position and locks, and leave it for independent review. Successful
reconciliation logs Signal ID, event ID, and position version; the ledger is the
durable audit trail. Repeated startup/cycles cannot close an already closed
position again. A failed transaction rolls back its accounting and lock changes.

Stale locks are also removed for durable cancellation, a closed modern Position
State, or a historical terminal monitor without a modern position. A terminal
monitor contradicting an open modern position is ambiguous and retains occupancy.
Genuinely open intermediate TPs keep locks, including Risk Free/Save50 positions.
Admission still enforces one `(destination_id, canonical_symbol)` across owners.

## Migration and rollout review

Migration `20261026_01` adds `manual_close` to the event-kind constraint and a
unique per-Signal manual-close index. It makes candidate/image references nullable
in the existing action outbox, constrained to both null (independent) or both
present (historical follow-up). It changes no existing row values and retains all
foreign keys and uniqueness protections. SQLite batch migration uses the existing
foreign-key suspension/validation runner to preserve referenced child records.

Empty upgrade/downgrade/reupgrade is supported. Downgrade refuses when independent
close evidence exists instead of deleting financial history or fabricating TP
references. Application rollback after manual-close writes therefore needs an
independently reviewed forward-compatible plan. Back up and apply migrations via
the normal reviewed release procedure; this development task does not deploy or
connect to Production.
