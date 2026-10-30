# Monitoraggio Linux con Docker Compose

Uno stack, installato e rimosso con Compose, senza scheduler o pacchetti
installati sull'host. I tool esistenti restano riutilizzati; nessun collector
custom dipende da `inputs.exec`.

Prima di aggiornare un nodo esistente seguire la procedura di
[migrazione e rollback](docs/migration.md), che conserva cache e configurazioni.
L'analisi dell'implementazione precedente è in [docs/analysis.md](docs/analysis.md).

| Servizio / profilo | Raccolta | Uscita | Accessi specifici |
| --- | --- | --- | --- |
| `telegraf` / sempre | CPU, RAM, swap, system, filesystem, dischi, rete ogni 30s | Remote Write | proc/sys, filesystem host in lettura, rete host per le interfacce |
| `docker` / `docker` | Risorse e inventario di tutti gli stati ogni 30s | Remote Write diretto | Socket Docker, nessuna capability |
| `image-check` / `image-check` | Scan ogni 6h, cache pubblicata ogni 30s | `.prom` → Telegraf | Socket, Compose/Dockerfile in lettura, HTTPS registry |
| `docker-prune` / `docker-prune` | Scan API 1.51 ogni 3h, cache ogni 30s | `.prom` → Telegraf | Socket, senza rete IP; nessuna cancellazione |
| `zfs` / `zfs` | Pool/vdev ogni 60s, dataset ogni 300s | `.prom` → Telegraf | Solo `/dev/zfs`, senza rete IP |
| `state-init` / uno dei profili custom | Una volta all'avvio | Prepara permessi in `state/`, poi termina | Root con sole capability CHOWN, FOWNER, DAC_OVERRIDE; senza rete |

Il collector Docker riusa **gli stessi plugin Telegraf**, separati dall'agente
principale. Il push diretto evita un exporter aggiuntivo e preserva nomi, tipi,
label e calcoli nativi. Gli altri collector condividono un piccolo scheduler
Python e pubblicano snapshot Prometheus mediante sostituzione atomica.

Telegraf principale non riceve socket Docker, gruppo Docker, device ZFS o
capability. Tutti i servizi permanenti usano UID/GID 65534, filesystem di sistema
in sola lettura e `no-new-privileges`. Il mount del filesystem host serve a
`inputs.disk`; i permessi host continuano a valere. Non esporre il socket con
permessi globali: un mount socket `:ro` **non** limita i metodi API. I servizi
che ricevono il socket hanno quindi un accesso equivalente al controllo del
daemon anche se il codice effettua soltanto letture.

## Nuova installazione

Servono Docker Engine con Compose v2 (incluso `pull_policy: build`), Git e la
chiave SSH per il repository centrale. Nessun Python, ZFS userspace o tool
Docker CLI aggiuntivo sull'host. OpenZFS deve già essere funzionante sul nodo
per abilitare il profilo `zfs`.

```bash
git clone git@code.example:team/telegraf.git /root/docker/telegraf
cd /root/docker/telegraf
cp .env.example .env
stat -c '%g' /var/run/docker.sock
```

Inserire in `.env` il GID restituito dal proprio nodo e impostare l'URL di
VictoriaMetrics. Con profili Docker attivi il GID è obbligatorio per il
funzionamento: il fallback 65534 permette di validare anche la configurazione
senza Docker, ma non concede accesso a un socket con GID diverso.

```dotenv
VICTORIAMETRICS_URL=http://victoriametrics.example:8428/api/v1/write
DOCKER_GID=INSERIRE_IL_NUMERO_DEL_NODO
COMPOSE_PROFILES=docker,image-check,docker-prune
COMPOSE_ROOT=/root/docker
IMAGE_CHECK_INTERVAL=6h
DOCKER_PRUNE_INTERVAL=3h
```

`COMPOSE_PROFILES=` abilita solo metriche standard. Aggiungere `zfs` sui nodi
OpenZFS e copiare `zfs/40-zfs.conf.example` in `telegraf.d/40-zfs.conf` per le
metriche ARC/kstat native. Non serve più un override Compose ZFS.

```bash
docker compose config --quiet
docker compose up -d
docker compose ps -a
docker compose logs --tail 100
```

Alla prima installazione Compose costruisce le sole immagini custom abilitate.
Le basi sono Telegraf 1.37.3 e Debian bookworm fissata tramite digest
multiarch; i tre target del Dockerfile includono soltanto le rispettive
dipendenze. Il build richiede accesso ai repository Debian. I pacchetti Debian
non sono congelati a uno snapshot: una ricostruzione senza cache può includere
aggiornamenti di sicurezza. Non si usa `latest`.

