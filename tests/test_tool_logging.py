import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "image-check"))
import collect
import tool_log


class LoggingTests(unittest.TestCase):
    def test_log_routes_to_container_stream_without_polluting_metrics(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "docker.log"
            with patch.dict(os.environ, {"TELEGRAF_TOOL_LOG": str(target)}), contextlib.redirect_stdout(io.StringIO()) as stdout:
                tool_log.log("checking service=p/s")
                tool_log.log("result status=OK")
            lines = target.read_text().splitlines()
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(len(lines), 2)
        self.assertIn("INFO [image-check]", lines[0])

    def test_log_fallback_and_redaction(self):
        with patch.dict(os.environ, {"TELEGRAF_TOOL_LOG": "/nonexistent/log"}), contextlib.redirect_stderr(io.StringIO()) as stderr:
            tool_log.log("https://user:private@example.org/?token=hidden password=secretvalue\nforged line", "ERROR")
        output = stderr.getvalue()
        for secret in ("private", "hidden", "secretvalue"):
            self.assertNotIn(secret, output)
        self.assertEqual(len(output.splitlines()), 1)
        self.assertIn("ERROR [image-check]", output)

    def test_background_worker_receives_docker_stream(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "docker.log"
            with patch.object(collect, "CACHE", root), patch.dict(os.environ, {"TELEGRAF_TOOL_LOG": str(target)}), patch.object(collect.subprocess, "Popen") as process, contextlib.redirect_stdout(io.StringIO()) as output:
                collect.publish(21600)
            kwargs = process.call_args.kwargs
            self.assertEqual(kwargs["stdout"].name, str(target))
            self.assertIs(kwargs["stderr"], kwargs["stdout"])
            self.assertIn("compose_image_scan", output.getvalue())
            self.assertNotIn("scan launched", output.getvalue())
            self.assertIn("scan launched", target.read_text())

    def test_prune_success_cache_and_failure_keep_json_stdout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            curl = root / "curl"
            curl.write_text("""#!/bin/bash\nif [[ \"${FAIL_SCAN:-0}\" == 1 ]]; then exit 22; fi\nwhile [[ \"$1\" != --output ]]; do shift; done\nprintf '{\"Images\": []}\\n' > \"$2\"\n""")
            curl.chmod(0o755)
            env = dict(os.environ, PATH=str(root) + ":" + os.environ["PATH"], DOCKER_PRUNE_CACHE_DIR=str(root), TELEGRAF_TOOL_LOG="")
            def run():
                return subprocess.run(["bash", str(ROOT / "docker-prune/collect.sh"), "3h"], env=env, capture_output=True, text=True, check=True)
            first = run()
            self.assertEqual(json.loads(first.stdout)["collection_success"], 1)
            self.assertIn("scan started", first.stderr)
            self.assertIn("scan completed", first.stderr)
            cached = run()
            self.assertEqual(cached.stderr, "")
            (root / "last-attempt").write_text("0")
            env["FAIL_SCAN"] = "1"
            failed = run()
            report = json.loads(failed.stdout)
            self.assertEqual(report["collection_success"], 0)
            self.assertEqual(report["disk_usage"], {"Images": []})
            self.assertIn("ERROR [docker-prune] scan failed curl_exit=22", failed.stderr)


if __name__ == "__main__":
    unittest.main()
