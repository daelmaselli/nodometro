# Monitoraggio OpenZFS opzionale

Abilitare il profilo `zfs` solo su nodi con OpenZFS funzionante e `/dev/zfs`:

```dotenv
COMPOSE_PROFILES=docker,image-check,docker-prune,zfs
```

Per ARC e kstat nativi copiare anche:

```bash
cp zfs/40-zfs.conf.example telegraf.d/40-zfs.conf
docker compose up -d
```

Non serve un override. Su installazioni precedenti seguire prima
[la migrazione](../docs/migration.md), che rimuove i vecchi `inputs.exec` locali
e il device assegnato all'agente principale. Nessun pacchetto ZFS, servizio o
timer viene installato sull'host.

Il servizio `zfs` include gli strumenti Debian bookworm ZFS 2.1.11, come
nell'immagine precedente. Usa UID/GID 65534, nessuna capability, solo il device
`/dev/zfs`, nessun socket Docker o rete IP. Il device deve essere leggibile
secondo i permessi del nodo: se richiede un gruppo specifico, configurarlo
solo su questo servizio nell'override locale. Non concedere `privileged`.

## Raccolta invariata

- Telegraf principale: `inputs.zfs` legge `/host/proc/spl/kstat/zfs` ogni 30s,
  con `poolMetrics = true` e `useNativeTypes = true`.
- Servizio `zfs`: `collect.sh pools` ogni 60s, timeout 20s: stato, capacità,
  frammentazione, errori per pool e vdev.
- Servizio `zfs`: `collect.sh datasets` ogni 300s, timeout 30s: usato,
  disponibile, snapshot, dataset, figli, spazio logico e compressione.

Il tool originale è invariato. Lo scheduler esegue senza sovrapposizioni e
pubblica snapshot `.prom` atomici in `state/metrics/zfs/`, con timestamp di
acquisizione. Telegraf li rilegge ogni 30s senza cambiare il timestamp e li
invia tramite Remote Write. Dopo un errore, la precedente raccolta resta con
il suo timestamp originale. Le famiglie `zfs_pool_status_*`, `zfs_vdev_*`,
`zfs_pool_errors_*`, `zfs_dataset_space_*` e le label sono conservate.

`snapshot_used_bytes` deriva da `usedbysnapshots` del singolo dataset, senza
sommare lo spazio dei singoli snapshot che condividono blocchi. Non include
snapshot dei figli. Anche `used_bytes` include figli: evitare doppi conteggi.
Non si eseguono scrub, destroy, import/export o modifiche a proprietà.

```bash
docker compose logs --tail 100 -f zfs
```

Dashboard, inventario e soglie Grafana rimangono invariati. La registrazione
di un nuovo host ZFS nel monitoraggio centrale resta un'operazione separata.