## Aggiornamento tramite Git SSH

Finché non esiste un registry dei collector, Git distribuisce il codice e
Compose ricostruisce le immagini locali versionate:

```bash
cd /root/docker/telegraf
git pull --ff-only
docker compose pull
docker compose up -d --remove-orphans
```

`pull_policy: build` fa ricostruire i collector durante `up`, usando la cache
di build quando il codice non cambia. `pull` salta questi servizi e aggiorna
le immagini ufficiali. **Il pull delle immagini da solo non aggiorna il
codice Git.** Non pubblicare immagini `local/*` in un registry.

Per modifiche a sole configurazioni Telegraf montate, dopo `up` eseguire
`docker compose restart telegraf` o `docker compose restart docker` secondo il
file cambiato: i bind mount da soli non forzano una ricreazione. Questo
riavvio va pianificato sui nodi in produzione. L'aggiornamento di codice
incluso nei target Docker cambia invece l'immagine e ricrea il collector.

Una futura pubblicazione può sostituire `local/*` con riferimenti versionati
nel registry ed eliminare `pull_policy: build`; allora, dopo aver distribuito
le configurazioni, bastano `docker compose pull && docker compose up -d
--remove-orphans`.

## Stato, configurazione e rimozione

- `state/image-check/`: report, lock e cache registry locali.
- `state/docker-prune/`: risposta API, esito e timestamp locali.
- `state/metrics/{images,prune,zfs}/`: snapshot `.prom`, ciascuna directory
  scrivibile soltanto dal servizio corrispondente; Telegraf vede tutto in lettura.
- `.env`, `image-check/settings.json`, `telegraf.d/*.conf` e l'eventuale override
  Compose sono locali e ignorati. I nuovi nodi usano `settings.json.example`.

I file `.prom` conservano il timestamp reale di acquisizione anche quando
Telegraf li rilegge ogni 30s. L'arresto di un collector non rinnova i campioni.
Gli scan image/prune pubblicano periodicamente la cache, come prima; gli stati
di errore e i timestamp di successo rimangono quelli dei tool originali.
Disabilitare un profilo con `up --remove-orphans` non garantisce la rimozione
del suo vecchio container su ogni versione Compose: fermarlo e rimuoverlo
esplicitamente con `docker compose rm -sf NOME_SERVIZIO` prima di cambiarlo.
I vecchi file non vengono ripubblicati con timestamp nuovi.

```bash
docker compose down
```

Rimuove container e rete Compose senza servizi o timer residui sull'host.
`state/` e le configurazioni restano per una reinstallazione o un rollback;
non vengono cancellati da `down -v` perché sono bind mount. Nessuna pulizia
automatica elimina cache o vecchi volumi della versione precedente.

## Diagnostica e verifiche

```bash
docker compose logs --tail 100 -f image-check docker-prune zfs
docker compose exec image-check python3 /app/image-check/collect.py 6h --refresh
docker compose exec docker-prune /bin/bash -c '/bin/bash /app/docker-prune/collect.sh 1s >/dev/null'
```

Il refresh aggiorna la cache; lo scheduler la pubblica entro il ciclo successivo.
Per i comportamenti originali vedere [image-check](image-check/README.md) e
[ZFS](zfs/README.md). Un errore del tool lascia lo snapshot precedente con il
vecchio timestamp, mai con un falso successo nuovo.

I test di compatibilità confrontano **payload Remote Write reali** prima/dopo:
nomi, tutte le label, valori e conservazione dei timestamp. I comandi ripetibili
sono in [docs/migration.md](docs/migration.md). Non inviano campioni al database.

## Repository e pacchetto universale

I commit condivisi si fanno nel repository centrale su central-host; gli altri nodi
eseguono pull via SSH. Niente credenziali, cache o configurazioni attive nei
commit. Prima di rendere pubblico il repository revisionare anche la storia
Git, i riferimenti a host/reti interne e la licenza dei sorgenti vendorizzati.
Non basta aggiungere `.gitignore` per eliminare eventuali segreti già committati.

L'unico archivio universale è prodotto da
`/root/docker/grafana/scripts/package-telegraf.py` dal **HEAD committato**.
Non esiste un secondo albero template. La generazione dell'archivio e il deploy
non sono necessari per preparare questa migrazione.
