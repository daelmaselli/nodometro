#!/usr/bin/env python3
"""Schedule existing tools and publish atomic Prometheus snapshots; no Telegraf exec."""
import argparse
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time

from prune import fields as prune_fields

ROOT = Path(__file__).resolve().parents[1]
STOP = threading.Event()


def split_escaped(text, separator, limit=-1):
    parts, start, escaped = [], 0, False
    for index, char in enumerate(text):
        if char == "\\":
            escaped = True
        elif char == separator and not escaped and (limit < 0 or len(parts) < limit):
            parts.append(text[start:index])
            start = index + 1
        else:
            escaped = False
    parts.append(text[start:])
    return parts


def unescape(value):
    # Match Telegraf's Influx parser: backslashes themselves remain literal.
    return re.sub(r"\\([ ,=])", r"\1", value)


def influx_rows(text):
    """Parse the numeric line-protocol subset emitted by image-check and ZFS.

    Do not implement a general Influx parser: unsupported output fails the
    snapshot rather than silently changing the metric contract.
    """
    for line in text.splitlines():
        if not line.strip():
            continue
        series, values = split_escaped(line, " ", 1)
        measurement, *tags = split_escaped(series, ",")
        labels = {}
        for tag in tags:
            key, value = split_escaped(tag, "=", 1)
            labels[unescape(key)] = unescape(value)
        for field in values.split(","):
            key, value = field.split("=", 1)
            if value.endswith(("i", "u")):
                value = str(int(value[:-1]))
            elif value.lower() in ("true", "false"):
                value = "1" if value.lower() == "true" else "0"
            elif not math.isfinite(float(value)):
                raise ValueError("non-finite tool output")
            yield unescape(measurement) + "_" + unescape(key), labels, value


def prometheus(rows, timestamp_ms):
    def escape(value):
        return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')
    lines = []
    for name, labels, value in rows:
        if not re.fullmatch(r"[a-zA-Z_:][a-zA-Z0-9_:]*", name):
            raise ValueError("invalid metric name")
        if any(not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", key) for key in labels):
            raise ValueError("invalid label name")
        tags = ",".join(f'{key}="{escape(value)}"' for key, value in sorted(labels.items()))
        lines.append(f"{name}" + ("{" + tags + "}" if tags else "") + f" {value} {timestamp_ms}\n")
    return "".join(lines)


def publish(path, content):
    temporary = path.with_suffix(".tmp")
    try:
        with temporary.open("w") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(0o644)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def execute(command, timeout):
    # A separate group lets timeout/stop terminate bash, curl and zfs together.
    with subprocess.Popen(command, stdout=subprocess.PIPE, text=True,
                          start_new_session=True) as process:
        deadline = time.monotonic() + timeout
        while True:
            try:
                output, _ = process.communicate(timeout=0.5)
                if process.returncode:
                    raise subprocess.CalledProcessError(process.returncode, command)
                return output
            except subprocess.TimeoutExpired:
                if STOP.is_set() or time.monotonic() >= deadline:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.communicate()
                    raise TimeoutError("tool stopped or timed out")


def jobs(collector):
    if collector == "image-check":
        return [("images", 30, 15, [sys.executable, str(ROOT / "image-check/collect.py"),
                                    os.environ.get("IMAGE_CHECK_INTERVAL", "6h")])]
    if collector == "docker-prune":
        return [("prune", 30, 75, ["/bin/bash", str(ROOT / "docker-prune/collect.sh"),
                                  os.environ.get("DOCKER_PRUNE_INTERVAL", "3h")])]
    return [("pools", 60, 20, ["/bin/bash", str(ROOT / "zfs/collect.sh"), "pools"]),
            ("datasets", 300, 30, ["/bin/bash", str(ROOT / "zfs/collect.sh"), "datasets"])]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("collector", choices=["image-check", "docker-prune", "zfs"])
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, lambda *_: STOP.set())
    signal.signal(signal.SIGINT, lambda *_: STOP.set())
    output = Path(os.environ.get("METRICS_DIRECTORY", "/metrics"))
    scheduled = [(job, 0) for job in jobs(args.collector)]
    while not STOP.is_set():
        for index, (job, due) in enumerate(scheduled):
            if STOP.is_set():
                break
            name, interval, timeout, command = job
            now = time.monotonic()
            if now < due:
                continue
            try:
                result = execute(command, timeout)
                if args.collector == "docker-prune":
                    rows = [("docker_prune_" + key, {"source": "docker-prune-monitoring"}, value)
                            for key, value in prune_fields(json.loads(result)).items()]
                else:
                    rows = influx_rows(result)
                publish(output / (name + ".prom"), prometheus(rows, time.time_ns() // 1_000_000))
            except Exception as error:
                # Preserve the previous snapshot AND its acquisition timestamp.
                # Tool stderr supplies diagnostics without disclosing output labels.
                print(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} ERROR "
                      f"[{args.collector}] {name}: {type(error).__name__}; snapshot unchanged", file=sys.stderr, flush=True)
            scheduled[index] = (job, now + interval)
        STOP.wait(max(0.1, min(due for _, due in scheduled) - time.monotonic()))


if __name__ == "__main__":
    main()
