import copy
from datetime import datetime, timezone
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import bitrix_read_kit as kit
from smoke_install import native_command


def fixture():
    return kit.load_json(ROOT / "examples" / "synthetic-snapshot.json")


class SnapshotTests(unittest.TestCase):
    def test_demo_result(self):
        result = kit.audit_snapshot(fixture(), "2026-10-10T10:00:00+03:00")
        self.assertEqual(result["summary"]["visible_open_deals"], 5)
        self.assertEqual(result["summary"]["unique_visible_open_activities"], 3)
        flags = result["summary"]["deal_flag_counts_nonexclusive"]
        self.assertEqual(flags, {"no_visible_open_activity": 1, "scheduled": 1, "overdue": 1,
                                 "undated": 1, "unknown_deadline": 0, "activity_coverage_incomplete": 1})
        self.assertEqual(result["deals"][-1]["flags"], ["activity_coverage_incomplete"])
        self.assertFalse(result["coverage"]["all_selected_activity_pages_complete"])
        self.assertFalse(result["coverage"]["portal_wide_completeness_proven"])
        self.assertTrue(result["synthetic"])

    def test_unknown_deadline_is_not_confirmed_undated(self):
        for value in [None, ""]:
            data = fixture()
            data["activity_reads"][3]["items"][0]["DEADLINE"] = value
            row = kit.audit_snapshot(data, "2026-10-10T10:00:00+03:00")["deals"][3]
            self.assertEqual(row["flags"], ["unknown_deadline"])
            self.assertEqual(row["unknown_deadline_activity_ids"], [203])
            self.assertEqual(row["undated_activity_ids"], [])

    def test_sentinel_zone_does_not_overflow(self):
        self.assertIsNone(kit.deadline_time("9999-12-31T23:59:59-14:00"))

    def test_valid_timezone_and_equal_deadline(self):
        data = fixture()
        data["activity_reads"][1]["items"][0]["DEADLINE"] = "2026-10-10T07:00:00Z"
        row = kit.audit_snapshot(data, "2026-10-10T10:00:00+03:00")["deals"][1]
        self.assertEqual(row["flags"], ["scheduled"])
        self.assertEqual(row["next_scheduled_visible_activity"]["deadline_utc"], "2026-10-10T07:00:00Z")

    def test_incomplete_with_evidence_does_not_hide_evidence(self):
        data = fixture()
        data["activity_reads"][1]["complete"] = False
        row = kit.audit_snapshot(data, "2026-10-10T10:00:00+03:00")["deals"][1]
        self.assertEqual(row["flags"], ["activity_coverage_incomplete", "scheduled"])
        self.assertEqual(row["visible_open_activity_ids"], [201])

    def test_incomplete_deals_not_full_portal(self):
        data = fixture()
        data["deals_complete"] = False
        result = kit.audit_snapshot(data, "2026-10-10T10:00:00+03:00")
        self.assertFalse(result["coverage"]["selected_deal_pages_complete"])
        self.assertFalse(result["coverage"]["portal_wide_completeness_proven"])

    def test_shared_activity_counted_once(self):
        data = fixture()
        data["activity_reads"][2]["items"] = [copy.deepcopy(data["activity_reads"][1]["items"][0])]
        result = kit.audit_snapshot(data, "2026-10-10T10:00:00+03:00")
        self.assertEqual(result["summary"]["unique_visible_open_activities"], 2)
        self.assertEqual(result["summary"]["deal_flag_counts_nonexclusive"]["scheduled"], 2)

    def test_conflicting_shared_activity_rejected(self):
        data = fixture()
        data["activity_reads"][2]["items"][0]["ID"] = 201
        with self.assertRaises(kit.KitError) as err:
            kit.validate_snapshot(data)
        self.assertEqual(err.exception.code, "conflicting_activity")

    def test_closed_deal_rejected(self):
        data = fixture()
        data["deals"][0]["stageSemanticId"] = "S"
        with self.assertRaises(kit.KitError):
            kit.validate_snapshot(data)

    def test_duplicate_deal_rejected(self):
        data = fixture()
        data["deals"][1]["id"] = 101
        with self.assertRaises(kit.KitError):
            kit.validate_snapshot(data)

    def test_duplicate_activity_rejected(self):
        data = fixture()
        data["activity_reads"][1]["items"] *= 2
        with self.assertRaises(kit.KitError):
            kit.validate_snapshot(data)

    def test_missing_activity_read_rejected(self):
        data = fixture()
        data["activity_reads"].pop()
        with self.assertRaises(kit.KitError):
            kit.validate_snapshot(data)

    def test_unexpected_activity_deal_rejected(self):
        data = fixture()
        data["activity_reads"][0]["deal_id"] = 555
        with self.assertRaises(kit.KitError):
            kit.validate_snapshot(data)

    def test_repeated_activity_read_rejected(self):
        data = fixture()
        data["activity_reads"][1]["deal_id"] = 101
        with self.assertRaises(kit.KitError):
            kit.validate_snapshot(data)

    def test_data_minimization_strict_extra_fields(self):
        data = fixture()
        data["deals"][0]["title"] = "Synthetic title not allowed"
        with self.assertRaises(kit.KitError):
            kit.validate_snapshot(data)

    def test_bool_schema_and_completeness_rejected(self):
        for key, value in [("schema_version", True), ("deals_complete", 1)]:
            data = fixture()
            data[key] = value
            with self.assertRaises(kit.KitError):
                kit.validate_snapshot(data)

    def test_invalid_ids(self):
        for bad in [True, 1.5, 0, -1, " 1", "1.0", "1" * 30]:
            with self.subTest(bad=bad), self.assertRaises(kit.KitError):
                kit.identifier(bad)

    def test_unknown_source_rejected(self):
        data = fixture()
        data["source"] = "official_portal_tested"
        with self.assertRaises(kit.KitError):
            kit.validate_snapshot(data)

    def test_invalid_dates(self):
        for bad in ["2026-10-10", "2026-10-10T10:00:00", "2026-02-30T10:00:00Z",
                    "2026-10-10T10:00:00+01:99", "2026-10-10T10:00:00+14:01",
                    "0001-01-01T00:00:00+14:00", 0, True]:
            with self.subTest(bad=bad), self.assertRaises(kit.KitError):
                kit.parse_time(bad)

    def test_asof_before_capture_rejected(self):
        with self.assertRaises(kit.KitError):
            kit.audit_snapshot(fixture(), "2026-10-09T10:00:00+03:00")

    def test_reversed_capture_window_rejected(self):
        data = fixture()
        data["capture_started_at"] = "2026-10-10T10:00:00+03:00"
        with self.assertRaises(kit.KitError):
            kit.validate_snapshot(data)

    def test_capture_window_in_report(self):
        result = kit.audit_snapshot(fixture(), "2026-10-10T10:00:00+03:00")
        self.assertEqual(result["capture_window"], {"started_at": "2026-10-10T06:00:00Z", "finished_at": "2026-10-10T06:00:00Z"})

    def test_invalid_deadline_rejected(self):
        data = fixture()
        data["activity_reads"][1]["items"][0]["DEADLINE"] = "later"
        with self.assertRaises(kit.KitError):
            kit.validate_snapshot(data)

    def test_completed_activity_not_open(self):
        data = fixture()
        data["activity_reads"][1]["items"][0]["COMPLETED"] = "Y"
        row = kit.audit_snapshot(data, "2026-10-10T10:00:00+03:00")["deals"][1]
        self.assertEqual(row["flags"], ["no_visible_open_activity"])

    def test_empty_complete_sample(self):
        data = fixture()
        data["deals"] = []
        data["activity_reads"] = []
        result = kit.audit_snapshot(data, "2026-10-10T10:00:00+03:00")
        self.assertEqual(result["summary"]["visible_open_deals"], 0)
        self.assertFalse(result["coverage"]["portal_wide_completeness_proven"])

    def test_no_advertising_in_normal_output(self):
        text = json.dumps(kit.audit_snapshot(fixture(), "2026-10-10T10:00:00+03:00"))
        self.assertNotIn("lookatshow.ru", text)
        self.assertNotIn("lookatsoul", text)


