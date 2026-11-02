# Analysis of the original implementation

The previous implementation used a single `telegraf` service, a custom Dockerfile
and Telegraf 1.37.3. This document records the decisions behind separating the
services. See [migration and rollback](upgrade.md) for the current procedure.

## Dependencies and intervals

| Original component | Interval / timeout | Dependencies and state |
| --- | --- | --- |
| CPU, mem, swap, system, disk, diskio, net | Collection and flush every 30s | proc/sys, host mounts, host network, dev/udev for diskio |
| `inputs.docker` | 30s / 10s | Docker socket; container resources, Compose tags; storage_objects disabled |
| `inputs.http` Docker inventory | 30s / 10s | GET `/containers/json?all=1`, state/health normalized with Starlark |
| image-check `inputs.exec` | Publication 30s / 15s; scan 6h, limit 1800s | Python 3, PyYAML, API adapter, upstream v1.8.0, socket, registry HTTPS, read-only Compose/Dockerfiles, cache volume |
| prune `inputs.exec` | Publication 30s / 75s; scan 3h; curl 10s + 60s; lock 70s | bash, curl, Python JSON parser, negotiated Docker API (capped at 1.51) `/system/df`, persistent cache, Starlark parser |
| Local native ZFS input | 30s | `/proc/spl/kstat/zfs`, native types and poolMetrics |
| Local ZFS pools `inputs.exec` | 60s / 20s | bash, awk, zpool list/status, `/dev/zfs` |
| Local ZFS datasets `inputs.exec` | 300s / 30s | bash, awk, zfs list, `/dev/zfs` |

The previous Dockerfile combined Telegraf 1.37, Python, PyYAML and zfsutils-linux
in one image. The separate targets retain Debian bookworm to limit dependency
changes. Compatibility between container ZFS tools and the kernel module must
be checked on the destination node.

The single service used UID/GID 65534, the socket's supplementary group,
`no-new-privileges`, a read-only filesystem and host network/UTS. A local override
added `/dev/zfs`. A read-only socket mount does not prevent API writes; separating
the services removes this access from the main agent.

## Compatibility requirements

- Keep all standard inputs and their exclusions unchanged.
- Derive the hostname from `uts: host`, preserving the node's `host` label.
- Preserve the Docker/HTTP inputs, Compose tag renaming and Starlark
  state_code/health_code/container_health processing in the Docker service.
- Continue excluding the `container_id` and `health_status` string fields, which
  Remote Write cannot encode. Keep stopped containers and other states inventoried.
- Preserve image-check codes, reasons, check_id, redacted logs, registry cache,
  locks and background workers. The independent scheduler invokes the same tool.
- Negotiate the Docker daemon's API version for prune, capped at 1.51 to avoid
  differences in reported reclaimable space from newer API responses. No prune
  operation is performed.
  Port the Starlark calculation to Python unchanged and compare it with the
  previous parser using real Remote Write payloads.
- With prune `success=0`, retain the previous timestamp and emit **zero** sizes
  as before; do not present old byte counts as valid results.
- Keep `zfs/collect.sh` unchanged, including labels and collected ZFS properties.
- Match Telegraf's Influx parser label escaping, including backslash handling;
  binary payload comparisons detect differences.

## Incompatibilities and decisions

1. **Old overrides and local inputs:** earlier installations may assign ZFS to
   Telegraf and use exec in `telegraf.d/40-zfs.conf`. A Git pull alone is not
   sufficient: review and replace them during cutover after taking a backup.
   Review other local files for socket/exec/privilege settings as well.
2. **Caches:** preserve old volumes and state for rollback. The current version
   uses new named volumes and allows starting with empty caches; old data is not
   deleted automatically.
3. **Profiles:** an existing `.env` may lack COMPOSE_PROFILES. Without it, only
   system metrics are collected. ZFS requires the actual device.
4. **Timestamps:** Telegraf reads `.prom` with `prometheus_metric_version=2` and
   preserves acquisition timestamps. The Remote Write serializer recognizes the
   `prometheus` measurement without adding prefixes. No extra `file`, `url`,
   `instance` or `job` labels are added. Actual before/after comparisons cover this.
5. **Scheduler failures:** an incomplete snapshot never replaces a valid one.
   A stopped process leaves old timestamps rather than fresh samples; freshness
   and staleness then follow VictoriaMetrics/Grafana behavior.
6. **Native Docker collection:** a dedicated Telegraf service sends Remote Write
   directly. This avoids rewriting plugins or adding exporters/socket proxies.
   The main agent does not duplicate this collection.
7. **Networking:** the main agent retains host networking to observe node
   interfaces. Native Docker collection and image-check use the Compose network,
   so the VM endpoint must be reachable and resolvable there too. `localhost`
   identifies the container; use the node's name/IP. Prune and ZFS use network none.
8. **Privileges:** all permanent services use UID/GID 65534 and cap_drop ALL.
   Only ZFS receives its device; only Docker-related services receive the socket
   and its group. The first separated version used an init to prepare `state/`;
   named volume permissions are now prepared in the images at build time.
9. **Architectures:** the Debian base uses a multiarch digest. Check ZFS userspace
   compatibility and platform support on each destination node.
10. **Distribution:** without a registry, Git and `pull_policy: build` support
    updates through Git pull and Compose up with build. Pulling an image alone
    cannot distribute unpublished Git code.
11. **Mounted files:** changing a Telegraf configuration does not always trigger
    recreation/reloading. Plan a targeted restart when only mounted files change.
12. **Container identity:** splitting the service creates new containers and
    Compose names, so their inventory changes accordingly. Historical series for
    existing monitored services, hosts, pools and datasets retain names and labels.

No monitoring component is installed on the host. Dashboards can continue using
the same metrics and labels. The inventory of expected resources remains part
of the visualization and alerting system's configuration.

Format references: [Telegraf Prometheus parser](https://docs.influxdata.com/telegraf/v1/data_formats/input/prometheus/),
[Remote Write serializer](https://docs.influxdata.com/telegraf/v1/data_formats/output/prometheusremotewrite/),
[Compose build/pull policy](https://docs.docker.com/reference/compose-file/build/).

## Compatibility checks

The suite compares real Remote Write payloads using an isolated receiver:
names, labels, values, timestamps, escaping and Docker calculation compatibility.
It covers startup without snapshots, tool errors, retention of the last valid
samples and agent interval settings. Reproducible instructions are in
[upgrade.md](upgrade.md#isolated-checks). Keep real node results, inventories and
logs out of Git.
