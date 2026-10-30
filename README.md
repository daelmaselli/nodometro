# Telegraf comune ai nodi Docker Linux

Questa directory si copia sui nodi senza cambiare hostname, dischi o
interfacce. Il gruppo Docker si imposta una volta nel file `.env`.
Tutti i file personalizzati rimangono nella directory del progetto; le cache
sono in volumi Docker nominati dello stack. Non installa servizi o programmi
sull'host.

## Avvio

Come root, nella directory `/root/docker/telegraf`:

```bash
cp .env.example .env
stat -c '%g' /var/run/docker.sock
```

Inserisci il numero restituito come `DOCKER_GID=...` nel file `.env`.
Anche il file contiene il comando per ricavarlo. Poi avvia normalmente:

```bash
docker compose up -d --build
```

Compose crea automaticamente i volumi `docker-prune-cache` e
`compose-image-cache`. L'immagine assegna UID/GID 65534 e modo 0750: non servono
`mkdir`, `chown` o `chmod` sull'host. `down`, `up`, `restart` e le ricreazioni
conservano i dati; solo `docker compose down -v` elimina entrambi i volumi.

Dagli avvii successivi bastano i normali comandi `docker compose up -d`,
`docker compose logs` e `docker compose restart telegraf`. Non serve un wrapper.

Il container mantiene UID/GID 65534 e riceve il gruppo aggiuntivo del socket.
L'accesso al daemon Docker è privilegiato; gli input configurati fanno letture.
La singola immagine custom include Python/PyYAML per image-check e gli strumenti
ZFS. Questi ultimi restano inattivi finché non si abilita il modulo ZFS.
`/dev` e il database `/run/udev` sono montati in sola lettura per risolvere nomi
e metadati usati da `inputs.diskio`; UID 65534 non appartiene al gruppo host
`disk` e il device cgroup continua a limitare l'accesso ai dispositivi non
autorizzati.

## Cosa è automatico

- Hostname del nodo tramite `uts: host`, senza un nome fisso in telegraf.conf.
- Tutte le CPU, i dischi e le interfacce, con esclusione di loopback,
  veth e bridge Docker dai dati di rete e dei filesystem virtuali dal disco.
- Container, healthcheck, risorse e etichette progetto/servizio Compose.
- Prune monitor nel container: API Docker ogni 3h, cache pubblicata ogni 30s.
- Verifica immagini Compose ogni 6h: PULL, BUILD?, RECREATE, CHECK e PINNED;
  la cache viene pubblicata ogni 30s.

L'indirizzo predefinito comune è `http://victoriametrics.example:8428/api/v1/write`.
Deve essere risolvibile e raggiungibile da ogni nodo. Per cambiarlo, copia
`.env.example` in `.env` e modifica `VICTORIAMETRICS_URL`.

La frequenza prune si modifica in `telegraf.conf`:

```toml
commands = ["/bin/bash /etc/telegraf/docker-prune/collect.sh 3h"]
```

Puoi usare `1h`, `6h`, `30m`. Poi `docker compose restart telegraf`.
Grafana considera valido il dato per quel periodo più 1 ora.

## Copia sugli altri nodi

La directory `/root/docker/telegraf` su **central-host** è il repository centrale.
Gli aggiornamenti condivisi diventano disponibili agli altri nodi dopo un
commit su central-host. Il repository non include cache, `.env`, backup, override
Compose attivo, configurazioni locali in `telegraf.d/` o abbinamenti build.
Image-check è comune e attivo; ZFS resta un modulo opzionale.

Su un nodo nuovo, con Git installato e accesso SSH a central-host:

```bash
git clone git@code.example:team/telegraf.git /root/docker/telegraf
cd /root/docker/telegraf
```

La destinazione deve essere vuota o non esistere. Se contiene già una precedente
installazione, conservarne prima configurazione e dati locali. Non usare clone
per sovrascrivere una directory esistente.

Poi seguire la preparazione di `.env` nella sezione Avvio. Docker, prune e
image-check sono già attivi. Solo ZFS si abilita copiando l'override e
la configurazione descritti nel relativo README.

Per aggiornare un nodo già clonato:

