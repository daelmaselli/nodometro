# Controllo immagini Compose in Telegraf

Adattatore API del motore **compose-image-check v1.8.0**, fissato al commit
`d3ae646417dc9d614623f6e907de9a5939ad1b15` del repository
https://github.com/daelmaselli/compose-image-check.
Il sorgente originale rimane invariato in `upstream/`; l'adattatore sostituisce
inventario e inspect Docker CLI con sole richieste GET all'Engine API.

## Comportamento

- PULL: layer pubblicati diversi dal tag locale, oppure tag locale mancante.
- RECREATE: l'immagine taggata localmente e un container/replica hanno ID diversi.
- BUILD?: confronto del prefisso dei layer con le basi FROM, come nel tool
  originale. Resta un indizio, non una prova della necessità di ricompilare.
- CHECK: impossibile determinare il risultato; non viene trasformato in OK.
- PINNED: digest esplicito, senza inseguire il movimento del tag.

Sono mantenuti cache registry 6h (senza rinnovare artificialmente i timestamp),
HEAD del manifest, selezione OS/architettura/variante, fallback OCI del tool,
limiti sulle risposte, HTTPS/token anonimi, redazione degli errori e arresto
delle richieste Docker Hub successive a un rate limit. Le operazioni logiche
Docker Hub sono distanziate di 8s, quelle verso gli altri registry di 1s.
Questi intervalli non garantiscono il numero esatto di richieste HTTP.

Non serve Docker CLI/Compose/Buildx nel container. Si usa il fallback OCI
originale; registry che richiedono credenziali non disponibili restano CHECK.
Non vengono letti `~/.docker`, credenziali applicative, `.env`, `env_file` o
secrets. I Compose sono analizzati solo per le dichiarazioni delle build;
il confronto delle immagini avviene tramite API. Lo scanner non esegue RUN
o altre istruzioni dei Dockerfile.

I percorsi `/opt/stacks/...` registrati da Dockge vengono risolti anche quando
la directory fisica `stacks` è annidata sotto `/root/docker`, purché il suffisso
del percorso individui un solo file o una sola directory.

Si inventariano servizi con container Compose già esistenti, anche fermati,
escludendo i one-off. I servizi mai creati non sono visibili nell'Engine API.
Sono limiti conservati anche il confronto rootfs.diff_ids (non rileva tutti i
cambiamenti di soli metadati) e i risultati incerti per ARG, multistadio,
Dockerfile inline o provenienza non ricostruibile.

## Attivazione predefinita

Il controllo è già attivo nel `docker-compose.yml` e nel `telegraf.conf`
universali. Su un nodo nuovo basta costruire e avviare lo stack:

```bash
docker compose up -d --build
```

Il container mantiene UID/GID 65534. La directory `/root/docker` è montata
in sola lettura su `/compose-host`; i permessi host restano applicati.
Tutte le scritture sono nel volume Docker nominato `compose-image-cache`.
Non vengono installati servizi, timer o pacchetti sull'host.

## Percorsi reali e permessi dei Compose

Le etichette Docker possono contenere il percorso fisico anche se lo stack è
stato avviato da un symlink. Sono riconosciuti `/root/docker`, `/stage/docker`,
`/docker` e `/opt/stacks`; gli altri prefissi si aggiungono a `host_roots` nel
file locale `settings.json`. Ogni prefisso deve corrispondere alla radice
montata su `/compose-host`.

`Permission denied` su un Compose significa che l'UID 65534, con i gruppi
aggiuntivi dello stack, non può attraversare una directory o leggere il file.
Il mount `:ro` non concede permessi aggiuntivi. Per identificare il componente
bloccante sull'host, ad esempio quando il percorso reale è `/docker`:

```bash
namei -l /docker/damp/docker-compose.yml
```

La scansione può terminare con `success=1` e tutti i servizi `CHECK`: la raccolta
è terminata, ma i confronti non sono determinabili. Questi risultati non
indicano che le immagini siano aggiornate. Cancellare i volumi cache non risolve
problemi di permessi o percorsi.

