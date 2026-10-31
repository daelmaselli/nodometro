# Analisi prima della migrazione

L'implementazione precedente usava un solo servizio `telegraf`, un Dockerfile
custom e Telegraf 1.37.3. Questo documento conserva le decisioni tecniche della
separazione; per la procedura attuale vedere [migrazione e rollback](upgrade.md).

## Dipendenze e frequenze

| Componente precedente | Frequenza / timeout | Dipendenze e stato |
| --- | --- | --- |
| CPU, mem, swap, system, disk, diskio, net | Raccolta e flush 30s | proc/sys, mount host, rete host, dev/udev per diskio |
| `inputs.docker` | 30s / 10s | Socket Docker; risorse container, tag Compose; storage_objects disattivato |
| `inputs.http` inventario Docker | 30s / 10s | GET `/containers/json?all=1`, stato/health normalizzati con Starlark |
| image-check `inputs.exec` | Pubblicazione 30s / 15s; scan 6h, limite 1800s | Python 3, PyYAML, adattatore API, upstream v1.8.0, socket, registry HTTPS, Compose/Dockerfile in lettura, volume cache |
| prune `inputs.exec` | Pubblicazione 30s / 75s; scan 3h; curl 60s; lock 70s | bash, curl, flock, API `/v1.51/system/df`, cache persistente, parser Starlark |
| ZFS nativo locale | 30s | `/proc/spl/kstat/zfs`, tipi nativi e poolMetrics |
| ZFS pools `inputs.exec` locale | 60s / 20s | bash, awk, zpool list/status, `/dev/zfs` |
| ZFS datasets `inputs.exec` locale | 300s / 30s | bash, awk, zfs list, `/dev/zfs` |

Dockerfile precedente: Telegraf 1.37 con Python, PyYAML e zfsutils-linux in
un'unica immagine. I target separati mantengono Debian bookworm per limitare
le variazioni delle dipendenze. La compatibilità tra gli strumenti ZFS del
container e il modulo kernel deve essere verificata sul nodo di destinazione.

Il servizio unico usa UID/GID 65534, gruppo aggiuntivo del socket,
`no-new-privileges`, filesystem in sola lettura, rete/UTS host; l'override locale
aggiunge `/dev/zfs`. Il socket in lettura non impedisce operazioni di scrittura
all'API. La separazione rimuove questo potere dall'agente principale.

## Contratti da conservare

- Tutti gli input standard e le loro esclusioni restano invariati.
- L'hostname deriva da `uts: host`, quindi la label `host` resta quella del nodo.
- Il servizio Docker conserva esattamente input Docker/HTTP, rinomina dei tag
  Compose e Starlark state_code/health_code/container_health.
- Rimangono esclusi i campi stringa `container_id` e `health_status` incompatibili
  con Remote Write. Restano inventariati container fermi e gli altri stati.
- Image-check mantiene codici, reason, check_id, log redatti, cache registry,
  lock e worker in background; lo scheduler indipendente chiama lo stesso tool.
- Prune continua a usare API 1.51: non si passa automaticamente alla 1.52,
  per evitare differenze nello spazio dichiarato recuperabile. Nessun prune
  viene eseguito. Il calcolo Starlark è trasferito senza cambiamenti in Python
  e confrontato con il parser precedente tramite Remote Write reale.
- Prune con `success=0` mantiene il vecchio timestamp e produce **zero** per
  le dimensioni, come prima; non riutilizza vecchi byte fingendoli validi.
- `zfs/collect.sh` rimane invariato, incluse label e proprietà ZFS raccolte.
- Gli escaping delle label devono corrispondere al parser Influx Telegraf,
  compreso il comportamento sui backslash; il test binario rileva differenze.

## Incompatibilità e decisioni

1. **Override e input locali vecchi:** le installazioni precedenti possono
   assegnare ZFS a Telegraf e usare `telegraf.d/40-zfs.conf` con exec. Non basta un Git pull:
   vanno sostituiti consapevolmente durante il cutover, dopo backup. Altri file
   locali potrebbero avere socket/exec/privilegi e vanno revisionati allo stesso modo.
