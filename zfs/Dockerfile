FROM telegraf:1.37
USER root
ARG WITH_IMAGE_CHECK=false
RUN sed -i 's/^Components: main$/Components: main contrib/' /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends zfsutils-linux \
    && if [ "$WITH_IMAGE_CHECK" = "true" ]; then DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends python3 python3-yaml; fi \
    && rm -rf /var/lib/apt/lists/*
USER 65534:65534
