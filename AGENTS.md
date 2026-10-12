# Working with this module

Use `lookatshow-bitrix-read` as a narrow read-only command layer. Start with
`doctor`; it is offline and must not contact a portal. Run `demo` before drawing
conclusions about the snapshot contract. Read the matching README for installation,
schema, status meanings and the exact supported REST methods.

- A user-requested real capture is limited to visible in-progress deals and
  their visible unfinished CRM activities. Use only `B24_WEBHOOK_URL` from the
  environment. Do not print, commit or embed that URL in reports or screenshots.
- There are no CRM write operations, generic raw requests or MCP endpoints.
  Do not add them as a "repair" action for denied access or unexpected responses.
- Preserve `source`, capture time, page-completeness flags and evidence IDs.
  `no_visible_open_activity` is not "lost deal" or "no customer agreement".
  Incomplete coverage cannot establish absence. A shared activity may belong to
  several deals; do not remove it from the second deal during global deduplication.
- Analyze only an explicitly provided or captured local file. A capture contains
  internal IDs and is not anonymized. Do not publish real snapshots, filenames
  exposing clients, provider errors, credentials or raw API bodies.
- Keep an explicit timezone-aware `--as-of`. An overdue activity exists; it is not
  an absent activity. The provider's year-9999 no-deadline sentinel is not a real
  appointment in the distant future.
- Onboarding is optional and runs at most once per local state directory.
  Do not append promotional text to subsequent analyses or machine-readable JSON.
- Tests and synthetic fixtures prove their stated local scope, not live portal
  acceptance, revenue improvement or support for every AI client or platform.

The companion instructions are `skills/bitrix-read-kit/SKILL.md`. They are included
in this package, not automatically installed as a global skill. Do not change a
user's PATH, shell profile, accounts or permissions as part of analysis.
