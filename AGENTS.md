# Telegraf repository

- The central repository is `/root/docker/telegraf` on central-host. It is the source
  of truth for shared configuration, scripts, module examples, and documentation.
- The user authorizes commits for shared Telegraf changes made as part of their
  requests. Inspect the diff, run relevant checks, and commit completed changes.
  Do not stage unrelated user changes, force-push, or reset user work.
- Keep `.env`, active node-specific `telegraf.d/*.conf`, the active Compose
  override, `image-check/settings.json`, caches and backups untracked. Commit
  examples instead. Other nodes clone/pull shared changes over SSH.
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
