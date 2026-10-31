# Nodometro Compose image collector

The independent `image-check` service is enabled by
`COMPOSE_PROFILES=docker,image-check,docker-prune` in `.env`.
The scheduler at `/app/collectors/run.py` invokes the original tool every 30s,
preserving scans every 6h, locks, the overall 30-minute timeout, registry cache
and interrupted scan handling. It does not use `inputs.exec`.

The adapter for **compose-image-check v1.8.0**, commit
`d3ae646417dc9d614623f6e907de9a5939ad1b15`, and vendored source in `upstream/`
retain their behavior. The adapter replaces Docker CLI calls with Engine API
GET requests; it does not pull, build or recreate applications.

## Preserved results

- PULL: published layers differ from the local tag, or the local tag is missing.
- RECREATE: the locally tagged image differs from a container/replica's image.
- BUILD?: comparison of FROM base layer prefixes; this is a hint.
- CHECK: comparison is inconclusive, never silently converted to OK.
- PINNED: explicit digest, without tracking tag movement.

The `compose_image_scan_*` and `compose_image_check_*` names, labels, reasons,
check_id and codes are preserved. Results expire after the scan interval plus
1h; an overall failure invalidates previous results. Dashboards and Grafana
checks require no changes.

Metric label values are a compatibility interface. The vendored tool retains
the legacy `[HEAD: digest invariato]` explanation (meaning "digest unchanged")
where it contributes to the `reason` label; translating it would change series
identity. Documentation and comments use English.

Registry cache behavior is unchanged: 6h without artificially renewing timestamps,
manifest HEAD, platform selection, OCI fallback, error redaction, response limits
and stopping Docker Hub requests after rate limiting. Docker Hub logical
operations are spaced 8s apart; other registries use 1s. The first scan may
therefore take several minutes.

## Configuration and permissions

The service uses UID/GID 65534 and the local socket GID from `.env`. It mounts
`${COMPOSE_ROOT:-/root/docker}` read-only at `/compose-host` and writes only to
its `image-cache` and `image-metrics` named volumes. Compose files are read for
build declarations: the tool does not read `.env`, `env_file`, secrets or
`~/.docker`, nor execute Dockerfile instructions. Private registries without
credentials remain CHECK. The Docker socket still grants privileged daemon access.

Code is included in the image. Compose mounts only `settings.json.example` as
the default configuration. To customize it:

```bash
cp image-check/settings.json.example image-check/settings.json
```

Add `IMAGE_CHECK_SETTINGS_FILE=./image-check/settings.json` to `.env`.
The local file is ignored by Git and image builds; it must be readable by UID
65534. A missing path causes an error rather than creating a directory.
Configuration supports explicit mappings:

```json
{
  "compose_root": "/compose-host",
  "host_roots": ["/root/docker", "/stage/docker", "/docker", "/opt/stacks"],
  "registry_cache": true,
  "builds": {
    "project/app": {"context": "project/src", "dockerfile": "project/src/Dockerfile"},
    "project/other": {"mode": "pull"}
  }
}
```

Paths are relative to `COMPOSE_ROOT`; each `host_roots` prefix must correspond
to the mounted directory. Physical path resolution and nested Dockge paths remain
supported. Symlinks outside the mount are rejected. Compose services never created
and one-off containers are not inventoried. ARG, multistage builds, inline
Dockerfiles and builds that cannot be reconstructed may remain CHECK.
Comparing rootfs.diff_ids does not detect all metadata-only changes.

A Compose file or Dockerfile unreadable by UID 65534 produces CHECK. Avoid
recursively changing application permissions; configure explicit mappings where
possible. Changes to files or permissions outside `/root/docker/telegraf` and
`/root/docker/grafana` require authorization on the central installation.

## Running and logs

```bash
docker compose logs --tail 100 -f image-check
docker compose exec image-check python3 /app/image-check/collect.py 6h --refresh
```

A refresh preserves registry cache policy; the scheduler publishes results by
the next cycle. Change `IMAGE_CHECK_INTERVAL` in `.env` and recreate the service
with `docker compose up -d` to change scan intervals. Setting
`registry_cache: false` forces more requests and may increase rate limiting.

The persistent report is `/var/cache/compose-image-check/report.json` in the
`image-cache` volume, mode 600. UTC messages tagged `[image-check]` with results
and explanations go to Docker stderr; credentials in messages are redacted.
`scan.log` is the fallback when the tool runs without `TELEGRAF_TOOL_LOG`.
Publishing cached results stays silent.

## Targets and inherited stages

`build.target` is respected, including the `target` field in local `builds`
mappings. For targets inheriting a parent stage, comparison follows the `FROM`
chain to the external base and uses the original layer check. Unreachable stages,
such as test stages, are not compared. Base references using `name:tag@digest`
are normalized for OCI APIs without tracking the tag.

Cross-stage dependencies (`COPY --from`, mounts from other stages), unresolved
ARGs and unknown targets remain CHECK; they are not declared current by exclusion.
The comparison of local tag and container image IDs still detects RECREATE.
Local collectors are compared against their external bases without looking up
`local/*` tags in a registry.