2. **Cache:** conservare i vecchi volumi e lo stato per il rollback. La versione
   corrente usa named volume nuovi e consente di ripartire con cache vuota;
   i dati precedenti non vengono cancellati automaticamente.
3. **Profili:** una `.env` precedente può non avere COMPOSE_PROFILES; senza aggiungerlo
   verrebbero raccolte solo metriche di sistema. ZFS richiede device reale.
4. **Timestamp:** Telegraf legge `.prom` con `prometheus_metric_version=2` e
   conserva il timestamp di acquisizione. Il serializer Remote Write riconosce
   la measurement `prometheus` senza aggiungere prefissi. Niente label `file`,
   `url`, `instance` o `job` aggiuntive. Test di confronto prima/dopo effettivi.
5. **Errore scheduler:** uno snapshot incompleto non sostituisce mai quello
   valido. Un processo fermo lascia timestamp vecchi, non campioni freschi;
   freshness e staleness seguono poi il comportamento di VictoriaMetrics/Grafana.
6. **Docker nativo:** un servizio Telegraf dedicato invia direttamente in
   Remote Write. Evita di riscrivere i plugin o aggiungere exporter/socket proxy;
   non viene duplicato dall'agente principale.
7. **Rete:** l'agente mantiene rete host per osservare le interfacce del nodo.
   Docker nativo e image-check usano la rete Compose, quindi l'endpoint VM deve
   essere raggiungibile e risolvibile anche da lì. `localhost` identifica il
   container: usare un nome/IP del nodo, non localhost. Prune e ZFS hanno rete none.
8. **Privilegi:** UID/GID 65534 e cap_drop ALL per tutti i servizi permanenti;
   solo il servizio ZFS riceve il device. Solo i servizi Docker ricevono gruppo
   e socket. Nella prima versione separata un init preparava `state/`; con i
   named volume i permessi sono predisposti nelle immagini durante la build.
9. **Architetture:** la base Debian usa un digest multiarch. ZFS userspace e
   supporto della piattaforma devono essere verificati sul nodo destinazione.
10. **Distribuzione:** senza registry, Git e `pull_policy: build` consentono
    aggiornamenti con Git pull e Compose up con build.
    La sola immagine pullata non può distribuire codice Git non pubblicato.
11. **File montati:** cambiare una configurazione Telegraf non forza sempre
    ricreazione/ricaricamento. Pianificare un restart mirato dopo aggiornamenti
    delle sole configurazioni montate.
12. **Identità container:** lo split crea nuovi container e nomi Compose;
    il loro inventario cambia correttamente. Le serie storiche dei servizi già
    monitorati, host, pool e dataset conservano invece nomi e label.

Nessun componente del monitoraggio è installato sull'host. Le dashboard possono
continuare a usare le stesse metriche e label; l'inventario delle risorse attese
resta parte della configurazione del sistema di visualizzazione e allarme.

Riferimenti del formato: [parser Prometheus Telegraf](https://docs.influxdata.com/telegraf/v1/data_formats/input/prometheus/),
[serializer Remote Write](https://docs.influxdata.com/telegraf/v1/data_formats/output/prometheusremotewrite/),
[policy Compose build/pull](https://docs.docker.com/reference/compose-file/build/).

## Verifiche di compatibilità

La suite confronta payload Remote Write reali con un ricevitore isolato:
nomi, label, valori, timestamp, escaping e compatibilità dei calcoli Docker.
Comprende avvio senza snapshot, errori dei tool, conservazione degli ultimi
campioni validi e lettura degli intervalli dalla configurazione dell'agente.
Le istruzioni ripetibili sono in [upgrade.md](upgrade.md#verifiche-isolate).
Risultati, inventari e log di nodi reali devono restare fuori da Git.
