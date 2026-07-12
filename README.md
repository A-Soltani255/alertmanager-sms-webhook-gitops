# Alertmanager SMS Webhook GitOps

A production-ready GitOps project for sending Kubernetes and application alerts from Prometheus Alertmanager to an SMS gateway.

This repository contains:

- A Python Flask/Gunicorn SMS webhook for Alertmanager
- Kubernetes manifests for deploying the webhook
- kube-prometheus-stack Alertmanager configuration examples
- Production Prometheus alert rules for Kubernetes and platform services
- Kafka lag alerting with the current lag value included in the SMS message
- Restricted Pod Security compatible test manifests
- A complete operational runbook for build, deploy, test, troubleshoot, and rollback

---

## Project Goals

The goal of this project is to provide a clean, repeatable, GitOps-friendly way to send important monitoring alerts as SMS messages from a Kubernetes cluster.

It is designed for environments where:

- Kubernetes is managed by GitOps, for example Argo CD
- Monitoring is based on kube-prometheus-stack
- Alertmanager must send alerts to an HTTP webhook
- The webhook must call an external SMS gateway
- The cluster may enforce restricted Pod Security policies
- The environment may be air-gapped or partially offline
- Alert noise and duplicate SMS messages must be controlled carefully

---

## High-Level Architecture

```text
Prometheus
  |
  | fires alert
  v
Alertmanager
  |
  | webhook receiver
  v
sms-webhook-alertmanager
  |
  | HTTP request
  v
SMS Gateway
  |
  v
Mobile Phones
```

Prometheus evaluates alert rules.  
Alertmanager groups, routes, inhibits, and deduplicates alerts.  
The SMS webhook formats the alert and sends it to the SMS gateway.

---

## Repository Structure

```text
alertmanager-sms-webhook-gitops/
├── app/
│   ├── main.py
│   ├── requirements.txt
│   ├── requirements-prod.txt
│   ├── Dockerfile
│   ├── Dockerfile.gunicorn
│   └── tests/
│
├── deploy/
│   ├── kubernetes/
│   │   ├── namespace.yaml
│   │   ├── kustomization.yaml
│   │   └── webhook/
│   │       ├── configmap.yaml
│   │       ├── deployment.yaml
│   │       ├── service.yaml
│   │       └── secret.example.yaml
│   │
│   ├── helm/
│   │   └── values-alertmanager-sms.example.yaml
│   │
│   └── prometheus-rules/
│       └── additionalPrometheusRulesMap-production-final-with-kafka-lag-value.yaml
│
├── scripts/
│   ├── build-and-push.sh
│   ├── collect-monlog-rule-audit.sh
│   ├── kafka-lag-test-restricted-pod.yaml
│   └── render-check.sh
│
├── docs/
│   └── RUNBOOK.md
│
├── .gitignore
├── .gitattributes
├── LICENSE
└── README.md
```

---

## Main Features

### Alertmanager SMS Webhook

The webhook receives Alertmanager webhook payloads and sends formatted SMS messages through an external SMS gateway.

Features:

- Supports multiple alerts in one Alertmanager payload
- Supports multiple destination phone numbers
- Masks sensitive data in logs
- Supports dry-run mode
- Supports health and readiness endpoints
- Supports configurable timezone
- Supports message length limiting
- Supports fallback phone numbers
- Processes all alerts in the Alertmanager payload
- Designed to run with Gunicorn in Kubernetes
- Designed for GitOps deployment with Argo CD

---

### Production Alert Rules

The repository includes an example production `additionalPrometheusRulesMap` for kube-prometheus-stack.

Covered areas include:

- Fast Kubernetes workload alerts
- CrashLoopBackOff
- ImagePullBackOff
- PodNotReady without false alerts for Completed Job pods
- Unexpected Completed pods for non-Job workloads
- Job failures
- Long-running Jobs
- Argo CD alerts
- Redis alerts
- Strimzi Kafka alerts
- Kafka consumer lag warning and critical alerts
- Kafka lag value included in SMS description
- PVC and storage-related alerts
- Monitoring-gap alerts for missing exporters

