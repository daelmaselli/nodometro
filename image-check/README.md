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
in `state/image-check/` e `state/metrics/images/`. La lettura dei Compose serve
alle dichiarazioni build: non legge `.env`, `env_file`, secrets o `~/.docker`,
né esegue istruzioni dei Dockerfile. Registry privati senza credenziali restano
CHECK. Il socket Docker resta un accesso privilegiato al daemon.

Per impostazione predefinita usa `settings.json.example`; un
`image-check/settings.json` locale può impostare abbinamenti espliciti:

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

Il report persistente è `state/image-check/report.json`, modo 600. I messaggi
UTC con nome `[image-check]`, esito e motivazione vanno allo stderr Docker;
le credenziali nei messaggi vengono oscurate. `scan.log` è il fallback del
tool eseguito senza `TELEGRAF_TOOL_LOG`. Le pubblicazioni cache restano silenziose.