Le alternative sono concedere accesso in lettura ai soli Compose/Dockerfile e
attraversamento alle directory necessarie, oppure configurare in `settings.json`
gli abbinamenti build espliciti. `mode: pull` è utilizzabile solo per servizi
che si sa non avere build locali; per le build servono comunque Dockerfile
leggibili. Non cambiare ricorsivamente i permessi delle applicazioni.
Qualsiasi modifica a permessi o file fuori da Telegraf/Grafana richiede prima
l'approvazione dell'utente.

## Nodo ZFS

La stessa immagine universale contiene Python/PyYAML e gli strumenti ZFS.
L'override ZFS aggiunge soltanto `/dev/zfs`; image-check continua a funzionare
senza configurazione aggiuntiva. Telegraf resta non root.

## Frequenza e cache

In `telegraf.conf`:

```toml
commands = ["/usr/bin/python3 /etc/telegraf/image-check/collect.py 6h"]
interval = "30s"
timeout = "15s"
```

Ogni 30s viene pubblicato il risultato salvato. La scansione parte ogni 6h
in un processo in background **dentro lo stesso container**, senza bloccare
la raccolta delle altre metriche. Un lock evita sovrapposizioni; il timestamp
persistente evita nuove scansioni ad ogni riavvio. Una scansione interrotta
viene rilevata e ritentata. Limite complessivo della scansione: 30 minuti.

Il primo controllo può richiedere diversi minuti proprio per rispettare i
registry. La dashboard indica IN CORSO. Un errore complessivo invalida i risultati
precedenti, anziché presentarli come aggiornati. Un risultato scade dopo il
periodo configurato più 1h. La cache registry originale dura comunque 6h:
una scansione più frequente può riusarla e lo indica nel motivo.

Per avviare una scansione adesso, conservando la politica della cache registry:

```bash
docker exec telegraf python3 /etc/telegraf/image-check/collect.py 6h --refresh
```

`settings.json` permette di disabilitare consapevolmente la cache registry con
`"registry_cache": false`; aumenta il carico e il rischio di rate limit.

## Build ambigue

Per impostazione predefinita si usa `image-check/settings.json.example`,
versionato nel repository. Per personalizzazioni, copiarlo in
`image-check/settings.json` (locale e ignorato da Git) e impostare `builds`.
I percorsi relativi partono da `/root/docker`:

```json
{
  "compose_root": "/compose-host",
  "host_roots": ["/root/docker", "/stage/docker", "/docker", "/opt/stacks"],
  "registry_cache": true,
  "builds": {
    "progetto/servizio": {
      "context": "progetto/src",
      "dockerfile": "progetto/src/Dockerfile"
    },
    "altro/servizio": {"mode": "pull"}
  }
}
```

`mode: pull` è una dichiarazione esplicita che il servizio non usa build locali.
Non si risolvono variabili di percorso leggendo file env: viene richiesto un
abbinamento esplicito. Symlink fuori dal mount sono rifiutati. Compose include,
extends e tag YAML speciali non supportati restano CHECK, senza supposizioni.
La lettura di `grafana/telegram.env` non è necessaria.

## Grafana e Telegram

La dashboard **Telegraf · Immagini Compose** mostra esiti e motivi completi,
oltre a stato e orario delle scansioni. I controlli seguono automaticamente gli
stack presenti e non richiedono registrazione in Grafana. Gli esiti che
richiedono attenzione, compreso CHECK, producono WARN dopo 5m nella pagina
generale e Telegram. I confronti non determinabili non sono errori del
funzionamento delle applicazioni. Nessuna azione pull/build/recreate viene
eseguita dal controllo.

Per seguire avanzamento, risultati per servizio e riepilogo usa
`docker logs --tail 100 -f telegraf`. Il report persistente si trova in
`/var/cache/compose-image-check/report.json` nel container, con permessi 600
per l'utente Telegraf. `scan.log` nella stessa cache viene usato solo come
ripiego quando `TELEGRAF_TOOL_LOG` non è impostato; nell'avvio Compose i
messaggi arrivano direttamente ai log Docker. Le cache e i risultati del nodo
sono esclusi dal pacchetto universale.
