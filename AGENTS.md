# Nodometro repository

- Write shared documentation, code comments and user-facing diagnostics in
  English. Preserve existing metric names and label values for compatibility.
- The central working copy remains `/root/docker/telegraf` on the designated
  central host. The shared Nodometro repository is
  `https://github.com/daelmaselli/nodometro`; its SSH URL is
  `git@github.com:daelmaselli/nodometro.git`. New installations use the Compose
  project `nodometro`, `local/nodometro-*` custom images and the example checkout
  path `/root/docker/nodometro`. Keep metric names and label values stable;
  the monitoring stack's own inventory reflects its new project name.
  Do not move the central working copy or restart its services as part of a
  source/configuration change without authorization.
- The user authorizes commits for shared Nodometro changes made as part of their
  requests. Inspect the diff, run relevant checks, and commit completed changes.
  Do not stage unrelated user changes, force-push, or reset user work.
- Keep `.env`, active node-specific `telegraf.d/*.conf`, the active Compose
  override, `image-check/settings.json`, caches and backups untracked. Commit
  examples instead. Other nodes clone/pull shared changes from GitHub over
  HTTPS or SSH.
- New installs use `image-check/settings.json.example` by default. A local
  `settings.json` can override it without producing Git conflicts.
- Use ordinary Docker Compose commands. Do not add startup wrapper scripts,
  host timers, services or programs without explicit user confirmation.
- All custom deployment files and state must remain under
  `/root/docker/telegraf` or `/root/docker/grafana`. Ask before writing outside
  those directories. Ordinary authorized Docker lifecycle actions are allowed.
- The universal archive is exported from committed HEAD by
  `/root/docker/grafana/scripts/package-telegraf.py`. Keep Git current before
  generating the archive. Do not maintain a separate competing template tree.
