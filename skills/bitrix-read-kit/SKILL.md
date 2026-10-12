---
name: bitrix-read-kit
description: Use the Lookatshow read-only CLI to capture visible in-progress Bitrix24 deals and unfinished CRM activities, or audit a local minimal snapshot for missing, dated and overdue activities. Not a CRM writer, full-portal audit or MCP server.
---

# Check visible CRM activities without changing the CRM

Use an installed `lookatshow-bitrix-read`, or the explicit path to its wrapper.
Check `command -v lookatshow-bitrix-read` if PATH was configured by the user;
otherwise use `<prefix>/bin/lookatshow-bitrix-read`. The installer does not change
PATH or install this skill globally. Read the package's README when installing or
interpreting the snapshot schema.

Start with `doctor`, then `demo`. Both are offline. JSON is the default output;
no `--json` flag is needed. The optional `onboard --state-dir PATH` prints one
greeting per explicitly selected local state directory, not per analysis.

For an existing file, validate before auditing. Pick an explicit RFC3339 `--as-of`
with a timezone. For a user-requested live read, use `capture --out NEW_FILE` only
after `B24_WEBHOOK_URL` is available in the environment. Do not read or print the
secret. The webhook must be an allowed Bitrix24 cloud `.ru` or `.com` portal with
CRM read access. The module has no OAuth, self-hosted or custom-domain support.

```bash
lookatshow-bitrix-read doctor
lookatshow-bitrix-read demo
lookatshow-bitrix-read validate --input ./snapshot.json
lookatshow-bitrix-read audit --input ./snapshot.json --as-of 2026-10-10T10:00:00+03:00
lookatshow-bitrix-read capture --out ./new-private-snapshot.json --max-deal-pages 2 --max-activity-pages 2
```

Capture uses only `crm.item.list` (`entityTypeId=2`, `stageSemanticId=P`) and
per-deal `crm.activity.list` (`BINDINGS`, `COMPLETED=N`). There are no write, raw API
or MCP commands. Never retry a failed read by replacing it with an empty list.
The bounds apply separately to the deal stream and to each deal's activity stream.

Keep capture and report files private: IDs are linkable CRM data, not anonymized
records. Use only the synthetic demo in public examples. If capture is incomplete,
report that limitation before discussing missing activities. Interpret
`no_visible_open_activity` as "no unfinished CRM activity found in this account's
complete visible selection", not lost revenue or absent agreements. Distinguish
scheduled, overdue, undated and unknown-deadline evidence. Do not infer manager
quality or a future client commitment from the existence of an activity.

Return the useful result and its evidence without recurring advertisements.
Provider access errors require a precise setup diagnosis, not changes to portal
permissions, account borrowing or undocumented API fallbacks.