class FilesAndCLITests(unittest.TestCase):
    def test_long_integer_literal_rejected_before_conversion(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.json"
            for sign in ("", "-"):
                path.write_text('{"value":' + sign + "9" * 5000 + "}")
                with self.assertRaises(kit.KitError) as error:
                    kit.load_json(path)
                self.assertEqual(error.exception.code, "invalid_json")
                self.assertNotIn("9" * 20, str(error.exception))

    def test_json_duplicate_and_nonfinite(self):
        with tempfile.TemporaryDirectory() as directory:
            for content in ['{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}', '{']:
                path = Path(directory) / "input.json"
                path.write_text(content)
                with self.assertRaises(kit.KitError):
                    kit.load_json(path)

    def test_symlink_and_directory_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "data.json"
            target.write_text("{}")
            link = root / "link.json"
            link.symlink_to(target)
            for path in [link, root]:
                with self.assertRaises(kit.KitError):
                    kit.load_json(path)

    def test_leaf_symlink_rejected_without_o_nofollow(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "synthetic.json"
            target.write_text("{}", encoding="utf-8")
            link = root / "link.json"
            link.symlink_to(target)
            with mock.patch.object(os, "O_NOFOLLOW", 0, create=True):
                with self.assertRaises(kit.KitError) as raised:
                    kit.load_json(link)
            self.assertEqual(raised.exception.code, "invalid_file")

    @unittest.skipUnless(hasattr(os, "mkfifo"), "POSIX-only FIFO check")
    def test_fifo_rejected_without_blocking(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pipe"
            os.mkfifo(path)
            with self.assertRaises(kit.KitError):
                kit.load_json(path)

    def test_large_file_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.json"
            path.write_bytes(b" " * (kit.MAX_BYTES + 1))
            with self.assertRaises(kit.KitError):
                kit.load_json(path)

    def test_new_snapshot_no_overwrite_and_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture.json"
            result = kit.write_new_snapshot(path, fixture())
            self.assertTrue(result["file_created"])
            self.assertFalse(result["anonymized"])
            self.assertEqual(kit.load_json(path), fixture())
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            before = path.read_bytes()
            with self.assertRaises(kit.KitError):
                kit.write_new_snapshot(path, {})
            self.assertEqual(path.read_bytes(), before)

    def test_onboard_once(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "onboarding"
            self.assertTrue(kit.onboard(state)["shown"])
            self.assertEqual(kit.onboard(state), {"ok": True, "shown": False})

    def test_cp1252_onboard_first_and_second_preserve_russian_message(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "состояние"
            env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="0",
                       PYTHONIOENCODING="cp1252")
            env.pop("B24_WEBHOOK_URL", None)
            command = [sys.executable, str(ROOT / "bitrix_read_kit.py"),
                       "onboard", "--state-dir", str(state)]
            first = subprocess.run(command, cwd=directory, env=env, capture_output=True, timeout=10)
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            self.assertEqual(first.stderr, b"")
            text = first.stdout.decode("ascii")
            result = json.loads(text)
            self.assertTrue(result["shown"])
            self.assertIn("Модуль подготовил Дмитрий Лукашов.", result["message"])
            self.assertIn("https://t.me/lookatsoul", result["message"])
            self.assertTrue((state / "bitrix-read-kit.onboarded").is_file())
            second = subprocess.run(command, cwd=directory, env=env, capture_output=True, timeout=10)
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            self.assertEqual(second.stderr, b"")
            self.assertEqual(json.loads(second.stdout.decode("ascii")), {"ok": True, "shown": False})

    def test_cp1252_valid_snapshot_at_cyrillic_path(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "снимок с пробелами.json"
            path.write_text(json.dumps(fixture(), ensure_ascii=False), encoding="utf-8")
            env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="0",
                       PYTHONIOENCODING="cp1252")
            env.pop("B24_WEBHOOK_URL", None)
            for command in [
                ["validate", "--input", str(path)],
                ["audit", "--input", str(path), "--as-of", "2026-10-10T10:00:00+03:00"],
            ]:
                run = subprocess.run([sys.executable, str(ROOT / "bitrix_read_kit.py"), *command],
                                     cwd=directory, env=env, capture_output=True, timeout=10)
                self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
                self.assertEqual(run.stderr, b"")
                result = json.loads(run.stdout.decode("ascii"))
                self.assertTrue(result["ok"])
                if command[0] == "audit":
                    self.assertEqual(result, kit.audit_snapshot(fixture(), "2026-10-10T10:00:00+03:00"))

    def test_invalid_onboard_marker_and_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "onboarding"
            state.write_text("file")
            with self.assertRaises(kit.KitError):
                kit.onboard(state)
            invalid = Path(directory) / "invalid"
            invalid.mkdir()
            (invalid / "bitrix-read-kit.onboarded").mkdir()
            with self.assertRaises(kit.KitError):
                kit.onboard(invalid)

    def test_cli_invalid_arguments_json(self):
        stream = io.StringIO()
        with redirect_stdout(stream):
            rc = kit.main(["crm", "delete", "synthetic-secret"])
        result = json.loads(stream.getvalue())
        self.assertEqual(rc, 2)
        self.assertEqual(result["error"]["code"], "invalid_arguments")
        self.assertNotIn("synthetic-secret", stream.getvalue())

    def test_demo_from_foreign_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
            env.pop("B24_WEBHOOK_URL", None)
            run = subprocess.run([sys.executable, str(ROOT / "bitrix_read_kit.py"), "demo"],
                                 cwd=directory, env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(json.loads(run.stdout)["summary"]["visible_open_deals"], 5)
            self.assertEqual(run.stderr, "")


class InstallerTests(unittest.TestCase):
    def run_install(self, prefix, source=ROOT):
        return subprocess.run([sys.executable, str(source / "install.py"), "--prefix", str(prefix)],
                              cwd=prefix.parent, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
                              capture_output=True, text=True, timeout=10)

    def test_installed_command_path_and_sources(self):
        with tempfile.TemporaryDirectory() as raw:
            parent = Path(raw).resolve()
            prefix = parent / "prefix"
            install = self.run_install(prefix)
            self.assertEqual(install.returncode, 0, install.stdout + install.stderr)
            receipt = json.loads(install.stdout)
            manifest = kit.load_json(ROOT / "MODULE.json")
            self.assertEqual(receipt["copied_files"], len(manifest["publish_files"]))
            for name in manifest["publish_files"]:
                self.assertEqual((prefix / "lib" / "bitrix-read-kit" / name).read_bytes(), (ROOT / name).read_bytes())
            env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PATH=str(prefix / "bin") + os.pathsep + os.environ.get("PATH", ""))
            env.pop("B24_WEBHOOK_URL", None)
            for cmd in ["doctor", "demo"]:
                result = subprocess.run(native_command(prefix, cmd), cwd=parent, env=env,
                                        capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertTrue(json.loads(result.stdout)["ok"])
                self.assertEqual(result.stderr, "")
            again = self.run_install(prefix)
            self.assertEqual(again.returncode, 2)
            self.assertEqual(json.loads(again.stdout)["error"]["code"], "prefix_exists")

    def test_cp1252_cyrillic_prefix_receipt_is_successful(self):
        with tempfile.TemporaryDirectory() as raw:
            parent = Path(raw).resolve()
            prefix = parent / "пакет с пробелами"
            env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="0",
                       PYTHONIOENCODING="cp1252")
            env.pop("B24_WEBHOOK_URL", None)
            command = [sys.executable, str(ROOT / "install.py"), "--prefix", str(prefix)]
            run = subprocess.run(command, cwd=parent, env=env, capture_output=True, timeout=10)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertEqual(run.stderr, b"")
            receipt = json.loads(run.stdout.decode("ascii"))
            self.assertTrue(receipt["ok"])
            self.assertEqual(receipt["command"], str(prefix / "bin" / "lookatshow-bitrix-read"))
            self.assertEqual(receipt["windows_command"], str(prefix / "bin" / "lookatshow-bitrix-read.cmd"))
            self.assertEqual(receipt["copied_files"], len(kit.load_json(ROOT / "MODULE.json")["publish_files"]))
            self.assertEqual((prefix / "bin" / "lookatshow-bitrix-read.cmd").read_bytes(),
                             b'@echo off\r\npython "%~dp0..\\lib\\bitrix-read-kit\\bitrix_read_kit.py" %*\r\n')
            again = subprocess.run(command, cwd=parent, env=env, capture_output=True, timeout=10)
            self.assertEqual(again.returncode, 2)
            self.assertEqual(json.loads(again.stdout.decode("ascii"))["error"]["code"], "prefix_exists")

    def test_installer_bad_manifest_cannot_copy_other_content(self):
        with tempfile.TemporaryDirectory() as raw:
            parent = Path(raw).resolve()
            source = parent / "source"
            source.mkdir()
            manifest = kit.load_json(ROOT / "MODULE.json")
            for name in manifest["publish_files"]:
                target = source / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / name, target)
            manifest["publish_files"].append("../private.json")
            (source / "MODULE.json").write_text(json.dumps(manifest), encoding="utf-8")
            run = self.run_install(parent / "prefix", source)
            self.assertEqual(run.returncode, 2)
            self.assertEqual(json.loads(run.stdout)["error"]["code"], "manifest_invalid")
            self.assertFalse((parent / "prefix").exists())

    def test_installer_symlink_parent_and_inside_source_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            parent = Path(raw).resolve()
            link = parent / "link"
            link.symlink_to(parent, target_is_directory=True)
            run = self.run_install(link / "prefix")
            self.assertEqual(run.returncode, 2)
            self.assertEqual(json.loads(run.stdout)["error"]["code"], "symlink_refused")
            nested = self.run_install(ROOT / "test-install-never-created")
            self.assertEqual(nested.returncode, 2)
            self.assertEqual(json.loads(nested.stdout)["error"]["code"], "prefix_invalid")
            self.assertFalse((ROOT / "test-install-never-created").exists())


class CaptureTests(unittest.TestCase):
    class FakeClient:
        def __init__(self, data, activity_complete=True):
            self.calls = []
            self.data = data
            self.activity_complete = activity_complete

        def pages(self, method, params, max_pages):
            self.calls.append((method, params, max_pages))
            if method == "crm.item.list":
                return {"records": self.data["deals"], "complete": self.data["deals_complete"]}
            did = params["filter"]["BINDINGS"][0]["OWNER_ID"]
            read = next(x for x in self.data["activity_reads"] if x["deal_id"] == did)
            return {"records": [x for x in read["items"] if x["COMPLETED"] == "N"],
                    "complete": read["complete"]}

    def test_capture_builders_and_scope(self):
        client = self.FakeClient(fixture())
        data = kit.capture_snapshot(client, captured_at="2026-10-10T10:00:00+03:00")
        self.assertEqual(data["source"], "bitrix24_webhook")
        self.assertEqual(len(client.calls), 6)
        method, params, limit = client.calls[0]
        self.assertEqual(method, "crm.item.list")
        self.assertEqual(params["entityTypeId"], 2)
        self.assertEqual(params["filter"], {"stageSemanticId": "P"})
        self.assertNotIn("title", params["select"])
        self.assertNotIn("opportunity", params["select"])
        self.assertEqual(client.calls[1][1]["filter"], {"BINDINGS": [{"OWNER_TYPE_ID": 2, "OWNER_ID": 101}], "COMPLETED": "N"})
        self.assertEqual(client.calls[1][1]["select"], ["ID", "COMPLETED", "DEADLINE"])
        self.assertFalse(data["activity_reads"][-1]["complete"])

    def test_capture_drops_unselected_provider_fields(self):
        data = fixture()
        data["deals"][0]["title"] = "discarded synthetic title"
        client = self.FakeClient(data)
        result = kit.capture_snapshot(client, captured_at="2026-10-10T10:00:00Z")
        self.assertNotIn("title", result["deals"][0])

    def test_capture_invalid_limits_before_network(self):
        client = self.FakeClient(fixture())
        for args in [(0, 1), (21, 1), (1, 11), (True, 1)]:
            with self.assertRaises(kit.KitError):
                kit.capture_snapshot(client, *args)
        self.assertEqual(client.calls, [])

    def test_invalid_deal_before_activity_network(self):
        data = fixture()
        data["deals"][0]["stageSemanticId"] = "S"
        client = self.FakeClient(data)
        with self.assertRaises(kit.KitError):
            kit.capture_snapshot(client)
        self.assertEqual(len(client.calls), 1)

    def test_capture_activity_budget_stops_before_next_deal(self):
        client = self.FakeClient(fixture())
        with mock.patch.object(kit, "MAX_ACTIVITIES", 0):
            with self.assertRaises(kit.KitError) as err:
                kit.capture_snapshot(client)
        self.assertEqual(err.exception.code, "capture_limit_exceeded")
        # Deal101 has no items; Deal102 reaches the record budget; later deals are not read.
        self.assertEqual(len(client.calls), 3)

    def test_real_transport_mocked_end_to_end(self):
        import bitrix_transport as transport
        from test_transport import FakeOpener, make_response, FAKE_URL
        data = fixture()
        responses = [make_response({"result": {"items": data["deals"]}})]
        for read in data["activity_reads"]:
            envelope = {"result": [x for x in read["items"] if x["COMPLETED"] == "N"]}
            if not read["complete"]:
                envelope["next"] = 50
            responses.append(make_response(envelope))
        opener = FakeOpener(*responses)
        client = transport.WebhookClient(FAKE_URL, opener=opener, min_interval=0)
        snapshot = kit.capture_snapshot(client, 1, 1, "2026-10-10T10:00:00+03:00")
        result = kit.audit_snapshot(snapshot, "2026-10-10T10:00:00+03:00")
        self.assertEqual(len(opener.calls), 6)
        self.assertFalse(result["synthetic"])
        self.assertEqual(result["summary"]["visible_open_deals"], 5)
        self.assertEqual(result["deals"][-1]["flags"], ["activity_coverage_incomplete"])
        self.assertNotIn("not-a-real-secret", json.dumps(snapshot))

    def test_doctor_never_opens_network(self):
        import bitrix_transport as transport
        from test_transport import FAKE_URL
        for env in [{}, {"B24_WEBHOOK_URL": FAKE_URL}, {"B24_WEBHOOK_URL": "malformed-synthetic-secret"}]:
            output = io.StringIO()
            with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(transport.WebhookClient, "call", side_effect=AssertionError("network forbidden")), redirect_stdout(output):
                self.assertEqual(kit.main(["doctor"]), 0)
            result = json.loads(output.getvalue())
            self.assertFalse(result["network_used"])
            self.assertFalse(result["live_portal_verified"])
            self.assertNotIn("malformed-synthetic-secret", output.getvalue())

    def test_capture_missing_auth_has_safe_json_and_no_file(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "capture.json"
            output = io.StringIO()
            with mock.patch.dict(os.environ, {}, clear=True), redirect_stdout(output):
                self.assertEqual(kit.main(["capture", "--out", str(target)]), 2)
            self.assertEqual(json.loads(output.getvalue())["error"]["code"], "CONFIG_MISSING")
            self.assertFalse(target.exists())

    def test_capture_transport_failure_has_no_file_or_secret(self):
        import bitrix_transport as transport
        from test_transport import FAKE_URL
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "capture.json"
            output = io.StringIO()
            with mock.patch.dict(os.environ, {"B24_WEBHOOK_URL": FAKE_URL}, clear=True), mock.patch.object(transport.WebhookClient, "pages", side_effect=transport.TransportError("PORTAL_BLOCKED", FAKE_URL)), redirect_stdout(output):
                self.assertEqual(kit.main(["capture", "--out", str(target)]), 2)
            self.assertEqual(json.loads(output.getvalue())["error"]["code"], "PORTAL_BLOCKED")
            self.assertNotIn("not-a-real-secret", output.getvalue())
            self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
