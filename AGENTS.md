# Agent Instructions

## Source of truth

The complete and approved product requirements are located at:

- `docs/PRODUCT_SPEC.md`

Read that document before proposing or implementing any product-related change.

Do not introduce features, roles, workflows, states, business rules, or assumptions that are not explicitly defined in the product specification.

When a requirement is ambiguous or missing, report the ambiguity and request clarification instead of inventing a solution.

## Current implementation

Before changing the code:

1. Inspect the complete repository.
2. Understand the existing market-data implementation.
3. Review the tests and runtime commands.
4. Identify the exact files affected by the requested task.
5. Present a plan before implementation when requested.

## Development rules

- Preserve existing working behavior unless the task explicitly changes it.
- Keep Telegram transport separate from application and business logic.
- Development uses Telegram `getUpdates`; production will later use Webhook.
- Do not hard-code secrets, tokens, chat IDs, or environment-specific values.
- Do not log secrets or sensitive Telegram payloads.
- Add or update tests for every behavioral change.
- Run the relevant tests before reporting completion.
- Report commands executed, test results, changed files, and remaining risks.
- Do not perform unrelated refactoring.
- Do not expand the product scope without explicit approval.

## Permanent Telegram and storage rules

- Every Telegram user-facing value is Persian and comes from the centralized
  `chainura/telegram/presentation/texts.py` catalog. Do not add a language selector.
- Reply Keyboards are only for persistent top-level navigation. Inline Keyboards
  are for contextual actions, and menus must not advertise unimplemented features.
- Redis stores ephemeral runtime state only. SQLite is the source of truth for
  permanent product data and Signalist status.
- Keep Telegram transport and polling lifecycle isolated from application and
  business logic so a future Webhook adapter can reuse the same dispatcher and routers.
- Never log tokens, credentials, connection URLs, or sensitive Telegram payloads.

## SQLite and environment rules

- SQLite is the current permanent source of truth; Redis remains ephemeral only.
- The SQLite database file must be on local persistent disk, regularly backed up,
  and must never be placed on NFS, SMB, or another network filesystem.
- Keep database transactions short and never perform Telegram, exchange, Redis,
  or other network calls inside a database transaction.
- Use portable SQLAlchemy constructs. Do not add PostgreSQL-specific types or SQL
  unless PostgreSQL is explicitly re-approved.
- Signalist status transitions use an atomic compare-and-swap conditional update.
- Load `.env` only at executable entry points. Real process environment variables
  override `.env` values.
- Secrets and SQLite runtime artifacts must never be committed or logged.

## Phase 4 destination rules

- Destinations have only active and inactive states; deletion and archive are prohibited.
- Destination Chat ID and internal ID are immutable.
- Exchange priority is a normalized, non-empty ordered subset of Ourbit, Toobit,
  LBank, and WEEX. Existing priorities are never automatically extended with WEEX;
  Main Admin must explicitly edit them.
- Signalists never receive Destination or assignment information.
- Admin-management handlers require `MAIN_ADMIN`, not generic authorization.
- The existing Signalist service remains the sole owner of Signalist transitions.
- FSM storage contains temporary form state only; permanent management data stays in SQLite.
- Persist timestamps in aware UTC; localization belongs only to presentation. Phase 4
  Telegram dates use the Jalali calendar and Tehran local time.
- Internal database IDs never appear in Telegram-visible presentation.
- Signalist usernames are retained as nullable compatibility data but are neither
  collected nor displayed.
- Destination title and type are derived from Telegram, and registration requires
  the existing Bot instance to confirm administrator status.
- Telegram inspection occurs outside database transactions, and automated tests
  must never call live Telegram.

## Persian Telegram bidirectional-text rules

- Persian Telegram messages are RTL. All ASCII identifiers, symbols, prices,
  percentages, leverage values, Entry values, TP/SL values, and other LTR numeric
  data must use explicit Unicode directional isolation.
- Telegram IDs and Chat IDs must be displayed on a separate line inside `<code>`.
  Identifiers must remain ASCII and copyable.
- Never rely on Telegram's automatic BiDi rendering for mixed Persian and numeric
  content. This applies in Phase 5 to symbol names, Entry, stop loss, take-profit
  levels, leverage, prices, percentages, Telegram IDs, and Chat IDs; it does not
  authorize implementing those Phase 5 fields early.

## Permanent Market, Limit, and rendering rules

- Never use float for financial values; use Decimal end-to-end.
- Never expose Destination or assignment information to Signalists.
- Never perform market HTTP calls inside database transactions.
- Every awaited market/FSM operation must use matching flow and operation tokens and discard stale results.
- Market P0 is captured at Symbol selection and final creation captures fresh P1.
  P0-to-P1 drift must not exceed exactly 0.70 percent. The accepted creation
  reference is the authoritative Market Entry, and Stage Two is `PENDING_DETAILS`.
