"""Logged-out simulator experiment on an ephemeral GitHub macOS runner."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import plistlib
import re
import shutil
import subprocess
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".ci-output" / "quiet-pilot"
REPORT = OUT / "pilot-report.json"
UPSTREAM = "24638bc69bd083981b178dd1e7f921eebbf7fc46"
BUNDLE = "org.mennya.telegramquiet.pilot"
XCODE = Path("/Applications/Xcode_27.1.app")
REUSED_COMMIT = "f3fe3730efe42bcaefc5deca551a372a94154f3c"
REUSED_IPA_SHA256 = "6d13c409d5c37a95d5d37cddd7d1fda0cd6420a189193c9fe05e9c24b7041d7c"


def require(value, message):
    if not value:
        raise RuntimeError(message)


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def call(*arguments, timeout=120):
    label = " ".join(arguments[:3])
    print("Pilot command: " + label, flush=True)
    started = time.monotonic()
    try:
        result = subprocess.run(
            list(arguments), cwd=ROOT, text=True, capture_output=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        save(timed_out_command=label, command_timeout_seconds=timeout)
        raise RuntimeError("Command timed out after " + str(timeout) + "s: " + label)
    print("Pilot command finished in " + str(round(time.monotonic() - started, 1))
          + "s: " + label, flush=True)
    require(result.returncode == 0, "Command failed: " + " ".join(arguments[:3])
            + "\n" + clean(result.stderr[-4000:]))
    return result.stdout.strip()


def clean(text):
    text = re.sub(
        r"-----BEGIN [^\n]*PRIVATE KEY-----.*?-----END [^\n]*PRIVATE KEY-----",
        "[private key omitted]", text, flags=re.DOTALL,
    )
    text = re.sub(r"(https?://[^\s?\"']+)\?[^\s\"']+", r"\1?[redacted]", text)
    return "\n".join(
        line for line in text.splitlines()
        if not re.search(
            r"(?i)authorization:|(?:api_id|api_hash|password|private_key|token)\s*[:=]"
            r"|x-amz-signature|-----BEGIN .*PRIVATE KEY", line,
        )
    )


def save(**values):
    OUT.mkdir(parents=True, exist_ok=True)
    report = json.loads(REPORT.read_text()) if REPORT.exists() else {}
    report.update(values)
    report["updated_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    REPORT.write_text(json.dumps(report, indent=2) + "\n")
    return report


def guard():
    require(sys.platform == "darwin", "The pilot only runs on macOS CI.")
    require(os.environ.get("GITHUB_ACTIONS") == "true", "GitHub Actions is required.")
    require(os.environ.get("GITHUB_REPOSITORY") == "mennya/telegram-ios-quiet",
            "Refusing to run outside the approved repository.")
    require(os.environ.get("QUIET_RUNNER_ENVIRONMENT") == "github-hosted",
            "Only a standard GitHub-hosted runner is approved.")
    require(os.environ.get("RUNNER_ARCH") == "ARM64", "ARM64 runner required.")


def preflight():
    save(status="checking_environment",
         fork_commit=call("git", "rev-parse", "HEAD"),
         upstream_commit=UPSTREAM,
         image_version=os.environ.get("ImageVersion"),
         available_xcodes=[p.name for p in Path("/Applications").glob("Xcode*.app")])
    versions = json.loads((ROOT / "versions.json").read_text())
    require(versions["xcode"] == "27.1" and versions["app"] == "13.2",
            "Reviewed upstream version changed.")
    require(os.environ.get("DEVELOPER_DIR") == str(XCODE / "Contents/Developer"),
            "Unexpected selected Xcode.")
    require((XCODE / "Contents/Developer/usr/bin/xcodebuild").is_file(),
            "Xcode 27.1 is not installed.")
    actual = call("xcodebuild", "-version")
    require(actual.splitlines()[0] == "Xcode 27.1", "Xcode version mismatch.")
    require(call("xcrun", "--sdk", "iphonesimulator", "--show-sdk-version") == "27.1",
            "Simulator SDK version mismatch.")
    before = shutil.disk_usage(ROOT).free
    removed = []
    if before < 55 * 1024**3:
        applications = Path("/Applications").resolve()
        keep = XCODE.resolve()
        for path in sorted(applications.glob("Xcode*.app")):
            if path.is_symlink():
                continue
            target = path.resolve()
            require(target.parent == applications, "Unexpected Xcode cleanup path.")
            if target == keep:
                continue
            require(target.name.startswith("Xcode") and target.suffix == ".app",
                    "Invalid disposable Xcode bundle.")
            call("sudo", "rm", "-rf", str(target), timeout=240)
            removed.append(target.name)
    memory = int(call("sysctl", "-n", "hw.memsize"))
    available = shutil.disk_usage(ROOT).free
    save(
        status="preflight",
        fork_commit=call("git", "rev-parse", "HEAD"),
        upstream_commit=UPSTREAM,
        app_version=versions["app"],
        xcode=actual,
        simulator_sdk="27.1",
        image_version=os.environ.get("ImageVersion"),
        runner_architecture="ARM64",
        ram_bytes=memory,
        free_bytes_before_cleanup=before,
        free_bytes_after_cleanup=available,
        removed_disposable_xcodes=removed,
        configuration="debug_sim_arm64",
        bundle_identifier=BUNDLE,
        apple_credentials_used=False,
        telegram_credentials_used=False,
        account_login_attempted=False,
        intended_distribution="simulator experiment only",
    )
    require(memory >= 6 * 1024**3, "Insufficient standard-runner memory.")
    require(available >= 35 * 1024**3, "Insufficient disk after disposable-runner cleanup.")
    save(status="preflight_passed")


def build():
    overlay = ROOT / "build-system/quiet-pilot/pilot.bazelrc"
    rc = ROOT / ".bazelrc"
    original = rc.read_text()
    require("quiet-pilot/pilot.bazelrc" not in original, "Overlay already applied.")
    save(original_bazelrc_sha256=digest(rc), overlay_sha256=digest(overlay))
    rc.write_text(original + "\ntry-import %workspace%/build-system/quiet-pilot/pilot.bazelrc\n")
    save(status="building", effective_bazelrc_sha256=digest(rc))
    command = [
        sys.executable, "-u", "build-system/Make/Make.py", "build",
        "--configurationPath=build-system/quiet-pilot/configuration.json",
        "--xcodeManagedCodesigning",
        "--configuration=debug_sim_arm64",
        "--buildNumber=" + str(100000 + int(os.environ["GITHUB_RUN_NUMBER"])),
    ]
    with (OUT / "build.log").open("w") as log:
        process = subprocess.Popen(
            command, cwd=ROOT, text=True, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, bufsize=1,
        )
        private_block = False
        for line in process.stdout:
            if re.search(r"-----BEGIN .*PRIVATE KEY-----", line):
                private_block = True
                continue
            if private_block:
                if re.search(r"-----END .*PRIVATE KEY-----", line):
                    private_block = False
                continue
            safe = clean(line)
            if safe:
                print(safe, flush=True)
                log.write(safe + "\n")
        code = process.wait()
    save(build_exit_code=code)
    require(code == 0, "Upstream simulator build failed; see filtered build.log.")
    ipa = ROOT / "bazel-bin/Telegram/Telegram.ipa"
    require(ipa.is_file(), "The current Bazel output has no simulator archive.")
    target = OUT / "telegram-quiet-simulator-only.ipa"
    shutil.copyfile(ipa, target)
    save(status="built", simulator_archive_sha256=digest(target))


def restore():
    """Reuse only the independently verified pilot #2 binary, never arbitrary input."""
    source = ROOT / ".ci-output/reuse-input"
    require(source.is_dir(), "Missing pinned artifact input.")
    declared = {}
    for line in (source / "SHA256SUMS.txt").read_text().splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9_.-]+)", line)
        require(match and match[2] not in declared, "Invalid artifact checksum manifest.")
        declared[match[2]] = match[1]
    require(set(declared) == {"pilot-report.json", "binary-platform.txt", "build.log",
                              "telegram-quiet-simulator-only.ipa"},
            "Unexpected retained artifact file list.")
    for name, expected in declared.items():
        path = source / name
        require(path.is_file() and not path.is_symlink() and digest(path) == expected,
                "Retained artifact checksum mismatch: " + name)
    report = json.loads((source / "pilot-report.json").read_text())
    require(report["fork_commit"] == REUSED_COMMIT and report["upstream_commit"] == UPSTREAM,
            "Retained source mismatch.")
    require(report["build_exit_code"] == 0 and report["bundle_version"] == "100002",
            "Retained native build did not succeed.")
    require(not report["apple_credentials_used"] and not report["telegram_credentials_used"]
            and not report["account_login_attempted"], "Unexpected retained login context.")
    ipa = source / "telegram-quiet-simulator-only.ipa"
    require(digest(ipa) == REUSED_IPA_SHA256, "Pinned simulator binary hash mismatch.")
    shutil.copyfile(ipa, OUT / ipa.name)
    shutil.copyfile(source / "pilot-report.json", OUT / "reused-build-report.json")
    save(status="built", harness_commit=call("git", "rev-parse", "HEAD"),
         fork_commit=REUSED_COMMIT, reused_build_run_id=43202755565,
         reused_build_run_number=2, compiled_build_number="100002", build_exit_code=0,
         simulator_archive_sha256=REUSED_IPA_SHA256)


