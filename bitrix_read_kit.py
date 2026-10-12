#!/usr/bin/env python3
"""Minimal visible-scope Bitrix activity audit. No CRM writes or telemetry."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
import sys

VERSION = "0.1.0"
MAX_BYTES = 2_000_000
MAX_DEALS = 1000
MAX_ACTIVITIES = 100_000
TIME_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})\Z")


class KitError(Exception):
    def __init__(self, code, message):
        self.code = code
        self.message = message
        super().__init__(message)


def require(condition, code, message):
    if not condition:
        raise KitError(code, message)


def parse_time(value, name="timestamp", convert_utc=True):
    require(isinstance(value, str) and bool(TIME_PATTERN.fullmatch(value)),
            "invalid_timestamp", name + " requires an ISO timestamp with timezone")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
        require(parsed.utcoffset() is not None and abs(parsed.utcoffset().total_seconds()) <= 14 * 3600,
                "invalid_timestamp", name + " has an invalid timezone offset")
        # Explicit minute check: fromisoformat can normalize +01:99.
        if value[-1] != "Z":
            require(int(value[-2:]) < 60, "invalid_timestamp", name + " has invalid timezone minutes")
        return parsed.astimezone(timezone.utc) if convert_utc else parsed
    except (ValueError, OverflowError):
        raise KitError("invalid_timestamp", name + " is not a valid calendar timestamp") from None


def utc_text(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def identifier(value, name="id", allow_zero=False):
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,18}", value):
        value = int(value)
    require(type(value) is int and (0 if allow_zero else 1) <= value <= 10**18,
            "invalid_id", name + " must be a bounded integer identifier")
    return value


def exact_keys(obj, required, name):
    require(isinstance(obj, dict) and set(obj) == set(required),
            "invalid_schema", name + " has unexpected or missing fields")


def no_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "invalid_json", "duplicate JSON keys are not accepted")
        result[key] = value
    return result


def invalid_constant(value):
    raise KitError("invalid_json", "non-finite JSON values are not accepted")


def bounded_integer(value):
    # Python 3.9 has no built-in long-integer parsing guard. Bound the literal
    # before conversion rather than spending time on a giant untrusted number.
    require(len(value.lstrip("-")) <= 19, "invalid_json", "JSON integer exceeds the digit limit")
    return int(value)


def load_json(path):
    try:
        # O_NOFOLLOW is unavailable on some hosts (notably Windows). Reject a
        # visible leaf symlink explicitly; keep O_NOFOLLOW for atomic POSIX open.
        require(not Path(path).is_symlink(), "invalid_file", "input must not be a symbolic link")
        # O_NOFOLLOW where available prevents opening a symlink to a private file.
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            require(stat.S_ISREG(info.st_mode), "invalid_file", "input must be a regular file")
            require(info.st_size <= MAX_BYTES, "input_too_large", "input exceeds the size limit")
            data = handle.read(MAX_BYTES + 1)
            require(len(data) <= MAX_BYTES, "input_too_large", "input exceeds the size limit")
        return json.loads(data.decode("utf-8"), object_pairs_hook=no_duplicate_keys,
                          parse_constant=invalid_constant, parse_int=bounded_integer)
    except (OSError, UnicodeError, ValueError, RecursionError):
        raise KitError("invalid_file", "cannot read valid bounded UTF-8 JSON from input") from None


def deadline_time(value):
    if value is None or value == "":
        return None
    parsed = parse_time(value, "activity deadline", convert_utc=False)
    # Bitrix uses 9999-12-31 for no deadline. Check the local date before UTC conversion.
    if value[:10] == "9999-12-31":
        return None
    try:
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise KitError("invalid_timestamp", "activity deadline exceeds the UTC calendar") from None


def validate_snapshot(data):
    exact_keys(data, ["schema_version", "source", "capture_started_at", "captured_at", "deals_complete", "deals", "activity_reads"], "snapshot")
    require(type(data["schema_version"]) is int and data["schema_version"] == 1,
            "invalid_schema", "unsupported snapshot schema version")
    require(data["source"] in ("synthetic", "bitrix24_webhook"), "invalid_schema", "unsupported snapshot source")
    captured = parse_time(data["captured_at"], "captured_at")
    started = parse_time(data["capture_started_at"], "capture_started_at")
    require(started <= captured, "invalid_timestamp", "capture window is reversed")
    require(type(data["deals_complete"]) is bool, "invalid_schema", "deals_complete must be boolean")
    require(isinstance(data["deals"], list) and len(data["deals"]) <= MAX_DEALS,
            "invalid_schema", "deals must be a bounded array")
    require(isinstance(data["activity_reads"], list) and len(data["activity_reads"]) == len(data["deals"]),
            "invalid_schema", "each selected deal requires exactly one activity read")
    deals, reads, activity_owners = {}, {}, {}
    for deal in data["deals"]:
        exact_keys(deal, ["id", "categoryId", "assignedById", "stageSemanticId"], "deal")
        did = identifier(deal["id"], "deal id")
        require(did not in deals, "duplicate_deal", "duplicate deal identifiers are not accepted")
        require(deal["stageSemanticId"] == "P", "invalid_schema", "snapshot must contain only open P-stage deals")
        deals[did] = {"id": did, "categoryId": identifier(deal["categoryId"], "categoryId", True),
                      "assignedById": identifier(deal["assignedById"], "assignedById", True), "stageSemanticId": "P"}
    total = 0
    for read in data["activity_reads"]:
        exact_keys(read, ["deal_id", "complete", "items"], "activity read")
        did = identifier(read["deal_id"], "activity read deal_id")
        require(did in deals and did not in reads, "invalid_schema", "activity read refers to an unexpected or repeated deal")
        require(type(read["complete"]) is bool, "invalid_schema", "activity complete must be boolean")
        require(isinstance(read["items"], list), "invalid_schema", "activity items must be an array")
        total += len(read["items"])
        require(total <= MAX_ACTIVITIES, "invalid_schema", "snapshot contains too many activities")
        activities = {}
        for item in read["items"]:
            exact_keys(item, ["ID", "COMPLETED", "DEADLINE"], "activity")
            aid = identifier(item["ID"], "activity ID")
            require(aid not in activities, "duplicate_activity", "duplicate activity within one deal is not accepted")
            require(item["COMPLETED"] in ("Y", "N"), "invalid_schema", "COMPLETED must be Y or N")
            deadline_time(item["DEADLINE"])
            activities[aid] = {"ID": aid, "COMPLETED": item["COMPLETED"], "DEADLINE": item["DEADLINE"]}
            # A shared activity can occur under multiple deal bindings; only conflicts are rejected.
            require(aid not in activity_owners or activity_owners[aid] == activities[aid],
                    "conflicting_activity", "shared activity fields conflict between deal reads")
            activity_owners[aid] = activities[aid]
        reads[did] = {"complete": read["complete"], "items": list(activities.values())}
    return {"started": started, "captured": captured, "deals": deals, "reads": reads, "source": data["source"],
            "deals_complete": data["deals_complete"]}


def audit_snapshot(data, as_of):
    clean = validate_snapshot(data)
    now = parse_time(as_of, "as_of")
    require(now >= clean["captured"], "invalid_as_of", "as_of cannot precede snapshot capture")
    rows, all_open_ids = [], set()
    counts = {"no_visible_open_activity": 0, "scheduled": 0, "overdue": 0,
              "undated": 0, "unknown_deadline": 0, "activity_coverage_incomplete": 0}
    for did in sorted(clean["deals"]):
        read = clean["reads"][did]
        open_items = [x for x in read["items"] if x["COMPLETED"] == "N"]
        all_open_ids.update(x["ID"] for x in open_items)
        overdue, scheduled, undated, unknown = [], [], [], []
        for item in open_items:
            due = deadline_time(item["DEADLINE"])
            if item["DEADLINE"] is None or item["DEADLINE"] == "":
                unknown.append(item["ID"])
            elif due is None:
                undated.append(item["ID"])
            elif due < now:
                overdue.append(item["ID"])
            else:
                scheduled.append((due, item["ID"]))
        scheduled.sort()
        flags = []
        if not read["complete"]:
            flags.append("activity_coverage_incomplete")
        elif not open_items:
            flags.append("no_visible_open_activity")
        if scheduled:
            flags.append("scheduled")
        if overdue:
            flags.append("overdue")
        if undated:
            flags.append("undated")
        if unknown:
            flags.append("unknown_deadline")
        for flag in flags:
            counts[flag] += 1
        next_value = {"activity_id": scheduled[0][1], "deadline_utc": utc_text(scheduled[0][0])} if scheduled else None
        rows.append({"deal_id": did, "category_id": clean["deals"][did]["categoryId"],
                     "assigned_by_id": clean["deals"][did]["assignedById"],
                     "activity_read_complete": read["complete"], "flags": flags,
                     "visible_open_activity_ids": sorted(x["ID"] for x in open_items),
                     "overdue_activity_ids": sorted(overdue), "undated_activity_ids": sorted(undated),
                     "unknown_deadline_activity_ids": sorted(unknown),
                     "next_scheduled_visible_activity": next_value})
    return {"ok": True, "schema_version": 1, "source": clean["source"],
            "synthetic": clean["source"] == "synthetic", "captured_at": utc_text(clean["captured"]),
            "as_of": utc_text(now), "capture_window": {"started_at": utc_text(clean["started"]), "finished_at": utc_text(clean["captured"])},
            "coverage": {"scope": "records_visible_to_webhook_user",
              "selected_deal_pages_complete": clean["deals_complete"],
              "all_selected_activity_pages_complete": all(x["complete"] for x in clean["reads"].values()),
              "snapshot_is_atomic": False, "portal_wide_completeness_proven": False},
            "summary": {"visible_open_deals": len(rows), "unique_visible_open_activities": len(all_open_ids),
                        "deal_flag_counts_nonexclusive": counts},
            "deals": rows,
            "limitations": ["No visible activity is not proof of no agreement, lost revenue or manager failure.",
                            "Incomplete pages cannot support an absence claim; permission-limited records may be missing.",
                            "Deal and activity reads are sequential, not a transactionally consistent portal snapshot.",
                            "This report contains internal CRM IDs; it is not anonymized or safe for public sharing."]}


def capture_snapshot(client, max_deal_pages=2, max_activity_pages=2, captured_at=None):
    require(type(max_deal_pages) is int and 1 <= max_deal_pages <= 20,
            "invalid_limit", "max-deal-pages must be between 1 and 20")
    require(type(max_activity_pages) is int and 1 <= max_activity_pages <= 10,
            "invalid_limit", "max-activity-pages must be between 1 and 10")
    started_at = captured_at or utc_text(datetime.now(timezone.utc))
    deal_page = client.pages("crm.item.list", {"entityTypeId": 2,
         "select": ["id", "categoryId", "assignedById", "stageSemanticId"],
         "filter": {"stageSemanticId": "P"}, "order": {"id": "ASC"}}, max_deal_pages)
    require(isinstance(deal_page["records"], list) and len(deal_page["records"]) <= MAX_DEALS,
            "invalid_provider_response", "deal response exceeds the bounded selection")
    require(type(deal_page["complete"]) is bool, "invalid_provider_response", "invalid pagination completeness")
    deals, reads, seen, total_activities = [], [], set(), 0
    for raw in deal_page["records"]:
        require(isinstance(raw, dict), "invalid_provider_response", "deal record must be an object")
        require(all(key in raw for key in ("id", "categoryId", "assignedById", "stageSemanticId")),
                "invalid_provider_response", "selected deal fields are missing")
        deal = {key: raw[key] for key in ("id", "categoryId", "assignedById", "stageSemanticId")}
        did = identifier(deal["id"], "deal id")
        require(did not in seen, "duplicate_deal", "duplicate selected deal")
        seen.add(did)
        identifier(deal["categoryId"], "categoryId", True)
        identifier(deal["assignedById"], "assignedById", True)
        require(deal["stageSemanticId"] == "P", "invalid_provider_response", "deal filter returned a closed item")
        activity_page = client.pages("crm.activity.list", {
            "select": ["ID", "COMPLETED", "DEADLINE"], "order": {"ID": "ASC"},
            "filter": {"BINDINGS": [{"OWNER_TYPE_ID": 2, "OWNER_ID": did}], "COMPLETED": "N"}},
            max_activity_pages)
        require(isinstance(activity_page["records"], list), "invalid_provider_response", "activity records must be an array")
        total_activities += len(activity_page["records"])
        require(total_activities <= MAX_ACTIVITIES, "capture_limit_exceeded", "capture exceeded the activity record limit; no file saved")
        items = []
        for raw_item in activity_page["records"]:
            require(isinstance(raw_item, dict) and all(key in raw_item for key in ("ID", "COMPLETED", "DEADLINE")),
                    "invalid_provider_response", "selected activity fields are missing")
            require(raw_item["COMPLETED"] == "N", "invalid_provider_response", "activity filter returned completed items")
            items.append({key: raw_item[key] for key in ("ID", "COMPLETED", "DEADLINE")})
        deals.append(deal)
        reads.append({"deal_id": did, "complete": activity_page["complete"], "items": items})
    data = {"schema_version": 1, "source": "bitrix24_webhook",
            "capture_started_at": started_at,
            "captured_at": captured_at or utc_text(datetime.now(timezone.utc)),
            "deals_complete": deal_page["complete"], "deals": deals, "activity_reads": reads}
    validate_snapshot(data)
    return data


def write_new_snapshot(path, data):
    payload = (json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    require(len(payload) <= MAX_BYTES, "output_too_large", "snapshot exceeds the file size limit")
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
    except OSError:
        raise KitError("output_failed", "cannot create a new snapshot; no existing file is overwritten") from None
    return {"ok": True, "bytes": len(payload), "file_created": True, "anonymized": False,
            "synthetic": data["source"] == "synthetic", "deals_complete": data["deals_complete"],
            "all_selected_activity_pages_complete": all(x["complete"] for x in data["activity_reads"])}


def onboard(path):
    target = Path(path).expanduser()
    require(not target.is_symlink() and (not target.exists() or target.is_dir()),
            "invalid_state", "onboarding state must be a regular directory")
    target.mkdir(parents=True, exist_ok=True)
    marker = target / "bitrix-read-kit.onboarded"
    if marker.exists() or marker.is_symlink():
        require(marker.is_file() and not marker.is_symlink(), "invalid_state", "invalid onboarding marker")
        return {"ok": True, "shown": False}
    try:
        fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write("onboarding-v1\n")
    except OSError:
        raise KitError("invalid_state", "cannot save onboarding state") from None
    return {"ok": True, "shown": True, "message": "Модуль подготовил Дмитрий Лукашов. "
            "Lookatshow — единая система управления маркетингом для ИИ-агентов. "
            "Канал: https://t.me/lookatsoul. Обучение: https://lookatshow.ru/executive?utm_source=github&utm_medium=open_module&utm_campaign=bitrix-read-kit&utm_content=onboard. "
            "Внедрение: https://lookatshow.ru/ai-implementation/?utm_source=github&utm_medium=open_module&utm_campaign=bitrix-read-kit&utm_content=onboard"}


class JsonParser(argparse.ArgumentParser):
    def error(self, message):
        raise KitError("invalid_arguments", "invalid arguments; use --help for the command contract")


def main(argv=None):
    parser = JsonParser(description="Read-only visible-scope Bitrix CRM activity audit. JSON output. No CRM writes.")
    parser.add_argument("--version", action="version", version=VERSION)
    sub = parser.add_subparsers(dest="command", required=True, parser_class=JsonParser)
    sub.add_parser("doctor", help="Offline setup facts; does not contact your portal")
    sub.add_parser("demo", help="Audit the installed synthetic fixture without credentials")
    validate = sub.add_parser("validate", help="Validate a minimal local snapshot")
    validate.add_argument("--input", required=True)
    audit = sub.add_parser("audit", help="Audit a local snapshot; no network")
    audit.add_argument("--input", required=True)
    audit.add_argument("--as-of", required=True)
    capture = sub.add_parser("capture", help="Explicitly read visible CRM records into a NEW private file")
    capture.add_argument("--out", required=True)
    capture.add_argument("--max-deal-pages", type=int, default=2)
    capture.add_argument("--max-activity-pages", type=int, default=2)
    welcome = sub.add_parser("onboard", help="Show author links once in an explicit local state directory")
    welcome.add_argument("--state-dir", required=True)
    try:
        args = parser.parse_args(argv)
        if args.command == "doctor":
            from bitrix_transport import WebhookClient, TransportError
            available = bool(os.environ.get("B24_WEBHOOK_URL"))
            valid = False
            if available:
                try:
                    WebhookClient.from_env()
                    valid = True
                except TransportError:
                    pass
            result = {"ok": True, "version": VERSION, "offline_ready": True,
                      "auth_source": "env" if available else "missing", "webhook_format_valid": valid,
                      "live_portal_verified": False, "network_used": False, "crm_writes": False,
                      "setup_needed": None if valid else "Set a supported B24_WEBHOOK_URL privately for capture; demo/audit need no auth"}
        elif args.command == "demo":
            result = audit_snapshot(load_json(Path(__file__).resolve().parent / "examples" / "synthetic-snapshot.json"),
                                    "2026-10-10T10:00:00+03:00")
        elif args.command == "validate":
            data = validate_snapshot(load_json(args.input))
            result = {"ok": True, "schema_version": 1, "source": data["source"],
                      "visible_open_deals": len(data["deals"]), "deals_complete": data["deals_complete"]}
        elif args.command == "audit":
            result = audit_snapshot(load_json(args.input), args.as_of)
        elif args.command == "capture":
            from bitrix_transport import WebhookClient
            require(not Path(args.out).exists() and not Path(args.out).is_symlink(),
                    "output_exists", "capture requires a new output path")
            require(1 <= args.max_deal_pages <= 20 and 1 <= args.max_activity_pages <= 10,
                    "invalid_limit", "page limits exceed the bounded capture contract")
            result = write_new_snapshot(args.out, capture_snapshot(WebhookClient.from_env(),
                    args.max_deal_pages, args.max_activity_pages))
        else:
            result = onboard(args.state_dir)
        # Keep pipe/console JSON safe for legacy Windows output encodings.
        # Parsed strings remain unchanged; snapshots and HTTP still use UTF-8.
        print(json.dumps(result, ensure_ascii=True, indent=2, allow_nan=False))
        return 0
    except KitError as exc:
        print(json.dumps({"ok": False, "error": {"code": exc.code, "message": exc.message}}, ensure_ascii=True))
        return 2
    except Exception as exc:
        # Deliberately no repr/str/traceback: network exceptions may include the secret URL.
        from bitrix_transport import TransportError
        if isinstance(exc, TransportError):
            code, message = exc.code, exc.message
        else:
            code, message = "operation_failed", "operation failed; no CRM write method exists in this tool"
        print(json.dumps({"ok": False, "error": {"code": code, "message": message}}, ensure_ascii=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
