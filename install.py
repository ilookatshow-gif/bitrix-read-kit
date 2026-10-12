#!/usr/bin/env python3
"""Install only this original package's explicit file manifest into a NEW prefix."""
import argparse
import json
import os
from pathlib import Path
import stat
import sys

FILES = (
    "bitrix_read_kit.py",
    "bitrix_transport.py",
    "install.py",
    "README.md",
    "README.ru.md",
    "LICENSE",
    "NOTICE.md",
    "MODULE.json",
    "AGENTS.md",
    "skills/bitrix-read-kit/SKILL.md",
    "tests/test_kit.py",
    "tests/test_transport.py",
    "tests/smoke_install.py",
    "examples/synthetic-snapshot.json",
    ".github/workflows/ci.yml",
)
MAX_SOURCE_BYTES = 2_000_000


class InstallError(Exception):
    def __init__(self, code, message):
        self.code = code
        self.message = message
        super().__init__(message)


def require(condition, code, message):
    if not condition:
        raise InstallError(code, message)


def no_symlinks(path):
    """Check every existing component before resolving or writing a path."""
    for component in (path,) + tuple(path.parents):
        require(not component.is_symlink(), "symlink_refused",
                "symbolic links are not accepted in installation paths")


def load_sources(source):
    no_symlinks(source)
    require(source.is_dir(), "source_invalid", "package source must be a regular directory")
    payloads = {}
    for relative in FILES:
        candidate = source / relative
        no_symlinks(candidate)
        try:
            require(stat.S_ISREG(candidate.stat().st_mode), "source_invalid",
                    "each manifest entry must be a regular file")
            require(candidate.stat().st_size <= MAX_SOURCE_BYTES, "source_invalid",
                    "a package file exceeds the size bound")
            payloads[relative] = candidate.read_bytes()
            require(len(payloads[relative]) <= MAX_SOURCE_BYTES, "source_invalid",
                    "a package file exceeds the size bound")
        except OSError:
            raise InstallError("source_invalid", "a required package file cannot be read") from None
    try:
        manifest = json.loads(payloads["MODULE.json"].decode("utf-8"))
    except (UnicodeError, ValueError, RecursionError):
        raise InstallError("manifest_invalid", "MODULE.json must contain valid UTF-8 JSON") from None
    require(isinstance(manifest, dict), "manifest_invalid", "MODULE.json must be an object")
    require(manifest.get("id") == "bitrix-read-kit" and manifest.get("license") == "MIT"
            and manifest.get("telemetry") is False, "manifest_invalid",
            "module identity, license or telemetry contract does not match this installer")
    entries = manifest.get("publish_files")
    require(isinstance(entries, list) and all(isinstance(entry, str) for entry in entries)
            and len(entries) == len(FILES) and set(entries) == set(FILES), "manifest_invalid",
            "MODULE.json must match the installer's exact file whitelist")
    require(isinstance(manifest.get("version"), str), "manifest_invalid",
            "the package must declare its version")
    return manifest, payloads


def install(prefix_text):
    raw_prefix = Path(prefix_text).expanduser()
    require(".." not in raw_prefix.parts, "prefix_invalid",
            "prefix must not contain parent-directory traversal")
    prefix = Path(os.path.abspath(str(raw_prefix)))
    source = Path(os.path.abspath(str(Path(__file__).parent)))
    no_symlinks(prefix)
    require(not prefix.exists(), "prefix_exists",
            "select a NEW prefix; this installer does not overwrite an existing installation")
    require(prefix.parent.is_dir(), "parent_missing", "the prefix parent directory must already exist")
    require(source != prefix and source not in prefix.parents and prefix not in source.parents,
            "prefix_invalid", "installation must be outside the source tree")
    manifest, payloads = load_sources(source)
    try:
        # NEW prefix + exclusive file creation: no existing files are replaced.
        prefix.mkdir(mode=0o700)
        library = prefix / "lib" / "bitrix-read-kit"
        library.mkdir(parents=True, mode=0o700)
        for relative, data in payloads.items():
            target = library / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as handle:
                handle.write(data)
            if os.name == "posix":
                target.chmod(0o644)
        binaries = prefix / "bin"
        binaries.mkdir(mode=0o700)
        wrapper = binaries / "lookatshow-bitrix-read"
        wrapper_text = (
            "#!/usr/bin/env python3\n"
            "import sys\n"
            "from pathlib import Path\n"
            "package = Path(__file__).resolve().parent.parent / 'lib' / 'bitrix-read-kit'\n"
            "sys.path.insert(0, str(package))\n"
            "from bitrix_read_kit import main\n"
            "if __name__ == '__main__':\n"
            "    sys.exit(main())\n"
        )
        with wrapper.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(wrapper_text)
        if os.name == "posix":
            wrapper.chmod(0o755)
        windows = binaries / "lookatshow-bitrix-read.cmd"
        with windows.open("x", encoding="utf-8", newline="") as handle:
            handle.write('@echo off\r\npython "%~dp0..\\lib\\bitrix-read-kit\\bitrix_read_kit.py" %*\r\n')
    except OSError:
        raise InstallError("install_failed",
                           "installation could not finish; no existing installation was overwritten; inspect the new prefix before retrying") from None
    return {
        "ok": True,
        "version": manifest["version"],
        "command": str(wrapper),
        "windows_command": str(windows),
        "copied_files": len(payloads),
        "companion_skill": str(library / "skills" / "bitrix-read-kit" / "SKILL.md"),
        "path_changed": False,
        "global_skill_installed": False,
        "network_used": False,
    }


class JsonParser(argparse.ArgumentParser):
    def error(self, message):
        raise InstallError("invalid_arguments", "use --prefix NEW_DIRECTORY; see --help")


def main(argv=None):
    parser = JsonParser(description="Install the Bitrix read kit into a NEW explicit local prefix. No PATH changes.")
    parser.add_argument("--prefix", required=True, help="new destination directory with an existing parent")
    try:
        args = parser.parse_args(argv)
        print(json.dumps(install(args.prefix), ensure_ascii=True, indent=2))
        return 0
    except InstallError as exc:
        print(json.dumps({"ok": False, "error": {"code": exc.code, "message": exc.message}}, ensure_ascii=True))
        return 2
    except Exception:
        print(json.dumps({"ok": False, "error": {"code": "install_failed", "message": "installation failed; no credentials or exception details are displayed"}}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