def install_diagnostics(device):
    predicate = 'process == "installd" OR process == "appinstalld" OR subsystem == "com.apple.installcoordination"'
    try:
        result = subprocess.run(
            ["xcrun", "simctl", "spawn", device, "log", "show", "--last", "3m",
             "--style", "compact", "--predicate", predicate],
            capture_output=True, text=True, timeout=30,
        )
        output = clean((result.stdout + "\n" + result.stderr)[-24000:])
        (OUT / "simulator-install.log").write_text(output + "\n")
    except (subprocess.TimeoutExpired, OSError):
        save(installer_diagnostics="unavailable within 30 seconds")


def choose_simulator(runtimes, types, listed_devices):
    """Use an installed compatible pair, but never boot a pre-existing device."""
    by_id = {device["identifier"]: device for device in types}
    by_name = {device["name"]: device for device in types}
    candidates = []
    for runtime in runtimes:
        version = tuple(int(v) for v in runtime["version"].split(".")[:2])
        if version > (27, 1):
            continue
        for listed in listed_devices.get(runtime["identifier"], []):
            if not listed.get("isAvailable", True):
                continue
            model = by_id.get(listed.get("deviceTypeIdentifier"))
            if model is None:
                model = by_name.get(listed.get("name"))
            if model and model["name"].startswith("iPhone"):
                priority = ("Duo" not in model["name"], version,
                            model["name"] == "iPhone 17", model["name"] == "iPhone 16")
                candidates.append((priority, runtime, model))
    require(candidates, "No listed compatible iPhone/runtime pair at or below SDK 27.1.")
    _, runtime, model = max(candidates, key=lambda candidate: candidate[0])
    return runtime, model


