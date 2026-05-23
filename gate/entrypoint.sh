#!/bin/bash
set -e

# Wait for and install Sentinel Root CA if available
if [ -d /usr/local/share/ca-certificates/sentinel ]; then
    echo "[Gate Entrypoint] Installing Sentinel Root CA..."
    if [ -f /usr/local/share/ca-certificates/sentinel/mitmproxy-ca-cert.pem ]; then
        cp /usr/local/share/ca-certificates/sentinel/mitmproxy-ca-cert.pem /usr/local/share/ca-certificates/sentinel-mitmproxy.crt
        update-ca-certificates
    fi
fi

echo "[Gate Entrypoint] Starting Gate..."
exec python app.py