```bash
cd /root/docker/telegraf
# Una tantum per installazioni che avevano image-check come modulo opzionale:
rm -f telegraf.d/50-images.conf
git pull --ff-only
docker compose up -d --build
docker compose restart telegraf
```

Il vecchio `docker-compose.override.yml` dedicato soltanto a image-check è ora
ridondante e può essere eliminato; non rimuovere invece un override che abilita
ZFS o contiene altre personalizzazioni locali.

Il riavvio serve a ricaricare anche le configurazioni montate, che da sole
potrebbero non provocare la ricreazione del container. Le personalizzazioni
devono restare nei file locali ignorati; modificare un file comune versionato
può richiedere una riconciliazione prima del pull. I nodi scaricano da central-host;
le modifiche universali e i commit si fanno sul repository centrale.

### Pacchetto alternativo

Il pacchetto `telegraf-universal.tar.gz`, esportato dal commit corrente, contiene la directory `telegraf/`
senza cache, backup, credenziali o impostazioni locali. Prima di sovrascrivere
un'installazione, salva i suoi file di configurazione. Poi, da `/root/docker`:

```bash
tar -xzf telegraf-universal.tar.gz
cd telegraf
cp .env.example .env
stat -c '%g' /var/run/docker.sock
# Imposta DOCKER_GID nel file .env, poi:
docker compose up -d --build
```

Non copiare i volumi cache da un altro nodo: contengono risposte e timestamp
specifici. Non copiare un GID Docker fisso. Per aggiornamenti successivi
conserva `.env` e i tuoi file locali in `telegraf.d/`.

## Forzare i controlli Docker

Il riavvio non forza una nuova scansione: le cache e i timestamp persistono nei
volumi nominati. Per avviare subito image-check, mantenendo la cache registry:

```bash
docker compose exec telegraf \
  python3 /etc/telegraf/image-check/collect.py 6h --refresh
```

Per forzare soltanto il calcolo dello spazio recuperabile Docker:

```bash
docker compose exec telegraf \
  /bin/bash -c '/bin/bash /etc/telegraf/docker-prune/collect.sh 1s >/dev/null'
```

Per azzerare deliberatamente **entrambe** le cache e ripartire da zero:

```bash
docker compose down -v
docker compose up -d --build
```

L'opzione `-v` cancella i risultati, i timestamp e la cache registry. Il nuovo
avvio esegue quindi entrambe le scansioni; non usarla come normale riavvio.

## Estensioni specifiche

È disponibile anche [image-check/README.md](image-check/README.md): descrive i
controlli PULL, BUILD?, RECREATE, CHECK e PINNED attivi per impostazione
predefinita, con API Docker/registry e Dockerfile in sola lettura. Telegraf
resta non root e non aggiorna applicazioni.

Per i nodi OpenZFS è incluso il modulo opzionale [zfs/README.md](zfs/README.md),
con configurazione dedicata in `telegraf.d/40-zfs.conf`. Non è attivo sui nodi
generici. Aggiunge spazio snapshot, stato pool, errori, ARC e contatori I/O.

Gli input comuni sono in `telegraf.conf`. Puoi aggiungere plugin specifici in
`telegraf.d/90-local.conf` o in altri file con estensione `.conf`.
Il file di esempio non viene caricato. Le configurazioni vengono sommate:
un secondo input dello stesso tipo ne crea un'altra istanza, non sostituisce
il primo. Per cambiare filtri di un input comune modifica quell'input.

## Registrazione nel monitoraggio centrale

Le metriche e l'elenco Docker sono visibili automaticamente. Container,
healthcheck, riavvii e verifiche immagini seguono dinamicamente gli stack
presenti: aggiungere o rimuovere uno stack non richiede registrazione e la sua
rimozione non produce un falso allarme di servizio mancante.

Solo i moduli infrastrutturali opzionali con risorse attese persistenti vanno
registrati esplicitamente. Per il controllo prune di un host:

```bash
cd /root/docker/grafana
python3 scripts/build-monitoring.py --add-docker-host example-node
docker restart grafana
```

Gli host di base già registrati conservano i controlli infrastrutturali. Per un
host nuovo aggiungi prima host, filesystem e altri servizi persistenti
all'inventario. Lo script non elimina automaticamente queste risorse attese.
