"""Collector diagnostics, separate from metric stdout."""
import datetime
import os
import re
import sys


def log(message, level="INFO"):
    message = str(message)
    message = re.sub(r"(?i)(https?://)[^\s/@]+:[^\s/@]+@", r"\1[credentials]@", message)
    message = re.sub(r"(?i)(token|password|passwd|secret|authorization|api[_-]?key)([=:\s]+)([^\s]+)", r"\1\2[redacted]", message)
    message = re.sub(r"(?i)([?&](?:token|key|signature|access_token|auth)=)[^&\s]+", r"\1[redacted]", message)
    message = re.sub(r"[\x00-\x1f\x7f]", " ", message)[:2000]
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"{timestamp} {level} [image-check] {message}\n"
    target = os.environ.get("TELEGRAF_TOOL_LOG")
    if target:
        try:
            with open(target, "a") as stream:
                stream.write(line)
            return
        except OSError:
            pass
    sys.stderr.write(line)
    sys.stderr.flush()
