FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
  && apt-get install -y --no-install-recommends \
    bash \
    ca-certificates \
    curl \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY scripts/ /app/scripts/
RUN chmod +x /app/scripts/*.sh

CMD ["/app/scripts/entrypoint.sh"]
