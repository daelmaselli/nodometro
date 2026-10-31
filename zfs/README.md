# Optional OpenZFS monitoring

Enable the `zfs` profile only on nodes with working OpenZFS and `/dev/zfs`:

```dotenv
COMPOSE_PROFILES=docker,image-check,docker-prune,zfs
```

For native ARC and kstat metrics, also copy:

```bash
cp zfs/40-zfs.conf.example telegraf.d/40-zfs.conf
docker compose up -d
```

No override is needed. For older installations, first follow
[the migration guide](../docs/upgrade.md) to remove old local `inputs.exec` and
the device assigned to the main agent. No ZFS package, service or timer is
installed on the host.

The `zfs` service includes Debian bookworm ZFS 2.1.11 tools, as in the previous
image. It uses UID/GID 65534, no Linux capabilities and only `/dev/zfs`, with no
Docker socket or IP network. The device must be readable under the node's
permissions: if it requires a specific group, configure that group only for this
service in the local override. Do not enable `privileged`.

## Preserved collection behavior

- Main Telegraf: `inputs.zfs` reads `/host/proc/spl/kstat/zfs` every 30s by default,
  with `poolMetrics = true` and `useNativeTypes = true`. The new example inherits
  `TELEGRAF_INTERVAL` (default 30s); older local copies with an explicit `interval`
  keep their own value.
- `zfs` service: `collect.sh pools` every 60s, 20s timeout: status, capacity,
  fragmentation, pool and vdev errors.
- `zfs` service: `collect.sh datasets` every 300s, 30s timeout: used, available,
  snapshot, dataset, children, logical space and compression.

The original tool is unchanged. The scheduler prevents overlapping runs and
publishes atomic `.prom` snapshots to the `zfs-metrics` named volume with
acquisition timestamps. Telegraf reads them every 30s by default, preserving
timestamps, and sends Remote Write. After an error, the previous collection
retains its original timestamp. The `zfs_pool_status_*`, `zfs_vdev_*`,
`zfs_pool_errors_*` and `zfs_dataset_space_*` families and labels are preserved.

`snapshot_used_bytes` comes from the dataset's `usedbysnapshots`, without summing
individual snapshots that share blocks. It does not include child snapshots.
`used_bytes` also includes children: avoid double counting. No scrub, destroy,
import/export or property changes are performed.

```bash
docker compose logs --tail 100 -f zfs
```

Dashboards, inventory and Grafana thresholds are unchanged. Registering a new
ZFS host in central monitoring remains a separate operation.