def smoke():
    save(smoke_phase="verify_archive")
    archive = OUT / "telegram-quiet-simulator-only.ipa"
    unpacked = OUT / "unpacked"
    require(archive.is_file() and not unpacked.exists(), "Missing archive or existing extraction.")
    with zipfile.ZipFile(archive) as source:
        require(len(source.infolist()) <= 30000, "Unexpected archive entry count.")
        total = 0
        for entry in source.infolist():
            path = PurePosixPath(entry.filename)
            require(not path.is_absolute() and ".." not in path.parts
                    and "\\" not in entry.filename, "Unsafe archive member.")
            require((entry.external_attr >> 16) & 0o170000 != 0o120000,
                    "Unexpected archive symlink.")
            total += entry.file_size
        require(total < 4 * 1024**3, "Unexpected expanded archive size.")
    call("ditto", "-x", "-k", str(archive), str(unpacked))
    apps = list((unpacked / "Payload").glob("*.app"))
    require(len(apps) == 1, "Expected exactly one app bundle.")
    app = apps[0]
    info = plistlib.loads((app / "Info.plist").read_bytes())
    require(info["CFBundleIdentifier"] == BUNDLE, "Unexpected bundle identifier.")
    require("iPhoneSimulator" in info.get("CFBundleSupportedPlatforms", []),
            "Refusing a device build in the simulator pilot.")
    require(info["CFBundleShortVersionString"] == "13.2", "Unexpected app version.")
    report = json.loads(REPORT.read_text())
    expected_build = report.get("compiled_build_number",
                                str(100000 + int(os.environ["GITHUB_RUN_NUMBER"])))
    require(info["CFBundleVersion"] == expected_build,
            "Archive does not carry this run's build number.")
    executable = app / info["CFBundleExecutable"]
    require("arm64" in call("lipo", "-archs", str(executable)), "ARM64 app required.")
    binary_platform = call("xcrun", "vtool", "-show-build", str(executable))
    require("IOSSIMULATOR" in binary_platform, "Mach-O target is not the simulator.")
    (OUT / "binary-platform.txt").write_text(binary_platform + "\n")
    save(executable_sha256=digest(executable),
         bundle_version=info["CFBundleVersion"],
         bundle_short_version=info["CFBundleShortVersionString"])
    for bazel in (ROOT / "build-input").glob("bazel-*-darwin-arm64"):
        call(str(bazel), "shutdown", timeout=120)
    runtimes = json.loads(call("xcrun", "simctl", "list", "runtimes", "--json"))["runtimes"]
    runtimes = [r for r in runtimes if r.get("isAvailable")
                and r["identifier"].startswith("com.apple.CoreSimulator.SimRuntime.iOS-")]
    require(runtimes, "No available iOS simulator runtime.")
    save(available_simulator_runtimes=[{"name": r["name"], "version": r["version"]}
                                      for r in runtimes])
    types = json.loads(call("xcrun", "simctl", "list", "devicetypes", "--json"))["devicetypes"]
    types = [d for d in types if d["name"].startswith("iPhone")]
    require(types, "No iPhone simulator device type.")
    listed = json.loads(call("xcrun", "simctl", "list", "devices", "available", "--json"))["devices"]
    runtime, device_type = choose_simulator(runtimes, types, listed)
    save(smoke_phase="create_compatible_owned_simulator", simulator_model=device_type["name"],
         runtime_selection="listed compatible pair at or below selected SDK 27.1")
    device = call("xcrun", "simctl", "create",
                  "QuietPilot-" + os.environ["GITHUB_RUN_ID"],
                  device_type["identifier"], runtime["identifier"])
    require(re.fullmatch(r"[A-Fa-f0-9-]{36}", device), "Invalid owned simulator identifier.")
    save(owned_simulator=device, simulator_runtime=runtime["name"])
    try:
        save(smoke_phase="boot_owned_simulator")
        call("xcrun", "simctl", "boot", device)
        call("xcrun", "simctl", "bootstatus", device, "-b", timeout=300)
        save(smoke_phase="install_verified_app")
        try:
            call("xcrun", "simctl", "install", device, str(app), timeout=600)
        except RuntimeError:
            install_diagnostics(device)
            raise
        save(smoke_phase="launch_verified_app")
        launched = call("xcrun", "simctl", "launch", device, BUNDLE, timeout=120)
        pid = re.search(r":\s*(\d+)\s*$", launched)
        require(pid, "Simulator did not report an application PID.")
        started = call("ps", "-p", pid[1], "-o", "lstart=")
        time.sleep(10)
        os.kill(int(pid[1]), 0)
        require(call("ps", "-p", pid[1], "-o", "lstart=") == started,
                "Simulator application process identity changed.")
        call("xcrun", "simctl", "io", device, "screenshot", str(OUT / "welcome.png"))
        save(status="simulator_launch_passed", simulator_application_pid=int(pid[1]),
             screenshot="welcome.png", authenticated_features_tested=False)
    finally:
        cleanup = []
        for operation in ("shutdown", "delete"):
            try:
                result = subprocess.run(["xcrun", "simctl", operation, device],
                                        capture_output=True, timeout=90)
                cleanup.append({"operation": operation, "exit_code": result.returncode})
            except subprocess.TimeoutExpired:
                cleanup.append({"operation": operation, "timed_out": True})
        save(owned_simulator_cleanup=cleanup)
        shutil.rmtree(unpacked)


def finalize():
    report = save(workflow_status_so_far=os.environ.get("QUIET_JOB_STATUS", "unknown"))
    if report.get("status") not in ("failed", "simulator_launch_passed"):
        save(status="incomplete")
    lines = []
    for path in sorted(OUT.iterdir()):
        if path.is_file() and path.name != "SHA256SUMS.txt":
            lines.append(digest(path) + "  " + path.name)
    (OUT / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n")
    print(json.dumps({"report": str(REPORT), "files": len(lines)}))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("step", choices=["preflight", "build", "restore", "smoke", "finalize"])
    arguments = parser.parse_args()
    guard()
    OUT.mkdir(parents=True, exist_ok=True)
    try:
        globals()[arguments.step]()
    except (RuntimeError, subprocess.TimeoutExpired, OSError) as error:
        message = clean(str(error))
        save(status="failed", failed_step=arguments.step, error=message)
        print(message, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
