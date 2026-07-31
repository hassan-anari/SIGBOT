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