- Limit Entry is the requested Decimal price. LONG must be below, and SHORT above,
  the fresh reference. Limits have no expiration and transition durably through
  `PENDING_ENTRY` to `ENTRY_TRIGGERED`, then require explicit Signalist activation
  before becoming `ACTIVE` or receiving TP/SL monitoring.
- The initial render uses the authoritative Entry and the Reference Price as display price; initial PnL is always 0.00 percent.
- TP and SL belong to the future caption, not the rendered card layout.
- Renderer clock and elapsed values are supplied externally; renderers never generate current time.
- Do not guess or commit production coordinates, fonts, or final layout details.

## Permanent Phase 5C publication rules

- New Signals and their initial-publication request commit atomically; legacy Signals are never backfilled.
- SQLite is the durable outbox and daily-capacity source of truth. Tehran dates define daily slots.
- Persist `sending` before Telegram; ambiguous delivery becomes `delivery_unknown` and is never resent automatically.
- Initial captions are centralized, Persian, HTML-safe, BiDi-isolated, and include `#پیامک` only for private destinations.
- Main Admin templates are PNG documents; generated initial images are cleaned on all outcomes.
- Drain publication and InitialImageService work before unregistering production renderers.

## Permanent Phase 6A monitoring rules

- TP/SL detection is automatic. Current Signals do not create automatic Breakeven
  prompts or events. Historical `BREAKEVEN`, `COMPLETED_BREAKEVEN`, prompt routes,
  and report rows remain readable compatibility data only.
- Event threshold, observed reference price, exact ROI, and event-time source prices are distinct durable facts.
- Explicit Risk Free activation moves the effective stop to Entry. A later Entry
  hit is `RISK_FREE_HIT` and closes the remaining slice at exact zero ROI.
- Signalist approval records Phase 6B publication intent only; Phase 6A never publishes updates to destinations.
- Monitoring refreshes exchanges in batches and direct notification delivery uses the SQLite outbox.

## Phase 6B implementation note

Approved update publication is now a durable SQLite outbox separate from Phase 5 initial publication. Keep update images on the same template/layout pipeline, keep update captions centralized, and keep Reply Chain parent resolution immediately before Telegram send.

## Phase 6C permanent rules

Main Admin reports are on-demand only, use Asia/Tehran date semantics, and calculate Win Rate from wins and losses only. Durable API health tracks eight services independently (price/catalog for Ourbit, Toobit, LBank, and WEEX). Only `down` opens one incident per service; repeat alerts use the fixed 60-second cadence and must not send catch-up bursts. Phase 6 is complete after the Phase 6C PR; do not add scheduled reports, historical outage reconstruction, Webhook migration, PostgreSQL, or external queues.

## Permanent Composite and WEEX rules

- Shared catalog eligibility is always the Ourbit, Toobit, and LBank core
  intersection. WEEX is optional enrichment and cannot remove a core Shared Symbol.
- Historical three-mapping Composite Signals remain unchanged. New Composite
  Signals persist the three core mappings plus WEEX when available, so they have
  either three or four mappings.
- With two fresh valid prices, use their arithmetic mean only when their spread is
  within the configured threshold. With three, use the median with the established
  agreement/outlier safeguards. With four agreeing prices, average the two sorted
  middle prices; with exactly one unique outlier, discard it and use the remaining
  three-price median; without a unique agreeing quorum, the reference is unresolved.
- WEEX uses only the public Contract API V3. Normal polling makes one bulk
  `/capi/v3/market/premiumIndex` call and uses `markPrice`; there is no per-Signal
  WEEX request, WebSocket feed, or candle reconstruction.
- Single Exchange WEEX persists `PriceSourceType.WEEX_ONLY`. A WEEX catalog outage
  does not disable Shared core operation, and missing optional catalog recovery is
  autonomous and idempotent.
- The first-pass WEEX renderer schema is production-functional; exact coordinates
  and typography are tuned through Renderer Lab, not business logic.

## Permanent Phase 7 private reporting rules

- Active Signalist personal reports derive ownership only from the authenticated Actor and reveal no Destination, assignment, publication, delivery, capacity, or selected-Destination-exchange facts.
- Main Admin Destination reports live inside the selected Destination management flow and never reveal Signalist identity.
- Destination membership requires a definitely sent Market Initial or Limit Pending root and uses root `sent_at`; Limit activation never adds a second row.
- All reports share weighted Decimal position accounting, with monitor ROI only as a legacy fallback, and remain synchronous SQLite reads without network calls or workers.
