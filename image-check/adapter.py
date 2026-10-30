"""Docker Engine API adapter for the pinned compose-image-check comparison engine."""
import importlib.util
import json
from pathlib import Path
import re
import socket
import http.client
import urllib.parse

import yaml
from tool_log import log


class Engine:
    def __init__(self, socket_path="/var/run/docker.sock"):
        self.socket_path = socket_path
        self.images = {}

    def get(self, path):
        connection = http.client.HTTPConnection("localhost", timeout=25)
        connection.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.sock.settimeout(25)
        connection.sock.connect(self.socket_path)
        try:
            connection.request("GET", path)
            response = connection.getresponse()
            content = response.read(32 * 1024 * 1024 + 1)
            if len(content) > 32 * 1024 * 1024:
                raise RuntimeError("Docker API response exceeds size limit")
            if response.status == 404:
                return None
            if response.status != 200:
                raise RuntimeError("Docker API returned HTTP " + str(response.status))
            return json.loads(content)
        finally:
            connection.close()

    def inspect_image(self, ref):
        if not ref:
            return None
        if ref not in self.images:
            self.images[ref] = self.get("/images/" + urllib.parse.quote(ref, safe="") + "/json")
        return self.images[ref]

    def services(self):
        groups = {}
        containers = self.get("/containers/json?all=1")
        if not isinstance(containers, list):
            raise RuntimeError("Docker container inventory is unavailable")
        for container in containers:
            labels = container.get("Labels") or {}
            project, service = labels.get("com.docker.compose.project"), labels.get("com.docker.compose.service")
            if not project or not service or labels.get("com.docker.compose.oneoff", "").lower() == "true":
                continue
            inspected = self.get("/containers/" + container["Id"] + "/json")
            if inspected:
                groups.setdefault((project, service), []).append(inspected)
        return groups


def load_upstream(cache):
    path = Path(__file__).parent / "upstream/compose-image-check.py"
    spec = importlib.util.spec_from_file_location("compose_image_check_upstream", path)
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    checker.CACHE_PATH = cache / "registry-v2.json"
    checker.OLD_CACHE_PATH = cache / "registry-v1.json"
    # Reuse the upstream OCI fallback, platform selection, throttling, HEAD and
    # authentication handling. No Docker CLI, Buildx or credential-store access.
    checker.buildx_available = lambda: False
    return checker


