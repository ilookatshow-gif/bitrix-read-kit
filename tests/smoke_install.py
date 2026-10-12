"""Offline native-launcher/clean-install smoke. Never connects to a CRM.

Run from the source package: python -B tests/smoke_install.py
Windows uses the actual .cmd; POSIX uses the executable shebang wrapper.
"""
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def native_command(prefix, *args):
    if os.name == "nt":
        # Only generated local paths and fixed synthetic arguments reach cmd.
        launcher = prefix / "bin" / "lookatshow-bitrix-read.cmd"
        command = '""{}" {}"'.format(launcher, subprocess.list2cmdline(list(args)))
        interpreter = [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c"]
        # One raw Windows command line avoids adding CRT backslash-quote escapes.
        return subprocess.list2cmdline(interpreter) + " " + command
    return [str(prefix / "bin" / "lookatshow-bitrix-read"), *args]


def run(command, cwd, expected=0):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1",
               PYTHONIOENCODING="utf-8")
    env.pop("B24_WEBHOOK_URL", None)
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    result = subprocess.run(command, cwd=cwd, env=env, capture_output=True,
                            encoding="utf-8", timeout=90)
    require(result.returncode == expected,
            "command failed: {}\n{}\n{}".format(command, result.stdout, result.stderr))
    return result


def data(command, cwd, expected=0):
    result = run(command, cwd, expected)
    require(not result.stderr, "unexpected stderr: " + result.stderr)
    return json.loads(result.stdout)


def main():
    manifest = json.loads((ROOT / "MODULE.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="bitrix-read-smoke-") as temporary:
        outside = Path(temporary).resolve()
        prefix = outside / "installed пакет"
        cwd = outside / "different каталог"
        cwd.mkdir()
        installed = data([sys.executable, "-B", str(ROOT / "install.py"),
                          "--prefix", str(prefix)], cwd)
        require(installed["ok"] and installed["copied_files"] == len(manifest["publish_files"]),
                "installer receipt does not match whitelist")
        library = prefix / "lib" / "bitrix-read-kit"
        for name in manifest["publish_files"]:
            require((library / name).read_bytes() == (ROOT / name).read_bytes(),
                    "installed bytes differ: " + name)
        wrapper = (prefix / "bin" / "lookatshow-bitrix-read.cmd").read_bytes()
        require(wrapper == b'@echo off\r\npython "%~dp0..\\lib\\bitrix-read-kit\\bitrix_read_kit.py" %*\r\n',
                "Windows wrapper CRLF/command contract differs")
        again = data([sys.executable, "-B", str(ROOT / "install.py"),
                      "--prefix", str(prefix)], cwd, expected=2)
        require(again["error"]["code"] == "prefix_exists", "existing prefix not refused")
        doctor = data(native_command(prefix, "doctor"), cwd)
        require(doctor["ok"] and doctor["auth_source"] == "missing" and
                doctor["network_used"] is False and doctor["crm_writes"] is False,
                "offline doctor contract changed")
        demo = data(native_command(prefix, "demo"), cwd)
        fixture = str(library / "examples" / "synthetic-snapshot.json")
        valid = data(native_command(prefix, "validate", "--input", fixture), cwd)
        require(valid["source"] == "synthetic" and valid["visible_open_deals"] == 5,
                "fixture must remain synthetic")
        audit = data(native_command(prefix, "audit", "--input", fixture,
                                    "--as-of", "2026-10-10T10:00:00+03:00"), cwd)
        require(audit == demo and audit["synthetic"] is True, "native audit differs from demo")
        require(audit["summary"]["unique_visible_open_activities"] == 3,
                "synthetic activity count differs")
        normal = json.dumps(audit, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        require("lookatsoul" not in normal and "lookatshow.ru" not in normal,
                "ordinary audit contains recurring advertising")
        state = str(outside / "chosen состояние")
        first = data(native_command(prefix, "onboard", "--state-dir", state), cwd)
        second = data(native_command(prefix, "onboard", "--state-dir", state), cwd)
        require(first["shown"] is True and "Дмитрий Лукашов" in first["message"],
                "first greeting missing")
        require(second == {"ok": True, "shown": False}, "greeting repeated")
        missing_capture = str(outside / "must-not-be-created.json")
        missing = data(native_command(prefix, "capture", "--out", missing_capture), cwd, expected=2)
        require(missing["error"]["code"] == "CONFIG_MISSING" and not Path(missing_capture).exists(),
                "missing-auth capture must not create output")
        tests = run([sys.executable, "-B", "-m", "unittest", "discover",
                     "-s", str(library / "tests"), "-v"], cwd)
        require("OK" in tests.stderr, "installed regression suite did not complete")
        tested = re.search(r"Ran (\d+) tests", tests.stderr)
        require(tested is not None, "installed suite count missing")
        print(json.dumps({"ok": True, "platform": platform.system(),
                          "architecture": platform.machine(), "python": platform.python_version(),
                          "files_verified": len(manifest["publish_files"]),
                          "native_launcher": ".cmd" if os.name == "nt" else "POSIX",
                          "source_and_install_separate": True, "synthetic": True,
                          "installed_tests": int(tested.group(1)),
                          "ordinary_json_ad_free": True, "onboarding_once": True,
                          "missing_auth_capture_no_file": True,
                          "report_sha256": hashlib.sha256(normal.encode("utf-8")).hexdigest()},
                         ensure_ascii=True))


if __name__ == "__main__":
    main()
