# Only tool services use these targets; the agents use the official image.
FROM debian:bookworm-slim@sha256:99c0b25c46d0214d5a2091092e7908907dcaecd6a962cde0ac700acee222b6f3 AS tools
USER root
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends python3 ca-certificates \
    && rm -rf /var/lib/apt/lists/*
# Docker copies these permissions into fresh named volumes. No runtime root.
RUN install -d -o 65534 -g 65534 -m 0755 /metrics \
    && touch /metrics/.keep \
    && chown 65534:65534 /metrics/.keep
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TELEGRAF_TOOL_LOG=/proc/1/fd/2
COPY collectors/*.py /app/collectors/
USER 65534:65534
ENTRYPOINT ["/usr/bin/python3", "/app/collectors/run.py"]

FROM tools AS docker-prune
USER root
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends bash curl util-linux \
    && rm -rf /var/lib/apt/lists/*
RUN install -d -o 65534 -g 65534 -m 0750 /var/cache/docker-prune
USER 65534:65534
COPY docker-prune/collect.sh /app/docker-prune/collect.sh
CMD ["docker-prune"]

FROM tools AS image-check
USER root
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends python3-yaml \
    && rm -rf /var/lib/apt/lists/*
RUN install -d -o 65534 -g 65534 -m 0750 /var/cache/compose-image-check
COPY image-check /app/image-check
USER 65534:65534
CMD ["image-check"]

FROM tools AS zfs
USER root
RUN sed -i 's/^Components: main$/Components: main contrib/' /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends zfsutils-linux bash mawk \
    && rm -rf /var/lib/apt/lists/*
COPY zfs/collect.sh /app/zfs/collect.sh
USER 65534:65534
CMD ["zfs"]

# Optional verification image, never part of the deployed Compose stack.
FROM image-check AS test
USER root
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends bash curl util-linux \
    && rm -rf /var/lib/apt/lists/*
COPY --from=telegraf:1.37.3 /usr/bin/telegraf /usr/bin/telegraf
ENTRYPOINT ["python3", "-m", "unittest", "discover", "-s", "tests", "-v"]
