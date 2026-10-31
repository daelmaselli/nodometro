# Linux monitoring with Docker Compose

One stack for system, Docker, image and OpenZFS metrics. No additional host
packages or schedulers are required. Custom collectors reuse the existing tools
without `inputs.exec`. The main Telegraf agent has no Docker socket, Docker group,
ZFS device or Linux capabilities. There is no initialization service.

## Installation

Requirements: Docker Engine with Compose v2 supporting `pull_policy: build`, Git
and access to the Git repository. Addresses ending in `.example` are placeholders:
replace the Git URL and VictoriaMetrics endpoint with those of your installation.
On a new node:

```bash
git clone 'git@code.example:team/telegraf.git' /root/docker/telegraf
cd /root/docker/telegraf
cp .env.example .env
stat -c '%g' /var/run/docker.sock
```

In `.env`, set the VictoriaMetrics URL and the GID reported **on the local node**.
The URL must also be reachable from containers on the bridge network: `localhost`
refers to the container, not the host. `COMPOSE_ROOT` locates the stacks to inspect.

```dotenv
VICTORIAMETRICS_URL=http://victoriametrics.example:8428/api/v1/write
DOCKER_GID=REPLACE_WITH_LOCAL_SOCKET_GID
COMPOSE_PROFILES=docker,image-check,docker-prune
COMPOSE_ROOT=/root/docker
```

Set `COMPOSE_PROFILES=` for standard metrics only. Add `zfs` only where OpenZFS
is working and `/dev/zfs` is available. See [ZFS](zfs/README.md) for optional
ARC/kstat metrics. `telegraf.d/` is reserved for local customization: standard
monitoring requires no additional configuration files or Compose overrides.

```bash
docker compose config --quiet
docker compose up -d
docker compose ps -a
```

Compose automatically builds enabled collector images, using the build cache.
The base images are Telegraf 1.37.3 and Debian bookworm pinned by multiarch digest.
No image uses `latest`. Builds require access to Debian repositories; packages
are not pinned, so a build without cache may update them.

## Updates

**For the first upgrade from an earlier version, follow
[migration and rollback](docs/upgrade.md)**: state mounts and image-check
configuration mounts have changed. For subsequent updates over Git SSH:

```bash
cd /root/docker/telegraf
git pull --ff-only && docker compose up -d --build --remove-orphans
```

Collector code is included in the images; rebuilding applies code updates.
`docker compose pull` cannot distribute custom code until the custom images are
published to a registry. Official images have explicit versions. To refresh the
same tag separately, run `docker compose pull telegraf docker` (omit `docker` if
that profile is disabled).

If only mounted Telegraf configuration files change, also schedule
`docker compose restart telegraf` or `docker compose restart docker`, depending
on the file: a bind-mounted file change alone does not trigger recreation.

## Services and configuration

| Service / profile | Collection | Specific access |
| --- | --- | --- |
| `telegraf` / always | System metrics every 30s by default; reads `.prom` files and sends Remote Write | Read-only host proc/sys and filesystems; host network |
| `docker` / `docker` | Native Telegraf Docker plugin and inventory every 30s by default, direct Remote Write | Docker socket |
| `image-check` / `image-check` | Scan every 6h, cached results published every 30s | Socket, read-only Compose/Dockerfiles, registry HTTPS |
| `docker-prune` / `docker-prune` | Reclaimable space every 3h, cached results every 30s; deletes nothing | Socket, no IP network |
| `zfs` / `zfs` | Pools/vdevs every 60s, datasets every 300s | Only `/dev/zfs`, no IP network |

All services use UID/GID 65534, `cap_drop: ALL`, a read-only root filesystem and
`no-new-privileges`. Mounting a socket with `:ro` does not restrict API methods:
services receiving it have access to the daemon. Host filesystem permissions
still apply to Telegraf.

`.env`, `image-check/settings.json`, `telegraf.d/*.conf` and the Compose override
are local files, excluded from Git and image build contexts. On new nodes,
image-check mounts only `settings.json.example`; source code is not mounted.
See [image-check](image-check/README.md) for local configuration.

Optional `.env` settings:

