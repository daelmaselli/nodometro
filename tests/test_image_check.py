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

    def test_compose_target_and_explicit_target_mapping_are_preserved(self):
        (self.root / 'compose.yml').write_text('services:\n  app:\n    build:\n      context: .\n      target: production\n')
        discovery = adapter.BuildDiscovery({'compose_root': str(self.root), 'host_roots': ['/root/docker']})
        build, _ = discovery.discover('p', 'app', {'com.docker.compose.project.config_files': '/root/docker/compose.yml'})
        self.assertEqual(build['target'], 'production')
        discovery = adapter.BuildDiscovery({'compose_root': str(self.root),
            'builds': {'p/app': {'dockerfile': 'Dockerfile', 'target': 'custom'}}})
        build, _ = discovery.discover('p', 'app', {})
        self.assertEqual(build['target'], 'custom')

    def test_real_collectors_select_the_pinned_base_without_test_stage(self):
        expected = [('debian@sha256:99c0b25c46d0214d5a2091092e7908907dcaecd6a962cde0ac700acee222b6f3', '')]
        for target in ('docker-prune', 'image-check', 'zfs'):
            self.assertEqual(adapter.build_base(ROOT / 'Dockerfile', target), expected)
        with self.assertRaisesRegex(ValueError, 'cross-stage'):
            adapter.build_base(ROOT / 'Dockerfile', 'test')

    def test_target_build_keeps_ok_build_and_recreate_results(self):
        path = self.root / 'Dockerfile'
        path.write_text('FROM alpine:3.22 AS base\nFROM base AS production\nRUN true\nFROM unknown:latest AS test\n')
        build = {'context': str(self.root), 'dockerfile': str(path), 'target': 'production'}
        for expected, remote_layers, running in [('OK', ['base'], 'built'), ('BUILD?', ['new'], 'built'), ('RECREATE', ['base'], 'old')]:
            with self.subTest(status=expected):
                checker = self.checker()
                checker.inspect_image = lambda ref: ({'Id': 'built', 'Architecture': 'amd64', 'RootFS': {'Layers': ['base', 'app']}}
                                                     if ref == 'app:1' else {'Id': 'base', 'RootFS': {'Layers': ['base']}})
                with patch.object(checker, 'remote_config', return_value=({'rootfs': {'diff_ids': remote_layers}}, '')) as remote:
                    adapter.check_build(checker, 'p/app', build, 'app:1', running, self.root, 'p', 'app')
                self.assertEqual([row[0] for row in checker.results], [expected])
                if expected != 'RECREATE':
                    remote.assert_called_once_with('alpine:3.22', 'linux/amd64')
                else:
                    remote.assert_not_called()

    def test_unverifiable_dependencies_stay_check_and_never_query_unrelated_stages(self):
        for source, target in [
            ('FROM alpine AS builder\nFROM alpine AS app\nCOPY --from=builder /a /a\n', 'app'),
            ('ARG BASE\nFROM $BASE AS app\n', 'app'),
            ('FROM alpine AS app\nRUN --mount=from=other true\n', 'app'),
            ('FROM alpine AS app\n', 'missing'),
        ]:
            path = self.root / 'Dockerfile'; path.write_text(source)
            checker = self.checker()
            checker.inspect_image = lambda _: {'Id': 'built'}
            with patch.object(checker, 'remote_config', side_effect=AssertionError('must remain uncertain')):
                adapter.check_build(checker, 'p/app', {'dockerfile': str(path), 'target': target}, 'app:1', 'built', self.root, 'p', 'app')
            self.assertEqual([row[0] for row in checker.results], ['CHECK'])

    def test_unknown_build_still_detects_newly_built_image(self):
        path = self.root / 'Dockerfile'; path.write_text('FROM alpine AS app\nCOPY --from=other /a /a\n')
        checker = self.checker(); checker.inspect_image = lambda _: {'Id': 'new'}
        adapter.check_build(checker, 'p/app', {'dockerfile': str(path)}, 'app:1', 'old', self.root, 'p', 'app')
        self.assertEqual(checker.results[0][0], 'RECREATE')

    def test_digest_normalization_preserves_registry_port(self):
        path = self.root / 'Dockerfile'
        path.write_text('FROM registry.example:5000/team/base:tag@sha256:' + 'a'*64 + '\n')
        self.assertEqual(adapter.build_base(path), [('registry.example:5000/team/base@sha256:' + 'a'*64, '')])

    def test_physical_docker_root_works_with_existing_settings(self):
        project = self.root / "mysql"
        project.mkdir()
        compose = project / "docker-compose.yml"
        compose.write_text("services:\n  mysql:\n    image: mysql:5.7\n")
        # Existing node settings do not need to be replaced on upgrade.
        discovery = adapter.BuildDiscovery({"compose_root": str(self.root), "host_roots": ["/root/docker"]})
        build, directory = discovery.discover("mysql", "mysql", {
            "com.docker.compose.project.config_files": "/docker/mysql/docker-compose.yml",
            "com.docker.compose.project.working_dir": "/docker/mysql",
        })
        self.assertIsNone(build)
        self.assertEqual(directory, project)

    def test_resolved_docker_symlink_target_works_with_existing_settings(self):
        project = self.root / "www"
        project.mkdir()
        compose = project / "docker-compose.yml"
        compose.write_text("services:\n  php84:\n    build: .\n")
        (project / "Dockerfile").write_text("FROM php:8.4-apache\n")
        # COMPOSE_ROOT provides the one mounted physical path to the adapter.
        with patch.dict(os.environ, {"IMAGE_CHECK_HOST_COMPOSE_ROOT": "/data/docker"}):
            discovery = adapter.BuildDiscovery({"compose_root": str(self.root), "host_roots": ["/root/docker"]})
            build, directory = discovery.discover("www", "php84", {
                "com.docker.compose.project.config_files": "/data/docker/www/docker-compose.yml",
                "com.docker.compose.project.working_dir": "/data/docker/www",
            })
        self.assertEqual(build["dockerfile"], str(project / "Dockerfile"))
        self.assertEqual(directory, project)

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
