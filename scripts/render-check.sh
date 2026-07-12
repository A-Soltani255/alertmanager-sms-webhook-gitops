#!/usr/bin/env bash
set -euo pipefail

RELEASE="${RELEASE:-kube-prometheus-stack}"
NS="${NS:-monlog}"
CHART="${CHART:-./kube-prometheus-stack}"
VALUES="${VALUES:-values.yaml}"
OUT="${OUT:-/tmp/kps-rendered.yaml}"

helm template "$RELEASE" "$CHART" -n "$NS" -f "$VALUES" > "$OUT"

echo "Rendered to $OUT"
grep -nE 'magfa-sms|send_resolved|SmsFast|KafkaConsumerLag|ciritical|CIRITICAL|severity = "warning\|critical"' "$OUT" || true