| Variable | Default | Purpose |
| --- | --- | --- |
| `TELEGRAF_INTERVAL` | `30s` | System/Docker collection and `.prom` reads |
| `TELEGRAF_FLUSH_INTERVAL` | `30s` | Flush buffered samples from both agents |
| `DOCKER_TELEGRAF_INTERVAL` | Inherits `TELEGRAF_INTERVAL` | Docker service collection only |
| `DOCKER_TELEGRAF_FLUSH_INTERVAL` | Inherits `TELEGRAF_FLUSH_INTERVAL` | Docker service flush only |
| `IMAGE_CHECK_INTERVAL` | `6h` | Image scan interval |
| `DOCKER_PRUNE_INTERVAL` | `3h` | Reclaimable space calculation interval |
| `IMAGE_CHECK_SETTINGS_FILE` | `./image-check/settings.json.example` | Local image-check settings file |

## Collection and flush intervals

All stack options are documented in `.env.example`. To reduce the number of
standard samples collected and stored, start with:

```dotenv
TELEGRAF_INTERVAL=60s
TELEGRAF_FLUSH_INTERVAL=30s
```

These shared values also apply to the `docker` service unless overridden with
`DOCKER_TELEGRAF_*`. Increasing only `TELEGRAF_FLUSH_INTERVAL` groups samples
without reducing their number; batch limits may still split them into multiple
HTTP requests. The effect on load therefore depends on the bottleneck.

If dashboards and alerts use a 120s data freshness threshold, leave room for
collection, buffering and network delays. Longer intervals require adjusting
those thresholds; no threshold is changed automatically. Custom collectors keep
their own scheduling and health checks: changing these Telegraf values does not
change image/prune scans or ZFS collection intervals.

An input in `telegraf.d/` with an explicit `interval` overrides the agent setting.
The new ARC/kstat example inherits the agent interval; remove `interval = "30s"`
from older local copies if you want the same inheritance.

Apply `.env` changes during an approved restart window:

```bash
docker compose up -d --no-deps --no-build --pull never telegraf docker
```

Omit `docker` on nodes that do not use it. `up` recreates services when their
environment changes. If only mounted `.conf` files change, also run
`docker compose restart telegraf docker`. Keep the destination, GID and
node-specific settings in the local `.env` rather than replacing it with the example.

## State, checks and removal

Compose manages five named volumes: `image-cache`, `prune-cache`, `image-metrics`,
`prune-metrics` and `zfs-metrics`. Actual names have the Compose project prefix.
Each collector writes only its own volumes. Telegraf mounts metrics read-only
without initializing them. Images prepare permissions at build time, so startup
does not require root.

Atomic `.prom` snapshots retain acquisition timestamps: reading an old snapshot
does not make a stopped collector look fresh. Metric names, labels and tool
intervals remain compatible with historical data.

```bash
docker compose ps -a
docker compose logs --tail 100
```

Collectors should become `healthy` after their first publication. The first
image scan may take several minutes. See [image-check](image-check/README.md)
for reports and refreshes. The [tests](docs/upgrade.md#isolated-checks) also compare
real Remote Write payloads without sending them to VictoriaMetrics.

```bash
docker compose --profile '*' down
```

This removes all stack services and its network, preserving caches and metrics.
To delete **all five volumes and their contents as well**, use
`docker compose --profile '*' down -v`. Local configuration, backups and the old
`state/` directory are not deleted. No host service or timer is left installed.

To disable an active collector, stop and remove it with
`docker compose rm -sf SERVICE_NAME` before removing its profile from `.env`.
Changing the profile alone does not guarantee that Compose removes its container.

## Repository

Use a shared Git repository to distribute code and example configuration.
Each node keeps its own local configuration ignored by Git. The original analysis
is in [docs/analysis.md](docs/analysis.md); the first migration is summarized in
[the migration history](docs/migration.md). Before making the repository public,
review Git history, secrets, internal references and vendored source licenses.

Distributable archives must be exported from **committed HEAD**, excluding
`.git/`, local configuration and data. Avoid separate competing template trees.

Reviewing the working tree does not remove data from earlier commits: review
Git history separately before publishing it.

## License

Project source code and documentation are available under the [MIT License](LICENSE),
including the vendored Compose Image Check tool, which also carries a
[local copy of the license](image-check/upstream/LICENSE).
Telegraf, OpenZFS, base images and other third-party dependencies retain their
own licenses.
