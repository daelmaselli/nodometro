# Nodometro migration to named volumes and rollback

This version removes `state-init`, uses five named volumes and includes collector
code in `local/telegraf-*:2.1.0` images. Metrics, labels and scheduling are
unchanged. The 2.0.0 images and previous `state/` directory are not deleted.
The procedure allows starting with empty caches.

The project is now called Nodometro. Existing installation paths, Compose
project/service names and `local/telegraf-*` image tags remain unchanged.
Follow the upgrade in the existing checkout; do not rename its directory as
part of this migration.

Preparation and tests do not affect running services. The actual cutover
recreates the affected containers: perform it during an approved maintenance
window. Do not run `down -v` while validating the migration.

## Before Git pull

From the repository directory on the node being upgraded:

```bash
cd /root/docker/telegraf
backup="backups/before-named-volumes-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$backup"
chmod 700 "$backup"
git rev-parse HEAD > "$backup/commit"
git archive HEAD > "$backup/source.tar"
cp -a docker-compose.yml Dockerfile telegraf.conf .env telegraf.d "$backup/"
```

If Compose overrides or `image-check/settings.json` exist, copy them into the
same backup without overwriting files already saved. Keep the `$backup` path,
Compose project name and previous images. For version 2.0, save these tags before
switching if they are available:

```bash
docker image save -o "$backup/images-2.0.0.tar" \
  local/telegraf-image-check:2.0.0 local/telegraf-docker-prune:2.0.0 \
  local/telegraf-zfs:2.0.0 telegraf:1.37.3
```

This also protects against removal of unused images. If tags are missing, arrange
a rebuild from the saved sources before switching; do not assume that removing
a container preserves its image. For the single-service version, save the actual
image tag in use instead. Review local changes to tracked files before pulling;
do not use `reset --hard`. Existing caches remain intact in their original locations.

## Preparation

```bash
git pull --ff-only
```

If you have a local `image-check/settings.json` to preserve, add or update this
line in the existing `.env`:

```dotenv
IMAGE_CHECK_SETTINGS_FILE=./image-check/settings.json
```

Without the variable, `settings.json.example` is used. Preserve the node's URL,
GID, custom intervals and profiles. The selected file must be readable by UID
65534. It is not copied into the image.

For nodes still using the single-service version, review local customization
first. Move overrides granting the main Telegraf agent a socket, Docker group or
`/dev/zfs` into the backup. Remove only old `inputs.exec` now replaced by separate
services. For ZFS, use `zfs/40-zfs.conf.example` as the native local configuration.
Add the required profiles and the node's socket GID to `.env`. Preserve unrelated
local customization. The single-service version's volumes are neither reused
nor deleted.

```bash
docker compose config --quiet
docker compose build
```

These commands do not restart services. Check that local overrides do not restore
old `state/` bind mounts, `state-init` dependencies or image-check source mounts.
Do not enable the `zfs` profile on a node without OpenZFS. No manual volume
creation or permission repair is needed.

## Cutover and checks

During the approved maintenance window:

```bash
docker compose up -d --build --remove-orphans
docker compose ps -a
docker compose logs --tail 100
```

Compose initializes new volumes from collector images, recreates containers with
the new mounts and removes the old `state-init`. Application services in other
stacks are not stopped. Caches start empty: image-check performs a new scan and
docker-prune recalculates space. VictoriaMetrics history remains intact. The
first scan may take several minutes.

Expect five services if all profiles are enabled, no `state-init` and `healthy`
collectors. Compare host and Docker metrics in Grafana for at least 60s, pools
for 120s and datasets for 600s. Check image scan completion and prune collection
success. The main Telegraf agent must retain no socket, Docker group, Linux
capabilities or ZFS device.

## Rollback

Keep `state/`, old volumes and previous images. To restore the saved
configuration during an approved maintenance window, set `$backup` to the saved
path and run:

```bash
docker compose --profile '*' down
cp -a "$backup/docker-compose.yml" "$backup/Dockerfile" "$backup/telegraf.conf" .
cp -a "$backup/.env" .env
```

If local files changed, restore only the affected overrides and Telegraf
configuration files from the backup. If previous images are no longer in the
daemon, load the available archive first:

```bash
docker image load -i "$backup/images-2.0.0.tar"
```

Then:

```bash
docker compose up -d --no-build --pull never
```

The previous version reuses its original state. New named volumes remain
available for analysis and are not used by the old Compose configuration.
Rollback leaves an explicit local diff of restored files; it neither deletes
user work nor rewrites Git history.

## Isolated checks

Tests do not use application Docker sockets, registries or VictoriaMetrics.
They decode Remote Write on loopback inside the test container and compare
names, labels, values and timestamps from the existing tools.

```bash
COMPOSE_PROFILES=docker,image-check,docker-prune,zfs docker compose -f docker-compose.yml config --quiet
docker build --target test -t local/telegraf-tests:2.1.0 .
mkdir -p tests/.tmp
docker run --rm --user 0:0 --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --mount type=bind,src="$PWD",dst=/work,readonly \
  --mount type=bind,src="$PWD/tests/.tmp",dst=/work/tests/.tmp \
  --workdir /work --env TMPDIR=/work/tests/.tmp \
  --env PYTHONDONTWRITEBYTECODE=1 local/telegraf-tests:2.1.0
```

Root is used only for temporary files in the isolated suite. Production
collectors retain UID/GID 65534 with no capabilities. To also check named volumes,
build the three collector images and run from the repository directory:

```bash
COMPOSE_PROFILES=image-check,docker-prune,zfs docker compose -f docker-compose.yml build
mkdir -p tests/.tmp
python3 tests/check_named_volumes.py
```

This check creates and removes only randomly named test volumes. It does not
start the production stack, mount sockets or devices, or send metrics. It covers
starting Telegraf before the collectors, all volume permissions, reading the
three snapshots, timestamp preservation and mounting configuration separately
from code. It requires access to the Docker daemon.