---

### Kafka Lag SMS Support

Kafka lag alerts include the current lag value in the alert description.

Example SMS text:

```text
Alert: KafkaConsumerLagWarning
State: firing
Severity: warning
Summary: Kafka consumer group lag warning
Description: Kafka consumer group sms-lag-test-group has current lag 6123 on topic sms-lag-test. Threshold: > 5000.
```

The lag value is rendered using Prometheus annotation templating:

```yaml
description: "Kafka consumer group {{ $labels.consumergroup }} has current lag {{ printf \"%.0f\" $value }} on topic {{ $labels.topic }}. Threshold: > 5000."
```

---

## Important Design Decisions

### Do Not Put Dynamic Values in Labels

Do not put dynamic values such as current Kafka lag in labels.

Bad:

```yaml
labels:
  lag: "{{ $value }}"
```

Good:

```yaml
annotations:
  description: "Current lag is {{ printf \"%.0f\" $value }}"
```

Dynamic labels can create new alert instances repeatedly and cause alert storms.

---

### Completed Job Pods Should Not Trigger PodNotReady SMS

A pod in this state is usually normal for a completed Job or CronJob:

```text
0/1 Completed
```

Therefore, the fast PodNotReady rule excludes:

- `Succeeded` pods
- Job-owned pods

A separate alert is used for unexpected Completed pods that are not owned by Jobs.

---

### Do Not Route All Warnings to SMS

The recommended Alertmanager strategy is:

```text
critical alerts -> SMS
warning alerts  -> SMS only when sms="true"
info alerts     -> no SMS
```

This prevents SMS storms in busy Kubernetes clusters.

---

## Quick Start

### 1. Build the Webhook Image

```bash
cd app

docker build -f Dockerfile.gunicorn \
  -t <REGISTRY>/prometheus/webhook/sms-webhook-alertmanager:1.1.1 .

docker push <REGISTRY>/prometheus/webhook/sms-webhook-alertmanager:1.1.1
```

Example:

```bash
docker build -f Dockerfile.gunicorn \
  -t 172.20.117.211:7049/prometheus/webhook/sms-webhook-alertmanager:1.1.1 .

docker push 172.20.117.211:7049/prometheus/webhook/sms-webhook-alertmanager:1.1.1
```

---

### 2. Create the SMS Secret

Do not commit real secrets to Git.

Create the secret manually:

```bash
kubectl -n monlog create secret generic sms-webhook-secret \
  --from-literal=SMS_GATEWAY_URL='https://SMS_GATEWAY_IP/magfaHttpService?service=Enqueue&domain=magfa' \
  --from-literal=SMS_USERNAME='YOUR_USERNAME' \
  --from-literal=SMS_PASSWORD='YOUR_PASSWORD' \
  --from-literal=SMS_SOURCE='YOUR_SOURCE_NUMBER' \
  --dry-run=client -o yaml | kubectl apply -f -
```

---

### 3. Deploy the Webhook

Using Kustomize:

```bash
kubectl apply -k deploy/kubernetes
```

Verify:

```bash
kubectl -n monlog get deploy,svc,cm,secret | grep sms-webhook
kubectl -n monlog rollout status deployment/sms-webhook-alertmanager
```

---

### 4. Configure Alertmanager

Use the example file:

```text
deploy/helm/values-alertmanager-sms.example.yaml
```

The important receiver is:

```yaml
receivers:
  - name: 'null'

  - name: 'magfa-sms'
    webhook_configs:
      - url: 'http://sms-webhook-alertmanager.monlog.svc.cluster.local:5000/send_sms'
        send_resolved: false
```

Recommended routing model:

```yaml
route:
  group_by:
    - alertname
    - namespace
    - pod
    - deployment
    - statefulset
    - daemonset
    - job
    - job_name
    - ingress
    - service
    - consumergroup
    - topic
    - container
    - reason
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 4h
  receiver: 'null'
  routes:
    - receiver: 'null'
      matchers:
        - alertname = "Watchdog"

    - receiver: 'null'
      matchers:
        - alertname = "AlertmanagerFailedToSendAlerts"

    - receiver: 'null'
      matchers:
        - alertname = "PrometheusDuplicateTimestamps"

    - receiver: 'magfa-sms'
      matchers:
        - severity = "critical"
      group_wait: 5s
      group_interval: 1m
      repeat_interval: 2h

    - receiver: 'magfa-sms'
      matchers:
        - severity = "warning"
        - sms = "true"
      group_wait: 30s
      group_interval: 5m
      repeat_interval: 4h
```

