# Silent retirement of six historical Limit Signals

This procedure covers 551 SKYUSDT, 556 AUSDT, 572 ARUSDT, 703 MAGMAUSDT,
798 ALPINEUSDT, and 800 LSKUSDT. The November 23, 2026 audit found all six
in `entry_triggered`, with definitely delivered Signalist notifications and
38 Symbol Trade Locks across five Signals. Signal 551 owned zero locks.

The utility accepts Alembic revisions `20261026_01` and `20261123_01`. It
preserves the Signal rows, Trigger evidence, sent Pending rows, Telegram
message IDs, and daily slots for sent announcements. It supersedes only
provably unsent Pending work and releases its unused daily slots. It refuses
unexpected lifecycle, admission, notification, or lock evidence, any existing
cancellation outbox row for these Signals, and Pending sends in a retry,
in-flight, or ambiguous state. Worker recovery has no source for outbound
work on the retired Signals.

## Inspect before the maintenance window

Run from the reviewed checkout using its Python environment. Set `DB` to the
actual absolute SQLite path on the bot host. Use a secure, persistent backup
directory outside the repository. This procedure requires local Linux with
`/proc`, the bot's service account, and an approved maintenance window.

```bash
read -r -p 'Absolute SQLite database path: ' DB
read -r -p 'Absolute restricted backup directory: ' BACKUP_DIR
test -f "$DB" && test -d "$BACKUP_DIR"
umask 077
SCRIPT=scripts/retire_legacy_limits.py
python "$SCRIPT" "$DB"
```

Expect `result: dry_run`, six `entry_triggered` rows with
`trigger_notification: sent`, zero cancellation intents, and exactly 38 locks.
The dry run opens SQLite in read-only mode. If any check reports a refusal,
stop and reconcile the difference with the audit. A clean `already_retired`
report means no apply is needed.

An independent online backup and validation can be run before the stop. The
apply step still requires a fresh, separate backup path.

```bash
INSPECTION_BACKUP="$BACKUP_DIR/sigbot-legacy-limits-inspection.sqlite"
python "$SCRIPT" "$DB" --backup-only --backup "$INSPECTION_BACKUP"
python "$SCRIPT" "$INSPECTION_BACKUP" --validate-backup
```

## Stop and establish the maintenance window

The repository entry point is `python -m chainura.telegram`. Use the actual
production supervisor to stop it gracefully and suspend any timers,
automation, or other supervisors that could start it again. Stop any other
process that can write this SQLite file. The repository does not provision or
name a production service. The commands below apply to a systemd-managed
instance and use its confirmed unit name. For another supervisor, establish
the same stop and restart boundary using the deployment's runbook.

Run these host commands as an operator authorized to manage the service.
Check the listed unit and its account before stopping. Execute the SQLite
utility as the same account that normally runs the bot.

```bash
read -r -p 'Confirmed production SIGBOT systemd unit: ' SIGBOT_UNIT
sudo systemctl show "$SIGBOT_UNIT" -p Id -p LoadState -p User -p MainPID -p ActiveState
sudo systemctl stop "$SIGBOT_UNIT"
sudo systemctl show "$SIGBOT_UNIT" -p ActiveState -p SubState -p MainPID
```

Continue once systemd reports `ActiveState=inactive` and `MainPID=0`.
Check for other runtime processes and open SQLite handles. Run the handle
check with visibility of all local service accounts, and continue only when
there are no matches or permission errors.

```bash
pgrep -af '[p]ython[^ ]* .*chainura[.]telegram'
for file in "$DB" "$DB-wal" "$DB-shm"; do
    if [ -e "$file" ]; then sudo lsof -- "$file"; fi
done
```

The utility separately checks for a same-account runtime and open SQLite
handles. It takes a SQLite writer lock with no busy wait and keeps it through
backup, preflight, modification, and postflight. The operator-maintained stop
prevents other accounts or schedulers from launching workers during this
window.

## Apply and verify before restart

Choose a fresh backup filename. The SQLite Backup API captures committed
WAL data, and the utility verifies the backup with `integrity_check` and
`foreign_key_check` before changing business state. Keep the apply report
and backup in the restricted directory as the operation record.

```bash
set -o pipefail
APPLY_BACKUP="$BACKUP_DIR/sigbot-legacy-limits-before-apply.sqlite"
python "$SCRIPT" "$DB" --apply --maintenance-window \
    --confirm RETIRE-551-556-572-703-798-800 --backup "$APPLY_BACKUP" \
    | tee "$BACKUP_DIR/sigbot-legacy-limits-apply.json"
python "$SCRIPT" "$APPLY_BACKUP" --validate-backup
python "$SCRIPT" "$APPLY_BACKUP"
python "$SCRIPT" "$DB"
```

Proceed after a successful apply showing 38 released locks and six
`cancelled` Signals. The live file's read-only report must show
`already_retired`, zero Trade Locks, zero unsent Pending rows, and zero
cancellation intents for each Signal. The backup still shows the before
state. The utility also checks that all sent Pending rows, original Telegram
IDs, daily slots for those rows, Trigger evidence, and original Signal prices
match across the transaction. A failed preflight changes no business rows.
A failure after backup rolls back the transaction. Keep the backup and review
the refusal before retrying.

## Restart and verify

Restart the same approved release through the production supervisor. Any
deployment of automatic activation or application of Alembic revision
`20261123_01` follows the separate production rollout and approval process.

```bash
sudo systemctl start "$SIGBOT_UNIT"
sudo systemctl is-active "$SIGBOT_UNIT"
sudo journalctl -u "$SIGBOT_UNIT" --since '10 minutes ago' --no-pager
python "$SCRIPT" "$DB"
```

After at least one configured publication and notification sweep, rerun the
read-only command. Confirm `already_retired`, six cancelled rows, zero
owned locks, zero cancellation intents, and no outstanding work. Review
startup and recovery health in the service logs. Report the result through
the approved operator channel. The utility has no Telegram integration.
