#!/usr/bin/env bash
set -euo pipefail

REGISTRY="${REGISTRY:-REGISTRY.example.com}"
IMAGE_NAME="${IMAGE_NAME:-prometheus/webhook/sms-webhook-alertmanager}"
VERSION="${VERSION:-1.3.0}"
IMAGE="${REGISTRY}/${IMAGE_NAME}:${VERSION}"

cd "$(dirname "$0")/.."

docker build -f app/Dockerfile.gunicorn -t "$IMAGE" .
docker push "$IMAGE"

echo "$IMAGE"
