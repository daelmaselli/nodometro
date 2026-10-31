# Passaggio ai named volume e rollback

Questa versione elimina `state-init`, usa cinque named volume e incorpora
il codice dei collector nelle immagini `local/telegraf-*:2.1.0`. Non cambia
metriche, label o scheduling. Le immagini 2.0.0 e il precedente `state/` non
vengono cancellati. La procedura può ripartire con cache vuota.

La preparazione e i test non cambiano i servizi attivi. Il passaggio effettivo
ricrea i container interessati: eseguirlo nella finestra autorizzata. Non
eseguire `down -v` durante il collaudo.

## Prima del Git pull

Dalla directory del repository sul nodo da aggiornare:

```bash
cd /root/docker/telegraf
backup="backups/before-named-volumes-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$backup"
chmod 700 "$backup"
git rev-parse HEAD > "$backup/commit"
git archive HEAD > "$backup/source.tar"
cp -a docker-compose.yml Dockerfile telegraf.conf .env telegraf.d "$backup/"
```

Se esistono override Compose o `image-check/settings.json`, copiarli nello
stesso backup, senza sovrascrivere file già salvati. Conservare il percorso
`$backup`, il nome del progetto Compose e le immagini precedenti. Per la versione 2.0,
se i tag sono disponibili, salvarli prima del passaggio:

```bash
docker image save -o "$backup/images-2.0.0.tar" \
  local/telegraf-image-check:2.0.0 local/telegraf-docker-prune:2.0.0 \
  local/telegraf-zfs:2.0.0 telegraf:1.37.3
```

Questo protegge anche dalla rimozione delle immagini non più usate. Se i tag
mancano, predisporre prima del passaggio una ricostruzione dai sorgenti salvati;
non dare per scontato che rimuovere un container conservi la sua immagine.
Su un monolite salvare invece il tag effettivamente utilizzato. In presenza
di modifiche locali a file versionati, revisionarle prima del pull; non usare
`reset --hard`. Le cache esistenti restano nella loro posizione, intatte.

## Preparazione

```bash
git pull --ff-only
```

Se esiste un `image-check/settings.json` locale da conservare, aggiungere o
aggiornare questa riga nella `.env` esistente:

```dotenv
IMAGE_CHECK_SETTINGS_FILE=./image-check/settings.json
```

In assenza della variabile si usa `settings.json.example`. Non cambiare URL,
GID, intervalli personalizzati o profiles del nodo. Il file selezionato deve
essere leggibile dall'utente 65534. Non viene copiato nell'immagine.

Se il nodo usa ancora il monolite, prima di procedere revisionare le sue
personalizzazioni: spostare nel backup gli override che assegnano socket,
gruppo Docker o `/dev/zfs` al Telegraf principale; rimuovere soltanto i vecchi
`inputs.exec` ora sostituiti dai servizi; per ZFS usare il contenuto di
`zfs/40-zfs.conf.example` come configurazione locale nativa. Aggiungere in
`.env` i profiles necessari e il GID del socket del nodo. Conservare eventuali
personalizzazioni estranee a questa migrazione. I volumi del monolite non
vengono riutilizzati né cancellati.

```bash
docker compose config --quiet
docker compose build
```

Questi comandi non riavviano i servizi. Verificare che gli override locali non
ripristinino i vecchi bind `state/`, dipendenze da `state-init` o mount dei
sorgenti image-check. Su un nodo senza OpenZFS non abilitare il profilo `zfs`.
Non serve alcun comando di creazione volumi o correzione dei permessi.

## Passaggio e verifica

Nella finestra approvata:

```bash
docker compose up -d --build --remove-orphans
docker compose ps -a
docker compose logs --tail 100
```

Compose inizializza i nuovi volumi dalle immagini dei collector, ricrea i
container con i nuovi mount e rimuove il vecchio `state-init`. Non arresta i
servizi applicativi degli altri stack. Le cache ripartono vuote: image-check
esegue un nuovo scan e docker-prune ricalcola lo spazio. Lo storico in
VictoriaMetrics rimane intatto. Il primo scan può richiedere diversi minuti.

Atteso: cinque servizi se tutti i profiles sono attivi, nessun `state-init`,
collector `healthy`. Confrontare in Grafana host e metriche Docker per almeno
60s, pool per 120s e dataset per 600s. Verificare il completamento dello scan
immagini e l'esito della raccolta prune. Telegraf principale deve mantenere
assenza di socket, gruppo Docker, capability e device ZFS.

## Rollback

Non cancellare `state/`, i vecchi volumi o le immagini precedenti. Per tornare
alla configurazione salvata, nella finestra approvata, impostare `$backup` al
percorso conservato e usare:

```bash
docker compose --profile '*' down
cp -a "$backup/docker-compose.yml" "$backup/Dockerfile" "$backup/telegraf.conf" .
cp -a "$backup/.env" .env
```

Se sono stati cambiati file locali, ripristinare dal backup soltanto quelli
interessati (override e configurazioni Telegraf). Se le immagini precedenti
non sono più nel daemon, caricare prima l'archivio disponibile:

```bash
docker image load -i "$backup/images-2.0.0.tar"
```

Poi:

```bash
docker compose up -d --no-build --pull never
```

La versione precedente ritrova il proprio stato originale. I nuovi named volume
rimangono disponibili per analisi e non sono usati dal vecchio Compose.
Il rollback lascia un diff locale esplicito dei file ripristinati: non cancella
il lavoro dell'utente e non riscrive la storia Git.

## Verifiche isolate

I test non usano socket Docker applicativi, registry o VictoriaMetrics.
Decodificano Remote Write su loopback nel container di test e confrontano nomi,
label, valori e timestamp dei tool esistenti.

```bash
COMPOSE_PROFILES=docker,image-check,docker-prune,zfs docker compose -f docker-compose.yml config --quiet
docker build --target test -t local/telegraf-tests:2.1.0 .
mkdir -p tests/.tmp
docker run --rm --user 0:0 --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --mount type=bind,src="$PWD",dst=/work,readonly \
  --mount type=bind,src="$PWD/tests/.tmp",dst=/work/tests/.tmp \
  --workdir /work --env TMPDIR=/work/tests/.tmp \
  --env PYTHONDONTWRITEBYTECODE=1 local/telegraf-tests:2.1.0
```

L'utente root serve soltanto ai file temporanei della suite isolata. I collector
in produzione restano UID/GID 65534, senza capability. Per verificare anche i
named volume, dopo aver costruito le tre immagini, eseguire dalla directory
del repository:

```bash
COMPOSE_PROFILES=image-check,docker-prune,zfs docker compose -f docker-compose.yml build
mkdir -p tests/.tmp
python3 tests/check_named_volumes.py
```

Il controllo crea e rimuove soltanto volumi dal nome casuale dedicati al test.
Non avvia il Compose di produzione, non monta socket o device e non invia
metriche. Include Telegraf avviato prima dei collector, i permessi di tutti i
volumi, lettura delle tre pubblicazioni, conservazione dei timestamp e mount
di configurazione separato dal codice. Richiede accesso al daemon Docker.
