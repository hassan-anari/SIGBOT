# Phase 6 Manual Acceptance Checklist

Phase 6 is complete after the Phase 6C PR. Use disposable SQLite, fake or staging Telegram, fake exchanges, controlled templates/fonts/logos, and no real credentials for rehearsal unless a production operator explicitly authorizes a live run.

## Checklist

- Initial publication: create a Composite Signal, confirm the initial image/caption, and inspect `initial_publication_requests` and `destination_publications` for one sent row with a positive Telegram message ID.
- TP1/TP2 Reply Chain: trigger TP1, approve it, verify destination updates reply to the initial message; trigger TP2 and verify it replies to the sent TP1 where TP1 was sent.
- SL: trigger SL before TP1 and verify Loss state, negative theoretical ROI, Signalist notification, and update publication request.
- Risk Free: after an eligible TP, explicitly activate Risk Free, verify Effective Stop becomes Entry, then sample an Entry hit and verify one `RISK_FREE_HIT` closes the remaining slice with exact zero ROI. Verify no new automatic Breakeven prompt is created.
- Reports: as Main Admin, request all-Signalists and individual reports for today, last 7 days, last 30 days, and custom dates.
- Custom dates: enter invalid and reversed Gregorian `YYYY-MM-DD` ranges and verify controlled Persian rejection; enter a valid range and verify Tehran half-open UTC boundaries.
- Partial outage: fail one price API, verify one `outage_incidents` row, one immediate `admin_alert_outbox` row, and continued Composite validation with two fresh agreeing sources.
- Repeat after one minute: advance the deterministic scheduler by 60 seconds and verify exactly one repeat alert for the open incident, with no catch-up burst.
- Recovery: restore usable Healthy or Degraded data and verify one recovery alert and closed incident.
- Total price outage: fail all four exchange price services and verify one incident per price service, no false TP/SL/Risk-Free events, and live workers remain supervised. Shared eligibility remains the three-exchange Ourbit/Toobit/LBank core.
- Catalog outage: fail one core catalog API and verify its incident does not corrupt price-service health or monitoring of already-persisted Signals.
- WEEX optional recovery: fail `weex_catalog` while the three core catalogs remain healthy; verify Shared Search remains available, the runtime retries autonomously, one recovery closes the incident, and bulk WEEX MARK ticks resume without restart or user interaction.
- WEEX enrichment: create a core Shared Symbol also listed by WEEX and verify a new Composite stores four mappings/prices, uses the four-source policy, and falls back to its persisted core mappings during a WEEX price outage.
- WEEX Single Exchange: create and monitor Market and Limit WEEX-only Signals, verify `WEEX_ONLY`, exact native mapping, compatible Destination selection, and `weex_prices` health.
- Restart/no-duplicate checks: restart workers and verify no duplicate events, Signalist notifications, destination updates, requests, incidents, initial alerts, repeat slots, or recovery alerts.
- Database rows to inspect: `signals`, `signal_monitor_states`, `signal_events`, `signal_update_candidates`, `update_publication_requests`, `destination_update_publications`, `service_health_states`, `outage_incidents`, and `admin_alert_outbox`.
