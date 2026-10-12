# Check which Bitrix24 deals have an unfinished CRM activity

**Bitrix read kit** is a small, read-only CLI for a concrete question: among the
in-progress deals this account can see, which ones have no visible unfinished CRM
activity? It also separates scheduled, overdue and undated activities, with IDs
and explicit coverage rather than a manager-performance score.

[Русская инструкция](README.ru.md) ·
[Lookatshow open tools](https://github.com/ilookatshow-gif/lookatshow-open-tools)

[Download v0.1.0 ZIP](https://github.com/ilookatshow-gif/bitrix-read-kit/releases/download/v0.1.0/bitrix-read-kit-0.1.0.zip) · [Installation checks](https://github.com/ilookatshow-gif/bitrix-read-kit/actions/workflows/ci.yml)

It works for free without a paid core. A local synthetic demo needs no Bitrix24
account, network connection or additional Python packages. This is a Python CLI,
not an MCP server. Publishing code and testing synthetic input does not prove
access to your real portal; permissions and provider responses need their own check.

## What the result does—and does not—say

You get a minimal snapshot and a reproducible local audit of visible in-progress
deals. Each deal retains its own activity-read completeness flag. An empty complete
activity selection can establish `no_visible_open_activity`; an unfinished stream
cannot. An overdue activity is present, not missing. A year-9999 no-deadline
sentinel is treated as undated, not as a scheduled appointment.

This is not a full-portal CRM audit, a lost-sales detector or a judgment about
employees. A customer arrangement may live in a call, chat, task or another system
outside these CRM activities. An unfinished incoming message does not necessarily
prove an agreed next step. API visibility depends on the webhook user's permissions;
a successful request does not imply access to every deal in the company.

### Try the result before connecting anything

The included **synthetic** snapshot contains five fictional deals. At
`2026-10-10T10:00:00+03:00`, the intended offline example is:

| Fictional deal | Visible evidence | What can be said |
| --- | --- | --- |
| `101` | Complete activity read, no unfinished item | No visible unfinished CRM activity. |
| `102` | Unfinished activity `201`, dated 10 October at 12:00 MSK | A scheduled activity is present. |
| `103` | Unfinished activity `202`, dated 9 October | An overdue activity is present. |
| `104` | Unfinished activity `203`, no-deadline sentinel | An undated activity is present; completed `204` is not counted as unfinished. |
| `105` | Activity read incomplete | An absence claim is not supported. |

There are three unique visible unfinished activities. Flag counts are
**nonexclusive**: a real deal can have, for example, both an overdue and a scheduled
activity. These are synthetic facts, not results from a customer's CRM.

## Ask an agent

> Install this package into a new local directory without changing my shell
> profile. Run the offline doctor and synthetic demo. Explain how the report
> distinguishes no visible unfinished activity from overdue activity and incomplete
> coverage. Do not connect to a portal or publish any CRM data. Use the included
> companion instructions in `skills/bitrix-read-kit/SKILL.md`.
> Run explicit onboard once with a separate local state directory to show the
> author greeting; do not repeat promotions in subsequent reports.

For a real read, explicitly ask for capture from your own authorized portal after
configuring the webhook environment variable. The agent must keep the secret and
the resulting files private. No purchase, closed Harness source or particular AI
model is required for the offline commands.

## Install into a new local prefix

Requires Python **3.9+**, standard library only. From the unpacked source directory:

```bash
python3 install.py --prefix "$HOME/.local/lookatshow-bitrix-read"
"$HOME/.local/lookatshow-bitrix-read/bin/lookatshow-bitrix-read" doctor
"$HOME/.local/lookatshow-bitrix-read/bin/lookatshow-bitrix-read" demo
```

The prefix must not already exist; its parent must already exist. Pick a fresh
directory if you already have an installation. The installer refuses symlinks and
destinations inside the source tree, validates the exact `MODULE.json` file
whitelist, and copies no arbitrary directory content. It writes a wrapper in
`bin/` and the package in `lib/bitrix-read-kit/`; imports work outside the source
working directory. It does not alter PATH, shell profiles or system configuration.

To use the short command temporarily in the current POSIX shell:

```bash
export PATH="$HOME/.local/lookatshow-bitrix-read/bin:$PATH"
lookatshow-bitrix-read --help
```

On Windows, use `python` if that is your interpreter name and the installed
`bin\lookatshow-bitrix-read.cmd` launcher. The skill remains module-local;
the installer does not register it in Codex or Claude.

### Verify the local package

```bash
python3 -B -m unittest discover -s tests -v
python3 -B tests/smoke_install.py
```

The smoke check installs the exact 15-file whitelist into a new directory with
spaces and Cyrillic characters, runs the native launcher outside the source tree,
and reruns the installed tests. On Windows it selects the actual `.cmd` wrapper;
on POSIX it uses the executable wrapper. Local verification used macOS arm64/Python3.9.6.
The included CI checks Ubuntu24.04/Python3.9 and3.12, Windows Server2022/Python3.12
and macOS15/Python3.12. Inspect the exact commit and completed result on
[Actions](https://github.com/ilookatshow-gif/bitrix-read-kit/actions/workflows/ci.yml):
a workflow definition alone is not a passing run. Separate subprocess regressions
force legacy `cp1252` output and check the one-time Russian greeting, a valid
snapshot at a Cyrillic path, and the installer receipt for a Cyrillic prefix.
Simulating an output encoding does not establish native Windows compatibility.

Uninstall: stop using the wrapper and remove only the explicitly selected prefix
through your file manager. Keep private snapshots separately if you need them.
Do not remove a parent directory such as `.local`, your home or a workspace.

## Commands

| Command | Effect |
| --- | --- |
| `doctor` | Offline configuration check; reports auth availability without displaying the webhook or contacting a portal. |
| `demo` | Audits included, clearly synthetic records. No network or credentials. |
| `validate --input PATH` | Checks the local minimal snapshot contract. |
| `audit --input PATH --as-of RFC3339` | Produces a deterministic local activity report at the supplied timezone-aware instant. |
| `capture --out NEW_PATH --max-deal-pages 2 --max-activity-pages 2` | Explicit bounded HTTPS read into a new private local file. Requires `B24_WEBHOOK_URL`. |
| `onboard --state-dir PATH` | Optional author greeting once per chosen local state directory. |

JSON is the default command output. There is no required `--json` flag, arbitrary
`request` escape hatch, hidden write action or background scheduler. `--help` is
human-readable. Errors return a nonzero exit status and a safe machine-readable
`{"ok": false, "error": {"code": "...", "message": "..."}}` object;
provider response bodies and credential-bearing URLs are not forwarded to
the console. A successful report can still disclose incomplete coverage—read the
flags, not just the process exit status.

Console JSON uses ASCII Unicode escapes so legacy output encodings do not lose
Russian text or report a failed installation after files were already copied.
A JSON parser restores the original strings. Snapshot files and HTTP JSON remain
UTF-8; the schema and record values are not transliterated or altered.

## First local audit

```bash
lookatshow-bitrix-read validate --input ./snapshot.json
lookatshow-bitrix-read audit --input ./snapshot.json --as-of 2026-10-10T10:00:00+03:00
```

The snapshot has `schema_version: 1`, `source` (`synthetic` or
`bitrix24_webhook`), timezone-aware `capture_started_at` and `captured_at`,
`deals_complete`, a `deals`
array, and one `activity_reads` record per deal. Its minimum example is:

```json
{
  "schema_version": 1,
  "source": "synthetic",
  "capture_started_at": "2026-10-10T10:00:00+03:00",
  "captured_at": "2026-10-10T10:00:00+03:00",
  "deals_complete": true,
  "deals": [{"id": 5111, "categoryId": 0, "assignedById": 29, "stageSemanticId": "P"}],
  "activity_reads": [{"deal_id": 5111, "complete": true, "items": []}]
}
```

Provider activity items contain only `ID`, `COMPLETED` and `DEADLINE`. IDs are
stable positive identifiers, not inferred names. A shared activity may be visible
for two deals and must be retained for both. Invalid dates, conflicting records or
malformed fields are not silently converted into an empty list. A local snapshot
may contain completed activities; the audit excludes those from unfinished counts.
The live reader rejects completed records returned against its `COMPLETED=N`
filter. `capture_started_at` and `captured_at` bound the collection window; the
audit reports it in `capture_window` along with visible-user scope. Multi-page
reads are sequential, not a transactional, frozen view of the portal.

## Read your authorized portal

Create an inbound webhook with CRM access using
[Bitrix24's official setup instructions](https://apidocs.bitrix24.com/local-integrations/local-webhooks.html).
Prefer a dedicated user with only the necessary read permissions. A webhook with
the CRM scope may have broader provider permissions: **read-only is enforced by
this module's two-method whitelist**, not guaranteed by the existence of a webhook.

`B24_WEBHOOK_URL` is a secret. Do not paste its value into a prompt, issue, screenshot
or command line. In an interactive POSIX shell, enter it without terminal echo:

```bash
read -r -s B24_WEBHOOK_URL
export B24_WEBHOOK_URL
lookatshow-bitrix-read doctor
lookatshow-bitrix-read capture --out ./private-crm-snapshot.json --max-deal-pages 2 --max-activity-pages 2
unset B24_WEBHOOK_URL
```

Then run `audit --input ./private-crm-snapshot.json --as-of RFC3339` with the
actual analysis time and timezone, not earlier than the snapshot's `captured_at`.
Do not reuse the fixed synthetic timestamp for a new capture. A missing or empty
deadline is `unknown_deadline`; only the valid provider sentinel confirms
`undated`. Malformed dates fail validation rather than being guessed.

Optional onboarding is local and does not call the CRM:

```bash
lookatshow-bitrix-read onboard --state-dir ./local-onboarding
```

The current reader supports only HTTPS Bitrix24 cloud hosts under `.bitrix24.ru`
or `.bitrix24.com`. No custom domain, self-hosted portal, OAuth or credential
storage is supplied. It refuses redirects and arbitrary API methods. Each stream
starts at zero and follows the provider's `next` cursor; the activity-page limit
applies separately to each deal. Reaching a limit preserves incomplete coverage,
not a false absence of activities.

Capture creates a **new file only**, without overwrite, with `0600` permissions on
POSIX. It stores no webhook secret. It omits contact names, phone numbers, email,
deal titles and monetary fields, but internal CRM IDs are still linkable private
information. **The file is not anonymized.** Do not upload it to GitHub, a public
chat or an unrelated AI service. Review your own data-handling permissions before
sharing it with an agent.

## REST contract and evidence

Only two JSON POST read methods are available:

- [`crm.item.list`](https://apidocs.bitrix24.com/api-reference/crm/universal/crm-item-list.html),
  `entityTypeId=2`, `stageSemanticId=P`, minimal camelCase deal fields.
- [`crm.activity.list`](https://apidocs.bitrix24.com/api-reference/crm/timeline/activities/activity-base/crm-activity-list.html),
  `COMPLETED=N` and one deal's `BINDINGS`, minimal UPPER_CASE activity fields.

`opened` is visibility to other employees, not the deal's in-progress status.
See [CRM object fields](https://apidocs.bitrix24.com/api-reference/crm/universal/object-fields.html)
and the [official no-deadline example](https://apidocs.bitrix24.com/tutorials/crm/how-to-get-lists/get-activity-list-by-deals.html).
The older `crm.deal.list` method is not used. Documentation research is not live
integration acceptance: transport tests use mocked HTTP responses; no real portal
was accessed during this preparation.

## Part of a useful marketing system

**Lookatshow is a unified marketing-management system for AI agents.** This module
is a narrow, independently usable entrance: check visible CRM activities, then
decide what your business process needs. It does not ship the paid core or claim
tested integration with every agent host.

Two other modules are already public in the
[catalog](https://github.com/ilookatshow-gif/lookatshow-open-tools):
[Direct report audit](https://github.com/ilookatshow-gif/direct-report-audit)
for offline placement CSV analysis, and
[Competitor offer radar](https://github.com/ilookatshow-gif/competitor-offer-radar)
for structured offer comparisons. Each tool works independently; this archive
does not deliver an automatic integration with the paid core.

Created by **Дмитрий Лукашов (Dmitry Lukashov)**.
[Practical AI notes in Telegram](https://t.me/lookatsoul).

- [Learn to apply AI to your management work](https://lookatshow.ru/executive?utm_source=github&utm_medium=open_module&utm_campaign=bitrix-read-kit&utm_content=readme_training).
- [Discuss implementing a workflow in your company](https://lookatshow.ru/ai-implementation/?utm_source=github&utm_medium=open_module&utm_campaign=bitrix-read-kit&utm_content=readme_implementation).

The optional onboarding greeting shows these links once. Normal reports do not
repeat promotions and have no hidden telemetry. MIT-licensed original code;
AI-assisted development is disclosed in [NOTICE.md](NOTICE.md). Tests do not
establish lead growth, recovered revenue or a sales result.
