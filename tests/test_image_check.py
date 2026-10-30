import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "image-check"
sys.path.insert(0, str(MODULE))
import adapter
import collect


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def test_build_discovery_does_not_read_application_env(self):
        project = self.root / "app"; project.mkdir()
        (project / "docker-compose.yml").write_text('services:\n  app:\n    build: .\n    env_file: ./secret.env\n')
        (project / "secret.env").write_text("NOT_FOR_THE_CHECKER=private")
        (project / "Dockerfile").write_text("FROM alpine:3.22\n")
        discovery = adapter.BuildDiscovery({"compose_root": str(self.root), "host_roots": ["/root/docker"]})
        original = Path.read_text
        def guarded(path, *args, **kwargs):
            if path.name == "secret.env":
                raise AssertionError("application env must not be opened")
            return original(path, *args, **kwargs)
        with patch.object(Path, "read_text", guarded):
            build, _ = discovery.discover("app", "app", {"com.docker.compose.project.config_files": "/root/docker/app/docker-compose.yml"})
        self.assertEqual(build["dockerfile"], str(project / "Dockerfile"))

    def test_explicit_mapping_needs_no_compose_file(self):
        (self.root / "Dockerfile").write_text("FROM scratch\n")
        d = adapter.BuildDiscovery({"compose_root": str(self.root), "builds": {"p/s": {"dockerfile": "Dockerfile"}}})
        build, _ = d.discover("p", "s", {})
        self.assertEqual(build["dockerfile"], str(self.root / "Dockerfile"))

    def test_symlink_escape_is_rejected(self):
        (self.root / "outside").symlink_to("/etc")
        d = adapter.BuildDiscovery({"compose_root": str(self.root)})
        with self.assertRaises(ValueError):
            d.mapped("outside/passwd")

    def checker(self):
        c = adapter.load_upstream(self.root)
        c.cmd = lambda *args, **kwargs: self.fail("comparison must not execute a CLI command")
        return c

    def test_recreate_uses_image_ids_and_platform(self):
        c = self.checker()
        c.inspect_image = lambda ref: {"Id": ref, "RootFS": {"Layers": ["a"]}, "Os": "linux", "Architecture": "arm", "Variant": "v7"}
        with patch.object(c, "remote_config", return_value=({"rootfs": {"diff_ids": ["a"]}}, "")) as remote:
            c.check_pull("p/s", "local", "old")
        self.assertEqual(c.results[0][0], "RECREATE")
        remote.assert_called_once_with("local", "linux/arm/v7")

    def test_pull_compares_layers_not_tags(self):
        c = self.checker()
        c.inspect_image = lambda ref: {"Id": "same", "RootFS": {"Layers": ["old"]}, "Architecture": "amd64"}
        c.remote_config = lambda ref, platform: ({"rootfs": {"diff_ids": ["new"]}}, "")
        c.check_pull("p/s", "same-tag", "same")
        self.assertEqual(c.results[0][0], "PULL")

    def test_build_hint_is_not_a_definitive_build_requirement(self):
        c = self.checker(); (self.root / "Dockerfile").write_text("FROM base:tag\n")
        images = {"built": {"Id": "built", "Architecture": "amd64", "RootFS": {"Layers": ["old", "app"]}}, "base:tag": {"RootFS": {"Layers": ["old"]}}}
        c.inspect_image = images.get
        c.remote_config = lambda ref, platform: ({"rootfs": {"diff_ids": ["new"]}}, "")
        c.check_build("p/s", {"context": str(self.root)}, "built", "built", self.root)
        self.assertEqual(c.results[0][0], "BUILD?")

    def test_arg_and_multistage_remain_uncertain(self):
        c = self.checker(); (self.root / "Dockerfile").write_text("ARG BASE\nFROM ${BASE}\n")
        c.inspect_image = lambda ref: {"Id": "built", "Architecture": "amd64", "RootFS": {"Layers": ["a"]}}
        c.check_build("p/s", ".", "built", "built", self.root)
        self.assertEqual(c.results[0][0], "CHECK")

    def test_rate_limit_stops_other_docker_hub_requests(self):
        c = self.checker(); c.throttle = lambda *args: None
        with patch.object(c, "manifest_head", return_value=(None, "RATE_LIMIT")) as head:
            a = c.remote_config("alpine:latest", "linux/amd64")
            b = c.remote_config("busybox:latest", "linux/amd64")
        self.assertIsNone(a[0]); self.assertIsNone(b[0]); self.assertEqual(head.call_count, 1)

    def test_pinned_reference_needs_no_compose_or_registry_access(self):
        c = self.checker()
        container = {"Image": "id", "Config": {"Image": "example@sha256:" + "a"*64, "Labels": {}}, "State": {"Running": True}}
        with patch.object(adapter, "load_upstream", return_value=c), patch.object(adapter.Engine, "services", return_value={("p", "s"): [container]}), patch.object(adapter.BuildDiscovery, "discover", side_effect=AssertionError("Compose must not be read")), patch.object(c, "remote_config", side_effect=AssertionError("Registry must not be queried")):
            with contextlib.redirect_stdout(io.StringIO()):
                rows, _ = adapter.scan(self.root, {"compose_root": str(self.root)})
        self.assertEqual(rows[0][0], "PINNED")

    def test_head_unchanged_reuses_layers_without_metadata_download(self):
        c = self.checker(); c.throttle = lambda *args: None
        ref, platform = "alpine:latest", "linux/amd64"
        c.disk_cache[c.cache_key(ref, platform)] = {"checked": time.time()-c.CACHE_TTL-1, "layers": ["known"], "manifest_digest": "sha256:"+"a"*64}
        with patch.object(c, "manifest_head", return_value=("sha256:"+"a"*64, "")), patch.object(c, "oci_config", side_effect=AssertionError("Config must not be downloaded")):
            result, reason = c.remote_config(ref, platform)
        self.assertEqual(result["rootfs"]["diff_ids"], ["known"])
        self.assertEqual(reason, "HEAD_OK")

    def test_cache_does_not_extend_its_original_timestamp(self):
        c = self.checker(); checked = time.time() - 100
        key = c.cache_key("alpine:latest", "linux/amd64")
        c.disk_cache[key] = {"checked": checked, "layers": ["a"]}
        self.assertIsNotNone(c.cached_layers("alpine:latest", "linux/amd64"))
        self.assertEqual(c.disk_cache[key]["checked"], checked)
        c.disk_cache[key]["checked"] = time.time() - c.CACHE_TTL - 1
        self.assertIsNone(c.cached_layers("alpine:latest", "linux/amd64"))

    def test_cached_publish_does_not_launch_a_new_scan(self):
        report = {"success": 1, "last_success": int(time.time()), "last_attempt": int(time.time()), "checks": []}
        with patch.object(collect, "CACHE", self.root):
            collect.write_report(report)
            with patch.object(collect.subprocess, "Popen") as process, contextlib.redirect_stdout(io.StringIO()) as output:
                collect.publish(21600)
            process.assert_not_called()
        self.assertIn("success=1i", output.getvalue())

    def test_failed_scan_invalidates_old_results(self):
        with patch.object(collect, "CACHE", self.root):
            collect.write_report({"success": 1, "last_success": 123, "checks": [{"status": "OK"}], "in_progress": True})
            with patch.object(adapter, "scan", side_effect=TimeoutError):
                # Settings are read before invoking scan.
                with patch.object(collect, "SETTINGS", MODULE / "settings.json"):
                    collect.run_scan()
            report = collect.read_report()
        self.assertEqual(report["success"], 0)
        self.assertEqual(report["last_success"], 123)
        self.assertFalse(report["in_progress"])


if __name__ == "__main__":
    unittest.main()