class BuildDiscovery:
    def __init__(self, settings):
        self.root = Path(settings.get("compose_root", "/compose-host")).resolve()
        configured_roots = settings.get("host_roots", [])
        self.host_roots = list(dict.fromkeys([
            *configured_roots, "/root/docker", "/stage/docker", "/opt/stacks"
        ]))
        self.overrides = settings.get("builds", {})
        self.projects = {}

    def mapped(self, value):
        path = Path(value)
        if not path.is_absolute():
            path = self.root / path
        elif not path.is_relative_to(self.root):
            for prefix in self.host_roots:
                if path.is_relative_to(prefix):
                    relative = path.relative_to(prefix)
                    path = self.root / relative
                    # Dockge records /opt/stacks/... in Docker labels, while
                    # the corresponding host directory may be nested anywhere
                    # below /root/docker. Accept a unique matching suffix.
                    if prefix == "/opt/stacks" and not path.exists():
                        suffix = relative.parts
                        matches = [candidate for candidate in self.root.rglob(relative.name)
                                   if candidate.parts[-len(suffix):] == suffix]
                        if len(matches) == 1:
                            path = matches[0]
                    break
            else:
                raise ValueError("path is outside the mounted Compose directory")
        path = path.resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("path escapes the mounted Compose directory")
        return path

    def discover(self, project, service, labels):
        override = self.overrides.get(project + "/" + service)
        if override is not None:
            if override.get("mode") == "pull":
                return None, self.root
            dockerfile = self.mapped(override["dockerfile"])
            context = self.mapped(override.get("context", str(dockerfile.parent)))
            return {"context": str(context), "dockerfile": str(dockerfile)}, context
        if project not in self.projects:
            files = [name.strip() for name in labels.get("com.docker.compose.project.config_files", "").split(",") if name.strip()]
            if not files:
                raise ValueError("Compose paths unknown; configure the build mapping explicitly")
            directory = self.mapped(labels.get("com.docker.compose.project.working_dir") or str(Path(files[0]).parent))
            services = {}
            for name in files:
                path = self.mapped(str(directory / name) if not Path(name).is_absolute() else name)
                # Parse declarations only. Never read env_file, secrets or .env.
                content = yaml.safe_load(path.read_text()) or {}
                if "include" in content:
                    raise ValueError("Compose include requires an explicit build mapping")
                for key, entry in (content.get("services") or {}).items():
                    entry = entry or {}
                    previous = services.setdefault(key, {})
                    if isinstance(previous.get("build"), dict) and isinstance(entry.get("build"), dict):
                        entry = dict(entry, build=dict(previous["build"], **entry["build"]))
                    previous.update(entry)
            self.projects[project] = (directory, services)
        directory, services = self.projects[project]
        if service not in services:
            raise ValueError("service declaration missing; configure the build mapping explicitly")
        entry = services[service]
        if entry.get("extends"):
            raise ValueError("Compose extends requires an explicit build mapping")
        build = entry.get("build")
        if not build:
            return None, directory
        build = {"context": build} if isinstance(build, str) else dict(build)
        if build.get("dockerfile_inline"):
            return build, directory
        for key in ("context", "dockerfile"):
            value = str(build.get(key, "." if key == "context" else "Dockerfile"))
            if "$" in value:
                raise ValueError("variable build path requires an explicit mapping; env files are not read")
            build[key] = value
        context = self.mapped(str(directory / build["context"]) if not Path(build["context"]).is_absolute() else build["context"])
        dockerfile = self.mapped(str(context / build["dockerfile"]) if not Path(build["dockerfile"]).is_absolute() else build["dockerfile"])
        return {"context": str(context), "dockerfile": str(dockerfile)}, directory


def scan(cache, settings):
    checker = load_upstream(cache)
    engine = Engine()
    original_row = checker.row
    def logged_row(status, name, ref, msg, action=""):
        original_row(status, name, ref, msg, action)
        level = "INFO" if status in ("OK", "PINNED") else "WARN"
        log(f"result service={name} status={status} image={ref} reason={msg}", level)
    checker.row = logged_row
    checker.inspect_image = engine.inspect_image
    checker.USE_DISK_CACHE = settings.get("registry_cache", True)
    discovery = BuildDiscovery(settings)
    checker.load_cache()
    try:
        groups = engine.services()
        log(f"inventory services={len(groups)} registry_cache={checker.USE_DISK_CACHE}")
        for (project, service), containers in sorted(groups.items()):
            name = project + "/" + service
            deployed = next((c for c in containers if c.get("State", {}).get("Running")), containers[0])
            labels = deployed.get("Config", {}).get("Labels") or {}
            ref = deployed.get("Config", {}).get("Image")
            log(f"checking service={name} image={ref}")
            if ref and "@sha256:" in ref:
                checker.row("PINNED", name, ref, "digest pinned; tag movement is not tracked")
                continue
            try:
                build, directory = discovery.discover(project, service, labels)
            except (OSError, ValueError, KeyError, yaml.YAMLError) as error:
                detail = checker.safe_error(str(error))[:180]
                reason = "build declaration cannot be determined"
                if detail:
                    reason += ": " + detail
                checker.row("CHECK", name, ref or "-", reason)
                continue
            local = engine.inspect_image(ref)
            local_id = (local or {}).get("Id")
            # Check all replicas, preferring an old deployed ID when mixed.
            running = next((c["Image"] for c in containers if local_id and c.get("Image") != local_id), deployed.get("Image"))
            if build:
                checker.check_build(name, build, ref, running, directory, project=project, service=service)
            elif not ref or re.fullmatch(r"(?:sha256:)?[a-f0-9]{64}", ref):
                checker.row("CHECK", name, ref or "-", "no mutable image reference is known")
            else:
                checker.check_pull(name, ref, running)
        return checker.results, dict(checker.metrics)
    finally:
        checker.save_cache()
