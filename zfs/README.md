# Modulo ZFS opzionale

Attivare solo sui nodi che usano OpenZFS. La configurazione comune rimane uguale
per gli altri nodi. Nessun servizio o pacchetto viene installato sull'host:
gli strumenti ZFS sono già presenti nell'unica immagine Telegraf universale,
ma non vengono eseguiti sui nodi generici.

Da `/root/docker/telegraf`, dopo aver copiato questa directory:

```bash
cp zfs/40-zfs.conf.example telegraf.d/40-zfs.conf
cp zfs/docker-compose.override.yml.example docker-compose.override.yml
docker compose up -d --build
```

Se hai già un override Compose, aggiungi le impostazioni ZFS al tuo file invece
di sovrascriverlo. Dagli avvii successivi basta `docker compose up -d`.
Il container conserva UID/GID 65534, senza `privileged` o nuove capability.
Richiede il device `/dev/zfs`; i comandi configurati eseguono solo letture.
Il piccolo override è necessario perché Docker non permette di dichiarare
`/dev/zfs` come device opzionale: sui nodi che non lo hanno lo stack non partirebbe.

## Metriche

- Ogni 30s, `inputs.zfs` legge `/host/proc/spl/kstat/zfs`: ARC, hit/miss,
  memoria, evizioni, prefetch, ZIL, contatori I/O per pool/dataset disponibili.
- Ogni minuto: stato pool, capacità, frammentazione ed errori read/write/checksum
  per pool e vdev.
- Ogni 5 minuti: spazio usato, disponibile, usato dagli snapshot, dal dataset,
  dai figli, spazio logico e rapporto di compressione.

`snapshot_used_bytes` deriva da `usedbysnapshots`: è lo spazio imputato agli
snapshot del singolo dataset. Non sommiamo lo spazio `used` dei singoli snapshot,
che condividono blocchi e darebbero un totale fuorviante. La proprietà non include
gli snapshot dei dataset figli: per un totale pool si sommano i dataset del pool.
Anche `used_bytes` include i figli: non sommarlo indiscriminatamente tra dataset.

La frequenza si modifica in `telegraf.d/40-zfs.conf`. Non vengono eseguiti scrub,
prune, destroy, modifiche a proprietà, import/export o altre operazioni ZFS.

## Dashboard e allarmi

La dashboard **Telegraf · ZFS** ha filtri Host, Pool e Dataset; mostra spazio
snapshot, capacità e spazio libero del pool, ARC/hit ratio, I/O, frammentazione,
compressione e contatori errori. Usa un solo decimale nelle misure.

Sulla macchina Grafana, dopo l'arrivo dei primi campioni:

```bash
cd /root/docker/grafana
python3 scripts/build-monitoring.py --add-zfs-host central-host
docker restart grafana
```

Sostituire `central-host` con il nodo da registrare, già presente nell'inventario.
I controlli compaiono nella pagina generale e notificano su Telegram:

- pool DEGRADED: WARN dopo 2m; altri stati diversi da ONLINE: CRIT dopo 2m;
- capacità pool: WARN da 85% per 10m, CRIT da 95% per 5m;
- contatori pool read/write/checksum non nulli: WARN dopo 5m. Sono cumulativi,
  quindi l'avviso non indica necessariamente un nuovo errore in quell'istante.

Lo spazio snapshot è informativo: non ha una soglia di cancellazione o allarme
arbitraria. ARC è per host, mentre I/O e spazio sono filtrabili per dataset.

Per testare:

```bash
docker exec telegraf telegraf --config /etc/telegraf/telegraf.conf --config-directory /etc/telegraf/telegraf.d --test --input-filter zfs:exec
```
