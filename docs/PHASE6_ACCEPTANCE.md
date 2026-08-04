# Phase 6 Manual Acceptance Checklist

Phase 6 is complete after the Phase 6C PR. Use disposable SQLite, fake or staging Telegram, fake exchanges, controlled templates/fonts/logos, and no real credentials for rehearsal unless a production operator explicitly authorizes a live run.

## Checklist

- Initial publication: create a Composite Signal, confirm the initial image/caption, and inspect `initial_publication_requests` and `destination_publications` for one sent row with a positive Telegram message ID.
- TP1/TP2 Reply Chain: trigger TP1, approve it, verify destination updates reply to the initial message; trigger TP2 and verify it replies to the sent TP1 where TP1 was sent.
- SL: trigger SL before TP1 and verify Loss state, negative theoretical ROI, Signalist notification, and update publication request.
- Breakeven: arm after five minutes and 1.00% favorable movement, send one prompt, accept with a fresh in-range price, and verify Neutral zero ROI.
- Reports: as Main Admin, request all-Signalists and individual reports for today, last 7 days, last 30 days, and custom dates.
- Custom dates: enter invalid and reversed Gregorian `YYYY-MM-DD` ranges and verify controlled Persian rejection; enter a valid range and verify Tehran half-open UTC boundaries.
- Partial outage: fail one price API, verify one `outage_incidents` row, one immediate `admin_alert_outbox` row, and continued Composite validation with two fresh agreeing sources.
- Repeat after one minute: advance the deterministic scheduler by 60 seconds and verify exactly one repeat alert for the open incident, with no catch-up burst.
- Recovery: restore usable Healthy or Degraded data and verify one recovery alert and closed incident.
- Total outage: fail all three price APIs and verify one incident per price service, no false TP/SL/Breakeven events, and live workers remain supervised.
- Catalog outage: fail one catalog API and verify its incident does not corrupt price-service health or monitoring of already-persisted Signals.
- Restart/no-duplicate checks: restart workers and verify no duplicate events, Signalist notifications, destination updates, requests, incidents, initial alerts, repeat slots, or recovery alerts.
- Database rows to inspect: `signals`, `signal_monitor_states`, `signal_events`, `signal_update_candidates`, `update_publication_requests`, `destination_update_publications`, `service_health_states`, `outage_incidents`, and `admin_alert_outbox`.
