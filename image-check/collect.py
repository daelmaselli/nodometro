#!/usr/bin/env python3
"""Publish cached image checks; launch infrequent scans without blocking Telegraf."""
import argparse
from collections import Counter
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

CACHE = Path(os.environ.get("COMPOSE_IMAGE_CACHE", "/var/cache/compose-image-check"))
SETTINGS = Path(__file__).with_name("settings.json")
CODES = {"OK": 0, "PINNED": 1, "PULL": 2, "BUILD?": 3, "RECREATE": 4, "CHECK": 5}


def duration(value):
    match = re.fullmatch(r"([1-9][0-9]*)([smh])", value)
    if not match:
        raise argparse.ArgumentTypeError("use an interval such as 6h, 3h or 30m")
    return int(match[1]) * {"s": 1, "m": 60, "h": 3600}[match[2]]


def read_report():
    try:
        return json.loads((CACHE / "report.json").read_text())
    except (OSError, ValueError):
        return {"success": 0, "last_success": 0, "last_attempt": 0, "checks": [], "in_progress": False}


def write_report(report):
    temporary = CACHE / (".report-" + str(os.getpid()))
    with temporary.open("w") as stream:
        json.dump(report, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.chmod(0o600)
    os.replace(temporary, CACHE / "report.json")


def escape(value):
    value = re.sub(r"[\x00-\x1f\x7f]", " ", str(value))
    return value.replace("\\", "\\\\").replace(" ", "\\ ").replace(",", "\\,").replace("=", "\\=")


def encode(rows, checker):
    result = []
    occurrences = Counter()
    for status, name, image, reason, action in rows:
        if status not in CODES:
            raise ValueError("Unsupported checker status")
        project, service = name.split("/", 1)
        key = (name, image)
        check_id = str(occurrences[key]); occurrences[key] += 1
        result.append({"project": project, "service": service, "image": image,
                       "status": status, "check_id": check_id,
                       "reason": checker.safe_error(reason)[:360],
                       "registry_cached": "[CACHE" in reason})
    return result


def emit(report, interval):
    now = int(time.time())
    last = int(report.get("last_success", 0))
    success = int(report.get("success", 0))
    progress = bool(report.get("in_progress"))
    stale = now - last > interval + 3600
    state = 1 if progress else (2 if not success else (3 if stale else 0))
    print(f'compose_image_scan success={success}i,last_success_timestamp_seconds={last}i,refresh_interval_seconds={interval}i,in_progress={int(progress)}i,state_code={state}i,checks_total={len(report.get("checks", []))}i')
    for check in report.get("checks", []):
        status = check["status"]
        tags = {"compose_project": check["project"], "compose_service": check["service"],
                "compose_resource": check["project"] + "/" + check["service"],
                "image": check["image"], "check_id": check["check_id"],
                "status": status, "reason": check["reason"]}
        tag_text = ",".join(key + "=" + escape(value) for key, value in tags.items())
        # CHECK is attention/uncertainty, never a false OK or a definitive BUILD.
        attention = 0 if status in ("OK", "PINNED") else (2 if status == "CHECK" else 1)
        print(f'compose_image_check,{tag_text} status_code={CODES[status]}i,attention={attention}i,scan_timestamp_seconds={last}i,registry_cached={int(check["registry_cached"])}i')


class ScanTimeout(Exception):
    pass


def run_scan():
    with (CACHE / "scan.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        report = read_report()
        def timeout(signum, frame):
            raise ScanTimeout("image scan exceeded 30 minutes")
        signal.signal(signal.SIGALRM, timeout)
        signal.alarm(1800)
        try:
            from adapter import load_upstream, scan
            settings_file = SETTINGS if SETTINGS.is_file() else SETTINGS.with_name("settings.json.example")
            settings = json.loads(settings_file.read_text())
            rows, counters = scan(CACHE, settings)
            checker = load_upstream(CACHE)
            report.update(success=1, last_success=int(time.time()), checks=encode(rows, checker),
                          counters=counters, error="")
            print(f"Scan completed: {len(report['checks'])} checks", flush=True)
        except Exception as error:
            report.update(success=0, error="Scan failed: " + type(error).__name__)
            print(report["error"], file=sys.stderr, flush=True)
        finally:
            signal.alarm(0)
            report["in_progress"] = False
            write_report(report)


def publish(interval, force=False):
    CACHE.mkdir(mode=0o750, parents=True, exist_ok=True)
    with (CACHE / "launch.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        report = read_report()
        active = False
        with (CACHE / "scan.lock").open("a") as scan_lock:
            try:
                fcntl.flock(scan_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                active = True
        now = int(time.time())
        if report.get("in_progress") and not active and now - report.get("last_attempt", 0) > 90:
            report.update(success=0, in_progress=False, last_attempt=0, error="Previous scan interrupted")
        if not active and (force or now - report.get("last_attempt", 0) >= interval):
            report.update(in_progress=True, last_attempt=now)
            write_report(report)
            try:
                with (CACHE / "scan.log").open("w") as log:
                    os.chmod(CACHE / "scan.log", 0o600)
                    subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--scan"],
                                     stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                     start_new_session=True, close_fds=True,
                                     env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
            except OSError:
                report.update(success=0, in_progress=False, error="Unable to launch scan")
                write_report(report)
        emit(report, interval)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("interval", nargs="?", default="6h", type=duration)
    parser.add_argument("--scan", action="store_true")
    parser.add_argument("--refresh", action="store_true", help="rescan now, retaining upstream registry-cache policy")
    args = parser.parse_args()
    if args.scan:
        run_scan()
    else:
        publish(args.interval, force=args.refresh)


if __name__ == "__main__":
    main()
