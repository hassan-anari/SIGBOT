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
- Exchange priority is a normalized, non-empty ordered subset of Ourbit, Toobit, and LBank.
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

## Permanent Phase 5A signal and rendering rules

- Never use float for financial values; use Decimal end-to-end.
- Never expose Destination or assignment information to Signalists.
- Never perform market HTTP calls inside database transactions.
- Every awaited market/FSM operation must use matching flow and operation tokens and discard stale results.
- Entry-validation tolerance is exactly 0.05 percent unless explicitly changed later.
- The initial render uses the Signalist-entered Entry and the Reference Price as display price; initial PnL is always 0.00 percent.
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

- TP/SL detection is automatic; SL and Breakeven are disabled permanently after TP1.
- Event threshold, observed reference price, exact ROI, and event-time source prices are distinct durable facts.
- Breakeven is a contextual manual prompt after five minutes, 1.00% favorable movement, a 0.10% execution zone, 0.20% rearm distance, and 15-minute cooldown.
- Signalist approval records Phase 6B publication intent only; Phase 6A never publishes updates to destinations.
- Monitoring refreshes exchanges in batches and direct notification delivery uses the SQLite outbox.

## Phase 6B implementation note

Approved update publication is now a durable SQLite outbox separate from Phase 5 initial publication. Keep update images on the same template/layout pipeline, keep update captions centralized, and keep Reply Chain parent resolution immediately before Telegram send.
