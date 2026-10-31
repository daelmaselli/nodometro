# Collector immagini Compose

Servizio indipendente `image-check`, attivato da
`COMPOSE_PROFILES=docker,image-check,docker-prune` in `.env`.
Lo scheduler `/app/collectors/run.py` richiama il tool originale ogni 30s,
conservando scansioni ogni 6h, lock, timeout complessivo di 30 minuti, cache
registry e gestione delle scansioni interrotte. Non usa `inputs.exec`.

L'adattatore del motore **compose-image-check v1.8.0**, commit
`d3ae646417dc9d614623f6e907de9a5939ad1b15`, e il sorgente vendorizzato in
`upstream/` rimangono invariati. L'adattatore sostituisce Docker CLI con GET
all'Engine API; non esegue pull, build o recreate delle applicazioni.

## Esiti conservati

- PULL: layer pubblicati diversi dal tag locale o tag locale mancante.
- RECREATE: immagine taggata localmente diversa da quella di un container/replica.
- BUILD?: confronto del prefisso dei layer delle basi FROM; è un indizio.
- CHECK: confronto non determinabile, mai trasformato in OK.
- PINNED: digest esplicito, senza inseguire il movimento del tag.

Si conservano nomi `compose_image_scan_*`, `compose_image_check_*`, label,
reason, check_id e codici. I risultati scadono dopo l'intervallo più 1h;
un errore complessivo invalida i risultati precedenti. La dashboard e i
controlli Grafana non richiedono modifiche.

Cache registry 6h senza rinnovo artificiale dei timestamp, HEAD del manifest,
selezione piattaforma, fallback OCI, redazione errori, limiti di risposta e
arresto delle richieste Docker Hub dopo rate limit rimangono quelli originali.
Le operazioni logiche Docker Hub sono distanziate di 8s, gli altri registry di
1s. Il primo scan può quindi richiedere diversi minuti.

## Configurazione e permessi

Il servizio usa UID/GID 65534 e il GID locale del socket da `.env`. Monta
`${COMPOSE_ROOT:-/root/docker}` in sola lettura su `/compose-host` e scrive solo
nei named volume `image-cache` e `image-metrics`. La lettura dei Compose serve
alle dichiarazioni build: non legge `.env`, `env_file`, secrets o `~/.docker`,
né esegue istruzioni dei Dockerfile. Registry privati senza credenziali restano
CHECK. Il socket Docker resta un accesso privilegiato al daemon.

Il codice è incorporato nell'immagine. Compose monta soltanto
`settings.json.example` come configurazione predefinita. Per personalizzarla:

```bash
cp image-check/settings.json.example image-check/settings.json
```

Aggiungere a `.env` `IMAGE_CHECK_SETTINGS_FILE=./image-check/settings.json`.
Il file locale è ignorato da Git e dalla build; deve essere leggibile da UID
65534. Un percorso inesistente provoca un errore, senza creare directory.
La configurazione può impostare abbinamenti espliciti:

```json
{
  "compose_root": "/compose-host",
  "host_roots": ["/root/docker", "/stage/docker", "/docker", "/opt/stacks"],
  "registry_cache": true,
  "builds": {
    "project/app": {"context": "project/src", "dockerfile": "project/src/Dockerfile"},
    "project/other": {"mode": "pull"}
  }
}
```

I percorsi sono relativi a `COMPOSE_ROOT`; ciascun prefisso `host_roots` deve
corrispondere alla directory montata. La risoluzione dei percorsi fisici e
Dockge annidati rimane disponibile. Symlink fuori dal mount sono rifiutati.
I servizi Compose mai creati e i one-off non vengono inventariati. ARG,
multistadio, Dockerfile inline e build non ricostruibili possono restare CHECK.
Il confronto rootfs.diff_ids non rileva tutti i cambiamenti di soli metadati.

Un Compose/Dockerfile non leggibile da UID 65534 produce CHECK. Non cambiare
ricorsivamente i permessi delle applicazioni; configurare mapping espliciti
quando possibile. Cambiamenti a file o permessi fuori da Telegraf/Grafana
richiedono autorizzazione.

## Esecuzione e log

```bash
docker compose logs --tail 100 -f image-check
docker compose exec image-check python3 /app/image-check/collect.py 6h --refresh
```

Il refresh conserva la politica della cache registry; lo scheduler pubblica
entro il ciclo successivo. Cambiare `IMAGE_CHECK_INTERVAL` in `.env` e
ricreare con `docker compose up -d` modifica l'intervallo degli scan.
`registry_cache: false` forza più richieste e può favorire rate limit.

Il report persistente è `/var/cache/compose-image-check/report.json` nel volume
`image-cache`, modo 600. I messaggi
UTC con nome `[image-check]`, esito e motivazione vanno allo stderr Docker;
le credenziali nei messaggi vengono oscurate. `scan.log` è il fallback del
tool eseguito senza `TELEGRAF_TOOL_LOG`. Le pubblicazioni cache restano silenziose.

## Target e stage ereditati

La dichiarazione `build.target` viene rispettata, anche nei mapping locali
`builds` tramite il campo `target`. Per i target che ereditano uno stage padre,
il confronto segue la catena `FROM` fino alla base esterna e usa il controllo
dei layer originale. Gli stage non raggiunti, per esempio quelli di test,
non vengono confrontati. I riferimenti base `nome:tag@digest` vengono normalizzati
per le API OCI senza inseguire il tag.

Dipendenze cross-stage (`COPY --from`, mount da altri stage), ARG non risolti e
target sconosciuti restano CHECK; non vengono dichiarati aggiornati per
esclusione. Il confronto tra ID del tag locale e container continua a rilevare
RECREATE. Per i collector locali vengono confrontate le basi esterne, senza
cercare i tag `local/*` nel registry.
