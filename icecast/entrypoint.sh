#!/bin/sh
set -eu

export ICECAST_SOURCE_PASSWORD="${ICECAST_SOURCE_PASSWORD:-hackme}"
export ICECAST_RELAY_PASSWORD="${ICECAST_RELAY_PASSWORD:-hackme}"
export ICECAST_ADMIN_USER="${ICECAST_ADMIN_USER:-admin}"
export ICECAST_ADMIN_PASSWORD="${ICECAST_ADMIN_PASSWORD:-hackme}"
export ICECAST_HOSTNAME="${ICECAST_HOSTNAME:-localhost}"
export ICECAST_LOCATION="${ICECAST_LOCATION:-Local Radio}"
export ICECAST_ADMIN_EMAIL="${ICECAST_ADMIN_EMAIL:-radio@localhost}"

envsubst < /etc/icecast.xml.template > /tmp/icecast.xml
exec icecast -c /tmp/icecast.xml