---

### 5. Add Production Prometheus Rules

Use:

```text
deploy/prometheus-rules/additionalPrometheusRulesMap-production-final-with-kafka-lag-value.yaml
```

Merge its content under your existing kube-prometheus-stack:

```yaml
additionalPrometheusRulesMap:
```

Do not create two separate top-level `additionalPrometheusRulesMap` keys.

---

### 6. Deploy Through Argo CD

Commit and push your changes:

```bash
git add .
git commit -m "Deploy Alertmanager SMS webhook and production rules"
git push
```

Then sync the Argo CD application.

---

## Validation

### Check Alertmanager Config

```bash
kubectl -n monlog get secret alertmanager-kube-prometheus-stack-alertmanager \
  -o jsonpath='{.data.alertmanager\.yaml}' | base64 -d > /tmp/alertmanager.yaml

grep -nE 'magfa-sms|sms-webhook-alertmanager|send_resolved|group_by|group_wait|group_interval|repeat_interval' /tmp/alertmanager.yaml
```

Expected:

```yaml
send_resolved: false
```

---

### Check Webhook Logs

```bash
kubectl -n monlog logs deploy/sms-webhook-alertmanager -f
```

---

### Check Prometheus Alerts

```bash
kubectl -n monlog port-forward svc/kube-prometheus-stack-prometheus 9090:9090
```

```bash
curl -s 'http://127.0.0.1:9090/api/v1/alerts' | jq
```

Only alerts in `firing` state are sent to Alertmanager.

---

### Check Alertmanager Alerts

```bash
kubectl -n monlog port-forward svc/kube-prometheus-stack-alertmanager 9093:9093
```

```bash
curl -s 'http://127.0.0.1:9093/api/v2/alerts' | jq
```

---

## Testing Kafka Lag Alerts

For restricted Kubernetes clusters, use the manifest:

```text
scripts/kafka-lag-test-restricted-pod.yaml
```

The test process is:

1. Create a Kafka client pod compatible with restricted Pod Security
2. Create a test topic
3. Create a test consumer group offset
4. Produce messages without consuming them
5. Verify `kafka_consumergroup_lag` in Prometheus
6. Wait for `KafkaConsumerLagWarning` or `KafkaConsumerLagCritical`
7. Confirm SMS delivery
8. Reset offset and delete test resources

Full steps are documented in:

```text
docs/RUNBOOK.md
```

---

## Full Runbook

The complete zero-to-100 guide is available here:

[docs/RUNBOOK.md](docs/RUNBOOK.md)

The runbook includes:

- Repository structure
- Image build and push
- Air-gapped notes
- Kubernetes deployment
- Alertmanager routing
- PrometheusRule design
- Kafka lag testing
- Completed Job pod false-positive prevention
- Troubleshooting
- Rollback
- Production checklist

---

## Security Notes

Never commit real secrets.

This repository should only contain:

```text
secret.example.yaml
placeholder credentials
example phone numbers
```

Recommended production secret options:

- Manual Kubernetes Secret
- SealedSecrets
- ExternalSecrets
- SOPS
- Vault integration

Rotate any secret immediately if it is accidentally pushed to GitHub.

---

## Recommended Git Workflow

Create a development branch:

```bash
git checkout -b develop
```

Make changes on `develop`, then open a Pull Request to `main`.

Recommended tags:

```text
v1.0.0  Initial production-ready GitOps SMS monitoring release
v1.1.0  Webhook formatter or deployment improvements
v1.2.0  PrometheusRule improvements
v1.3.0  Argo CD deployment improvements
```

---

## License

This project is provided under the license defined in the `LICENSE` file.
