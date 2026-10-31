# Evolution from a single service

The original implementation used one Docker Compose service with a custom
Dockerfile. Telegraf collected system metrics, queried Docker and ran image,
reclaimable-space and ZFS tools through `inputs.exec`.

Version 2.0 separated the services and introduced atomic Prometheus snapshots,
independent collector scheduling and an init service for bind directory
permissions. Version 2.1 replaces those directories with named volumes and
prepares permissions in the images, removing the init service.

## What to preserve during migration

- Metric names, labels, status codes and acquisition timestamps.
- The Docker plugin and its data normalization, in a separate service.
- Scheduling and behavior of existing tools, without unnecessary rewrites.
- Local configuration and data needed for rollback.

Old overrides may grant the main Telegraf agent the Docker socket, Docker group
or `/dev/zfs`: review them before switching. Remove old local exec inputs now
replaced by collectors to avoid duplicate collection. Preserve unrelated local
customization.

The [current procedure](upgrade.md) includes preparation, checks and rollback,
including nodes still running the single service. Do not reuse commands tied to
another installation's container or volume names: check the actual Compose
project and mounts on the node being upgraded.

The [technical analysis](analysis.md) explains the compatibility requirements and
reasons for separating services. Keep real node inventories, test results,
credentials and backups in local files excluded from Git.
