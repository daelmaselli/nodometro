# Migrating an existing stack to Nodometro

Nodometro explicitly uses the Compose project name `nodometro` and collector
images named `local/nodometro-*:2.1.0`. New installations use
`/root/docker/nodometro` in the examples. Telegraf remains the metrics agent and
its service is still named `telegraf`.

Changing projects creates new containers, a new network and five new named
volumes. Image-check and prune start with empty caches; the first image scan
may take several minutes. VictoriaMetrics history, metric names and host labels
are preserved. The monitoring containers themselves have new Compose project,
resource names and inventory labels. Update any expected-resource inventory or
alerts that explicitly reference the old monitoring containers.

Preparation does not stop services. The cutover must stop the old project before
starting the new one, so two stacks do not collect and send the same host metrics.
Keep the old checkout, local configuration, images, volumes and any old `state/`
directory intact until validation is complete. Do not run `down -v` or prune
old images during the migration.

## Prepare the new checkout

Use the same shell for the following steps. Check `docker compose ls --all` and
set `old_project` to the actual project name; `telegraf` below is an example.
Keep the old checkout unchanged so it remains available for rollback.

```bash
old_dir=/root/docker/telegraf
new_dir=/root/docker/nodometro
old_project=telegraf

docker compose ls --all
git clone https://github.com/daelmaselli/nodometro.git "$new_dir"
cp -a "$old_dir/.env" "$new_dir/.env"

if [ -f "$old_dir/image-check/settings.json" ]; then
  cp -a "$old_dir/image-check/settings.json" "$new_dir/image-check/settings.json"
fi
```

For SSH access, replace the clone URL with
`git@github.com:daelmaselli/nodometro.git`. The new directory must not already
exist. If an unused new checkout already exists, update it with `git pull
--ff-only` before copying local settings. Do not overwrite another installation.

Review the copied `.env`:

- Keep the node's VictoriaMetrics URL, socket GID, profiles and custom intervals.
- Remove an old `COMPOSE_PROJECT_NAME` value, or set it to `nodometro`. The shell
  environment and `-p` can also override the project's name.
- If local image-check settings were copied, set
  `IMAGE_CHECK_SETTINGS_FILE=./image-check/settings.json`. Adjust absolute paths
  that still point into the old checkout. Otherwise the tracked example is used.
- Keep `COMPOSE_ROOT` pointing to this node's application stacks. Enable `zfs`
  only if OpenZFS and `/dev/zfs` are available.

Copy any required local native plugin files from the old `telegraf.d/` after
reviewing them. For optional ARC/kstat metrics, use the current example:

```bash
cp "$new_dir/zfs/40-zfs.conf.example" "$new_dir/telegraf.d/40-zfs.conf"
```

Copying `.env` alone does not preserve extra plugin configuration or local
image-check settings. Do not copy old `inputs.exec` replaced by the independent
collectors. Review Compose overrides individually: none may grant the main
Telegraf agent the Docker socket, Docker group or ZFS device, or restore old
`state-init` dependencies, `state/` bind mounts or image-check source mounts.
Preserve unrelated local customization.

For rollback, keep the actual images used by the old containers. If needed,
export them with `docker image save` before cutover; do not assume old tags will
remain available after image cleanup. Older single-service and 2.0 deployments
may use different tags and storage layouts.

## Validate and build

```bash
cd "$new_dir"
docker compose config --quiet
docker compose config | sed -n '/^name:/p'
docker compose build
```

These commands do not restart services. The displayed project name must be
`nodometro`; correct any environment overrides before proceeding. Images prepare
volume ownership at build time; there is no initialization service and no manual volume creation
or permission repair is required. Run the [isolated checks](#isolated-checks)
below if needed before cutover.

## Cutover and checks

During the approved maintenance window:

```bash
docker compose --project-directory "$old_dir" -p "$old_project" --profile '*' down --remove-orphans
docker compose --project-directory "$new_dir" up -d --build --remove-orphans
docker compose --project-directory "$new_dir" ps -a
docker compose --project-directory "$new_dir" logs --tail 100
```

The first command stops the old project and preserves its volumes. The new
project cannot remove old-project containers through `--remove-orphans` alone.
Other application stacks are not stopped. Compose initializes the new volumes
from collector images; old volumes are neither reused nor deleted. Image-check
performs a new scan and docker-prune recalculates reclaimable space.

Expect five services if all profiles are enabled, no `state-init` and `healthy`
collectors. Compare host and Docker metrics in Grafana for at least two collection
cycles (60s at the default interval), pools for 120s and datasets for 600s. Check
image scan completion and prune collection success. The main Telegraf agent
must have no Docker socket, Docker group, Linux capabilities or ZFS device.

After this one-time migration, update the Nodometro checkout normally:

```bash
cd /root/docker/nodometro
git pull --ff-only && docker compose up -d --build --remove-orphans
```

## Rollback

During an approved maintenance window, with `old_dir`, `new_dir` and `old_project`
set as above:

```bash
docker compose --project-directory "$new_dir" -p nodometro --profile '*' down
docker compose --project-directory "$old_dir" -p "$old_project" up -d --no-build --pull never
```

The unchanged old checkout reuses its original configuration, images and state.
If its images were removed, load their saved archive with `docker image load`
first. New Nodometro volumes remain available for analysis; do not delete either
project's data until the migration has been validated. No Git reset or history
rewrite is required.

## Isolated checks

Tests do not use application Docker sockets, registries or VictoriaMetrics.
They decode Remote Write on loopback inside the test container and compare
names, labels, values and timestamps from the existing tools.

```bash
COMPOSE_PROFILES=docker,image-check,docker-prune,zfs docker compose -f docker-compose.yml config --quiet
docker build --target test -t local/nodometro-tests:2.1.0 .
mkdir -p tests/.tmp
docker run --rm --user 0:0 --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --mount type=bind,src="$PWD",dst=/work,readonly \
  --mount type=bind,src="$PWD/tests/.tmp",dst=/work/tests/.tmp \
  --workdir /work --env TMPDIR=/work/tests/.tmp \
  --env PYTHONDONTWRITEBYTECODE=1 local/nodometro-tests:2.1.0
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
