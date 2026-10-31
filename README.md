# Monitoraggio Linux con Docker Compose

Uno stack per metriche di sistema, Docker, immagini e OpenZFS. Nessuno scheduler
o pacchetto aggiuntivo sull'host; i collector custom riusano i tool esistenti
senza `inputs.exec`. Telegraf principale non riceve socket Docker, gruppo Docker,
device ZFS o capability. Non esiste un servizio di inizializzazione.

## Installazione

Servono Docker Engine con Compose v2 compatibile con `pull_policy: build`, Git
e accesso SSH al repository centrale. Sul nodo nuovo:

```bash
git clone git@code.example:team/telegraf.git /root/docker/telegraf
cd /root/docker/telegraf
cp .env.example .env
stat -c '%g' /var/run/docker.sock
```

In `.env` impostare l'URL VictoriaMetrics e il GID restituito **dal nodo locale**.
L'URL deve essere raggiungibile anche dai container in rete bridge: `localhost`
non indica l'host. `COMPOSE_ROOT` indica dove sono gli stack da controllare.

```dotenv
VICTORIAMETRICS_URL=http://victoriametrics.example:8428/api/v1/write
DOCKER_GID=INSERIRE_IL_NUMERO_DEL_NODO
COMPOSE_PROFILES=docker,image-check,docker-prune
COMPOSE_ROOT=/root/docker
```

Lasciare `COMPOSE_PROFILES=` per le sole metriche standard; aggiungere `zfs`
soltanto dove OpenZFS è funzionante e `/dev/zfs` è disponibile. Per le metriche
ARC/kstat facoltative vedere [ZFS](zfs/README.md). `telegraf.d/` serve soltanto
alle personalizzazioni locali: per il monitoraggio standard non occorre copiare
file o creare override.

```bash
docker compose config --quiet
docker compose up -d
docker compose ps -a
```

Compose costruisce automaticamente le immagini dei collector abilitati, con
cache di build. Le basi sono Telegraf 1.37.3 e Debian bookworm fissata tramite
digest multiarch. Non si usa `latest`. Il build richiede accesso ai repository
Debian; i pacchetti non sono congelati e un build senza cache può aggiornarli.

## Aggiornamento

**Per il primo passaggio da una versione precedente seguire
[migrazione e rollback](docs/upgrade.md)**: cambiano i mount dello stato e della
configurazione image-check. Per gli aggiornamenti successivi tramite Git SSH:

```bash
cd /root/docker/telegraf
git pull --ff-only && docker compose up -d --build --remove-orphans
```

Il codice dei collector è nell'immagine: la build ne applica gli aggiornamenti.
Non serve `docker compose pull` per distribuire il codice finché le immagini
custom non sono pubblicate in un registry. Le immagini ufficiali hanno una
versione esplicita; per aggiornarle allo stesso tag si può usare separatamente
`docker compose pull telegraf docker` (omettere `docker` se il profilo è spento).

Se cambiano soltanto le configurazioni Telegraf montate, pianificare anche
`docker compose restart telegraf` o `docker compose restart docker`, secondo
il file modificato: il bind mount da solo non provoca una ricreazione.

## Servizi e configurazione

| Servizio / profilo | Raccolta | Accessi specifici |
| --- | --- | --- |
| `telegraf` / sempre | Sistema ogni 30s; legge `.prom` e invia Remote Write | proc/sys e filesystem host in lettura; rete host |
| `docker` / `docker` | Plugin Telegraf Docker e inventario ogni 30s, Remote Write diretto | Socket Docker |
| `image-check` / `image-check` | Scan ogni 6h, pubblicazione cache ogni 30s | Socket, Compose/Dockerfile in lettura, HTTPS registry |
| `docker-prune` / `docker-prune` | Spazio recuperabile ogni 3h, cache ogni 30s; non cancella nulla | Socket, senza rete IP |
| `zfs` / `zfs` | Pool/vdev ogni 60s, dataset ogni 300s | Solo `/dev/zfs`, senza rete IP |

Tutti i servizi usano UID/GID 65534, `cap_drop: ALL`, filesystem di sistema in
sola lettura e `no-new-privileges`. Il socket `:ro` non limita i metodi API:
i servizi che lo ricevono hanno accesso al daemon. I permessi del filesystem
host continuano a valere anche per Telegraf.

`.env`, `image-check/settings.json`, `telegraf.d/*.conf` e l'override Compose
sono locali, esclusi da Git e dai contesti di build sensibili. Nei nuovi nodi
image-check monta soltanto `settings.json.example`; il codice non è montato.
Per una configurazione locale seguire [image-check](image-check/README.md).

Opzioni facoltative da aggiungere a `.env` soltanto se necessarie:

| Variabile | Default | Uso |
| --- | --- | --- |
| `IMAGE_CHECK_INTERVAL` | `6h` | Intervallo scansione immagini |
| `DOCKER_PRUNE_INTERVAL` | `3h` | Intervallo calcolo spazio recuperabile |
| `IMAGE_CHECK_SETTINGS_FILE` | `./image-check/settings.json.example` | File locale di configurazione image-check |

## Stato, verifica e rimozione

Compose gestisce cinque named volume: `image-cache`, `prune-cache` e
`image-metrics`, `prune-metrics`, `zfs-metrics`. I nomi effettivi hanno il
prefisso del progetto Compose. Ogni collector scrive solo i propri volumi;
Telegraf monta le metriche in sola lettura, senza inizializzarle. Le immagini
preparano i permessi durante la build; non occorre root all'avvio.

Gli snapshot `.prom` sono atomici e conservano il timestamp di acquisizione:
rileggere uno snapshot non rende fresco un collector fermo. Le metriche, le
label e gli intervalli dei tool restano compatibili con lo storico.

```bash
docker compose ps -a
docker compose logs --tail 100
```

I collector devono diventare `healthy` dopo la prima pubblicazione. Il primo
scan immagini può durare diversi minuti. Per report e refresh vedere
[image-check](image-check/README.md). I [test](docs/upgrade.md#verifiche-isolate)
confrontano anche payload Remote Write reali senza inviarli a VictoriaMetrics.

```bash
docker compose --profile '*' down
```

Rimuove tutti i servizi dello stack e la rete, conservando cache e metriche.
Per cancellare **anche i cinque volumi e il loro contenuto**, usare invece
`docker compose --profile '*' down -v`. Le configurazioni locali, i backup e
il vecchio `state/` non vengono cancellati. Nessun servizio o timer rimane
installato sull'host.

Per disabilitare un collector già attivo, fermarlo e rimuoverlo con
`docker compose rm -sf NOME_SERVIZIO` prima di togliere il profilo da `.env`.
Il solo cambio del profilo non garantisce che Compose elimini il container.

## Repository

Il repository centrale è `/root/docker/telegraf` su central-host. Gli altri nodi
ricevono i commit via Git SSH. L'analisi originale è in [docs/analysis.md](docs/analysis.md);
la prima migrazione è conservata come [documento storico](docs/migration.md).
Prima della pubblicazione pubblica revisionare anche la storia Git, eventuali
segreti, riferimenti interni e licenze dei sorgenti vendorizzati.

L'unico archivio universale è esportato da **HEAD committato** con
`/root/docker/grafana/scripts/package-telegraf.py`, senza alberi template separati.
