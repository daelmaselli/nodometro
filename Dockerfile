FROM telegraf:1.37
USER root
RUN sed -i 's/^Components: main$/Components: main contrib/' /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends python3 python3-yaml zfsutils-linux \
    && rm -rf /var/lib/apt/lists/*
USER 65534:65534
