FROM alpine:3.20

RUN apk add --no-cache icecast gettext curl

COPY icecast.xml.template /etc/icecast.xml.template
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh \
  && mkdir -p /var/log/icecast2 \
  && chown -R icecast:icecast /var/log/icecast2 /etc/icecast.xml.template

EXPOSE 8000

USER icecast
ENTRYPOINT ["/entrypoint.sh"]
