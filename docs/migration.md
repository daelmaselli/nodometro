# Evoluzione dal servizio unico

L'implementazione iniziale usava un servizio Docker Compose con Dockerfile
custom: Telegraf raccoglieva il sistema, interrogava Docker ed eseguiva i tool
immagini, spazio recuperabile e ZFS tramite `inputs.exec`.

La versione 2.0 ha separato i servizi e introdotto snapshot Prometheus atomici,
scheduling autonomo dei collector e un init per i permessi di directory bind.
La versione 2.1 sostituisce queste directory con named volume e prepara i
permessi nelle immagini, eliminando il servizio di init.

## Cosa preservare nella migrazione

- Nomi delle metriche, label, codici di stato e timestamp di acquisizione.
- Plugin Docker e relativa normalizzazione dei dati, in un servizio separato.
- Scheduling e comportamento dei tool esistenti, senza riscriverli inutilmente.
- Configurazioni e dati locali necessari per un eventuale rollback.

I vecchi override possono concedere socket Docker, gruppo Docker o `/dev/zfs`
al Telegraf principale: revisionarli prima del passaggio. I vecchi input exec
ora sostituiti dai collector vanno rimossi dalla configurazione locale per
non duplicare la raccolta. Conservare le personalizzazioni estranee al cambio.

La [procedura corrente](upgrade.md) include preparazione, verifiche e rollback,
anche per i nodi che usano ancora il monolite. Non riutilizzare comandi legati
a nomi di container o volumi di un'altra installazione: verificare il progetto
Compose e i mount effettivi del nodo da aggiornare.

L'[analisi tecnica](analysis.md) descrive i contratti di compatibilità e le
motivazioni della separazione. Inventari, risultati di collaudo, credenziali e
backup di nodi reali devono restare in file locali esclusi da Git.
