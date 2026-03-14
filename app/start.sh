#!/bin/sh
set -e
cd /app
pip install --no-cache-dir -r requirements.txt
exec uvicorn main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips '*'
 