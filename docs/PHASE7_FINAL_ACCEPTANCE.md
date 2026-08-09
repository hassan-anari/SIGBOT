# Phase 7 Final Acceptance Checklist

Use this checklist for staging/manual handoff after automated acceptance passes. Prefer a disposable or staging SQLite database on local persistent disk; do not use a production database for destructive test setup.

## Environment

- [ ] Redis 7 is available.
- [ ] The bot token is configured through the environment.
- [ ] Ourbit, Toobit, and LBank APIs are reachable, or controlled staging/fake equivalents are configured.
- [ ] Approved PNG template assets and required fonts/logos are present.
- [ ] At least one Main Admin, one active Signalist, one active destination, and an assignment exist.
- [ ] SQLite is on local disk and a backup has been taken.

Never put real tokens, credentials, connection URLs, or sensitive Telegram payloads in screenshots, logs, or commits.

## Signalist creation and continuation

1. Tap **Create Signal** and select a shared Symbol. Record the reference shown at selection time; this is the immutable Entry captured by the system (the UI need not expose internal P0/P1 names).
2. Select Long or Short, then select leverage.
3. Observe the fresh confirmation price. At drift `<= 0.70%`, confirm that exactly one durable Pending Details Signal is created, Entry remains the selection-time price, and the fresh price is retained as the initial snapshot. At drift `> 0.70%`, confirm that no Signal or publication row is created.
4. Verify an Initial **Photo** is published and its Telegram message ID is stored.
5. Verify the Signalist receives the publication-completion notification and **Continue Details** action.
6. Continue from the durable Pending Signal, enter ordered TPs and SL, and finalize.
7. Verify lifecycle becomes ACTIVE, monitor stage is PRE_TP1, and Details is a **Text** reply to Initial.
8. Open **Active Signals**. Verify Pending, PRE_TP1, and POST_TP1 entries and their status buttons; completed, cancelled, and another Signalist's entries must be absent.
9. Restart the bot with a Pending Signal, then resume it from Active Signals without relying on old Redis/FSM data.

## Publication chain and exact execution

- [ ] Inspect actual Telegram reply relationships: `Initial -> Details -> TP/SL/BE -> Cancel`.
- [ ] TP is a **Photo**, SL is **Text**, Breakeven is a **Photo**, and Cancellation is **Text**.
- [ ] Force a TP overshoot, for example Entry `100`, TP `110`, observed market `111+`. The destination and Signalist-visible execution/result price must be `110`; `111+` remains audit data only.
- [ ] Force an SL overshoot, for example SL `90`, observed market `89`. The visible execution and persisted ROI basis must be `90`, and no SL image/photo is sent.
- [ ] Accept a valid Breakeven prompt. Verify execution basis equals Entry, ROI is zero, and the photo replies to the latest confirmed chain parent.
- [ ] For a legacy ACTIVE Signal with Initial but no Details row, verify monitoring still works and an event replies to Initial; do not backfill Details.

## Destination message templates

As Main Admin, exercise Initial, Details, TP, SL, BE, and Cancel message kinds (representative placeholders are sufficient):

1. Change a global template, preview it, confirm it, and verify the next publication.
2. Add a destination override and verify it takes precedence for that destination.
3. Verify another destination still uses the global override.
4. Remove the destination override and verify fallback to global.
5. Remove the global override and verify fallback to the built-in default.
6. Confirm content types never change: TP/BE remain photos; Details/SL/Cancel remain text.

## Cancellation

- [ ] Cancel a Pending Signal; it retains no TP/SL, cannot resume, and Cancellation replies to Initial if Initial was sent.
- [ ] Cancel an open ACTIVE PRE_TP1 Signal.
- [ ] Reach TP1 while still POST_TP1/open, then cancel. The TP event remains historical and Cancellation replies to the latest sent update.
- [ ] A completed trade cannot be cancelled.
- [ ] A Cancelled Signal disappears from Active Signals and remains terminal after restart.
- [ ] Stale event, continuation, and decision buttons do nothing actionable after cancellation.
- [ ] Pending/retry cancellation delivery resumes safely; SENT, FAILED, and DELIVERY_UNKNOWN rows are not resent.

## Main Admin reports

Test **Today**, **7 days**, **30 days**, and **Custom**, for **all Signalists** and an **individual Signalist**, including history pagination. Build a cohort containing Pending, open ACTIVE, Win, Loss, Breakeven, Cancelled-before-TP, and Cancelled-after-TP1.

- [ ] Counts separately show total, pending, open, cancelled, and resulted Signals.
- [ ] Pending and Cancelled Signals do not change Wins, Losses, Breakevens, Win Rate, ROI totals/averages/extremes, or final-result TP counts.
- [ ] Cancelled-after-TP retains highest TP in history but shows no theoretical ROI.
- [ ] Confirmed publication still means at least one SENT Initial publication; unpublished remains its complement against the created cohort.
- [ ] Report membership uses Signal creation time and Tehran-day UTC boundaries, not completion/cancellation/event time.
- [ ] Reports callbacks and filters route correctly for every configured Main Admin; health alerts still target `PRIMARY_MAIN_ADMIN`.

## Restart and recovery

Restart with one Pending Signal, one ACTIVE Signal, one CANCELLED Signal, and—if practical—one pending/retryable durable publication.

- [ ] Pending remains Pending, appears in Active Signals, and resumes.
- [ ] ACTIVE continues monitoring from its durable state.
- [ ] CANCELLED remains terminal, is not re-enrolled, and creates no later event/candidate/notification.
- [ ] Initial completion notification, Details, update, and cancellation pending/retry work recover according to their durable statuses.
- [ ] A cancellation row missing for a later-SENT Initial is reconciled once per destination.
- [ ] No duplicate Telegram destination messages or durable Signal, Initial, Details, Event, update, or cancellation-outbox rows appear.

## Health and alerts

- [ ] Verify partial and total market outage behavior, six independent durable service states, one incident per down service, 60-second repeat cadence, recovery alerts, and no catch-up burst.
- [ ] Verify all Main Admin access remains intact and only `PRIMARY_MAIN_ADMIN` receives health alerts.

## Database verification

Inspect preserved rows and relationships without modifying them. Useful tables include:

- `signals`, `signal_initial_prices`, `signal_take_profits`
- `initial_publication_requests`, `initial_publication_notifications`, `destination_publications`
- `signal_details_publications`
- `signal_monitor_states`, `signal_events`, `signal_event_source_prices`, `signal_update_candidates`
- `update_publication_requests`, `destination_update_publications`
- `destination_message_templates`, `signal_cancellation_publications`
- `service_health_states`, `outage_incidents`, `admin_alert_outbox`

- [ ] Upgrade a copy containing Phase 6 data to Alembic head and verify Signals, levels, monitor/event/source-price data, Initial message IDs, and update rows remain loadable.
- [ ] Upgrade a Phase 7.3 copy and verify Pending and ACTIVE Signals, message overrides, and Initial/Details/Update rows remain unchanged; no Signal becomes Cancelled automatically.
- [ ] Complete the repository's disposable downgrade/re-upgrade lifecycle check.

## Phase 7 Final Acceptance

- [ ] Signal creation
- [ ] P0/P1
- [ ] Initial publication
- [ ] Details
- [ ] Monitoring
- [ ] Exact TP/SL execution
- [ ] Templates
- [ ] Active Signals
- [ ] Cancellation
- [ ] Reports
- [ ] Restart/recovery
- [ ] Health/alerts
- [ ] Migration/DB verification

Operator: ____________________  Date/time: ____________________  Build/commit: ____________________

