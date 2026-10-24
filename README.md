# Alertmanager SMS Webhook GitOps

Production-ready GitOps project for sending important Kubernetes and application alerts from **Prometheus Alertmanager** to an external **SMS gateway**.

This repository contains the SMS webhook application, Kubernetes deployment manifests, kube-prometheus-stack Alertmanager examples, production Prometheus alert rules, restricted-cluster test manifests, and a complete operational runbook.

---

## 1. What This Repository Does

This project solves the following problem:

```text
Prometheus detects a problem in Kubernetes or an application
        ↓
Alertmanager receives the firing alert
        ↓
Alertmanager routes selected alerts to an HTTP webhook
        ↓
The SMS webhook formats the alert
        ↓
The webhook calls the SMS gateway
        ↓
Engineers receive SMS notifications
```

The repository is designed for environments where:

- Kubernetes monitoring is based on `kube-prometheus-stack`
- Alertmanager is managed by Helm/GitOps
- Argo CD deploys the monitoring configuration
- The cluster may enforce restricted Pod Security policies
- The environment may be air-gapped or partially offline
- Alert noise and duplicate SMS messages must be controlled carefully
- Important alerts such as Kafka lag, CrashLoopBackOff, ImagePullBackOff, and storage problems must reach engineers quickly

---

## 2. How to Read This Repository

Start with this `README.md` first. It explains the purpose of the repository and the correct reading order.

Then read the files in this order:

```text
1. README.md
   Understand the repository purpose, structure, and workflow.

2. docs/RUNBOOK.md
   Read the full zero-to-100 implementation guide.

3. app/main.py
   Understand how the Alertmanager webhook receives alerts and sends SMS.

4. app/Dockerfile.gunicorn
   Understand the production image build.

5. deploy/kubernetes/webhook/
   Review the Kubernetes Deployment, Service, ConfigMap, and Secret example.

6. deploy/helm/values-alertmanager-sms.example.yaml
   Review the Alertmanager routing configuration.

7. deploy/prometheus-rules/additionalPrometheusRulesMap-production-final-with-kafka-lag-value.yaml
   Review the production alert rules.

8. scripts/
   Use the helper scripts for build, render validation, rule audit, and Kafka lag testing.
```

For a new engineer, the best path is:

```text
README.md → docs/RUNBOOK.md → deploy/kubernetes → deploy/helm → deploy/prometheus-rules → scripts
```

For troubleshooting, go directly to:

```text
docs/RUNBOOK.md → Troubleshooting section
```

For production deployment, follow:

```text
docs/RUNBOOK.md → Build image → Create secret → Deploy webhook → Configure Alertmanager → Add Prometheus rules → Validate end-to-end
```

---

## 3. Repository Structure

```text
alertmanager-sms-webhook-gitops/
├── app/
│   ├── main.py
│   ├── requirements.txt
│   ├── requirements-prod.txt
│   ├── Dockerfile
│   ├── Dockerfile.gunicorn
│   └── tests/
│       ├── payload-single.json
│       └── payload-kafka-lag.json
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
├── .gitattributes
├── .gitignore
├── LICENSE
└── README.md
```

---

## 4. Important Files and Their Purpose

| Path | Purpose |
|---|---|
| `README.md` | Repository landing page and reading guide |
| `docs/RUNBOOK.md` | Full detailed implementation and operations guide |
| `app/main.py` | Python SMS webhook application |
| `app/Dockerfile.gunicorn` | Recommended production Dockerfile |
| `app/tests/` | Local test payloads for webhook validation |
| `deploy/kubernetes/webhook/configmap.yaml` | Runtime webhook configuration |
| `deploy/kubernetes/webhook/deployment.yaml` | Kubernetes Deployment for the webhook |
| `deploy/kubernetes/webhook/service.yaml` | Internal ClusterIP Service for Alertmanager to call |
| `deploy/kubernetes/webhook/secret.example.yaml` | Example secret structure only; do not commit real secrets |
| `deploy/helm/values-alertmanager-sms.example.yaml` | Example Alertmanager routing configuration |
| `deploy/prometheus-rules/` | Production Prometheus alert rules |
| `scripts/build-and-push.sh` | Helper script for building and pushing the image |
| `scripts/render-check.sh` | Helm rendering and validation helper |
| `scripts/collect-monlog-rule-audit.sh` | Collect existing Prometheus rules, metrics, and targets for deduplication |
| `scripts/kafka-lag-test-restricted-pod.yaml` | Restricted-policy-compatible Kafka lag test pod |

---

## 5. High-Level Architecture

```text
+-------------------+
|    Prometheus     |
| alert evaluation  |
+---------+---------+
          |
          | firing alert
          v
+-------------------+
|   Alertmanager    |
| grouping/routing  |
+---------+---------+
          |
          | webhook receiver
          v
+------------------------------+
| sms-webhook-alertmanager     |
| Python + Gunicorn            |
| /send_sms                    |
| /healthz /readyz             |
+--------------+---------------+
               |
               | HTTP request
               v
+-------------------+
|    SMS Gateway    |
+---------+---------+
          |
          v
+-------------------+
| Mobile Engineers  |
+-------------------+
```

---

## 6. Main Features

### SMS Webhook

The webhook:

- Receives Alertmanager webhook payloads on `POST /send_sms`
- Processes multiple alerts in one payload
- Sends SMS to one or more phone numbers
- Supports alert-specific phone numbers via `annotations.phone_numbers`
- Supports fallback phone numbers via `DEFAULT_PHONE_NUMBERS`
- Masks sensitive values in logs
- Supports dry-run mode
- Supports configurable timezone
- Supports message length limiting
- Supports health and readiness endpoints
- Runs with Gunicorn in production
- Is compatible with restricted Kubernetes policies

### Alertmanager Routing

The recommended routing model is:

```text
critical alerts → SMS
warning alerts  → SMS only when sms="true"
info alerts     → no SMS
```

This prevents SMS storms while still allowing important warning alerts to be sent.

### Production Prometheus Rules

The production rule file includes alerting logic for:

- Fast Kubernetes workload failures
- CrashLoopBackOff
- ImagePullBackOff / ErrImagePull
- PodNotReady without false alerts for completed Job pods
- Unexpected Completed pods for non-Job workloads
- Failed Jobs
- Long-running Jobs
- Argo CD health and sync problems
- Redis health and performance
- Strimzi Kafka health
- Kafka consumer lag warning and critical alerts
- Kafka lag value inside the SMS message
- PVC capacity and prediction alerts
- Monitoring-gap alerts for missing exporters

---

## 7. Important Design Decisions

### 7.1 Do Not Alert on Normal Completed Job Pods

A pod in this state is usually normal for a Job or CronJob:

```text
0/1 Completed
```

Therefore, the PodNotReady rule excludes:

- `Succeeded` pods
- Job-owned pods

A separate rule detects unexpected Completed pods that are not owned by Jobs.

### 7.2 Do Not Put Dynamic Values in Labels

Do not put current Kafka lag or other changing values in labels.

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

Dynamic labels create new alert instances repeatedly and can cause alert storms.

### 7.3 Pending Alerts Do Not Send SMS

Prometheus only sends alerts to Alertmanager after they become `firing`.

```text
pending → not sent to Alertmanager
firing  → sent to Alertmanager
```

So when testing SMS, always check whether the alert is still pending.

### 7.4 `send_resolved` Is Disabled for SMS

The recommended SMS receiver uses:

```yaml
send_resolved: false
```

This prevents extra recovery SMS messages and reduces duplicate-looking notifications.

---

## 8. Prerequisites

You need:

- Kubernetes cluster
- kube-prometheus-stack installed
- Prometheus Operator CRDs installed
- Alertmanager enabled
- Argo CD or another GitOps controller
- Container registry access
- SMS gateway URL, username, password, and sender/source number
- `kubectl`
- `helm`
- `docker` or compatible build tool
- `jq`
- Git

Recommended namespace used in examples:

```text
monlog
```

---

## 9. Quick Start

### 9.1 Build the Webhook Image

```bash
cd app

docker build -f Dockerfile.gunicorn \
  -t <REGISTRY>/prometheus/webhook/sms-webhook-alertmanager:1.1.1 .

docker push <REGISTRY>/prometheus/webhook/sms-webhook-alertmanager:1.1.1
```

Example:

```bash
docker build -f Dockerfile.gunicorn \
  -t 192.168.10.10:7049/prometheus/webhook/sms-webhook-alertmanager:1.1.1 .

docker push 192.168.10.10:7049/prometheus/webhook/sms-webhook-alertmanager:1.1.1
```

---

### 9.2 Create the SMS Secret

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

### 9.3 Deploy the Webhook

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

### 9.4 Configure Alertmanager

Use this example as a reference:

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

Recommended SMS routing:

```yaml
routes:
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

### 9.5 Add Production Prometheus Rules

Use:

```text
deploy/prometheus-rules/additionalPrometheusRulesMap-production-final-with-kafka-lag-value.yaml
```

Merge it under your kube-prometheus-stack `values.yaml` key:

```yaml
additionalPrometheusRulesMap:
```

Do not create two duplicate top-level `additionalPrometheusRulesMap` keys.

---

### 9.6 Deploy Through GitOps

Commit and push:

```bash
git add .
git commit -m "Deploy Alertmanager SMS webhook and production rules"
git push
```

Then sync the Argo CD application.

---

## 10. Validation Commands

### Check Alertmanager Generated Config

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

### Check Webhook Pod and Logs

```bash
kubectl -n monlog get pods -l app=sms-webhook-alertmanager
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

## 11. Testing

### 11.1 Direct Webhook Test

Port-forward the webhook:

```bash
kubectl -n monlog port-forward svc/sms-webhook-alertmanager 5000:5000
```

Send a test payload:

```bash
curl -s -X POST http://127.0.0.1:5000/send_sms \
  -H 'Content-Type: application/json' \
  --data-binary @app/tests/payload-single.json | jq
```

---

### 11.2 Kafka Lag Test

For restricted clusters, use:

```text
scripts/kafka-lag-test-restricted-pod.yaml
```

The process is:

```text
1. Create a restricted-compatible Kafka client pod
2. Create a test topic
3. Create a consumer group with committed offset
4. Produce messages without consuming them
5. Confirm kafka_consumergroup_lag in Prometheus
6. Wait for KafkaConsumerLagWarning or KafkaConsumerLagCritical
7. Confirm SMS delivery
8. Reset offset and delete test resources
```

The Kafka lag alert includes the current lag count in the SMS:

```text
Kafka consumer group sms-lag-test-group has current lag 6123 on topic sms-lag-test. Threshold: > 5000.
```

Full instructions are in:

```text
docs/RUNBOOK.md
```

---

## 12. Troubleshooting Quick Reference

### No SMS Received

Check in this order:

```text
1. Is the alert firing in Prometheus?
2. Is the alert visible in Alertmanager?
3. Does the Alertmanager route match the alert labels?
4. Is the webhook Service reachable from Alertmanager?
5. Does the webhook log show the POST request?
6. Is SMS_DRY_RUN=false?
7. Are SMS credentials correct?
8. Did the SMS gateway return HTTP 200?
```

Useful commands:

```bash
kubectl -n monlog logs deploy/sms-webhook-alertmanager --tail=100
kubectl -n monlog get endpoints sms-webhook-alertmanager
kubectl -n monlog get secret alertmanager-kube-prometheus-stack-alertmanager \
  -o jsonpath='{.data.alertmanager\.yaml}' | base64 -d
```

---

### Alert Exists in Prometheus but No SMS

Check the alert state:

```bash
curl -s 'http://127.0.0.1:9090/api/v1/alerts' \
  | jq '.data.alerts[] | {alertname: .labels.alertname, state: .state, labels: .labels}'
```

If the state is:

```text
pending
```

then it has not been sent to Alertmanager yet.

---

### Kafka Lag Alert Does Not Fire

Check that the metric exists:

```bash
curl -sG 'http://127.0.0.1:9090/api/v1/query' \
  --data-urlencode 'query=sum by (namespace, consumergroup, topic) (kafka_consumergroup_lag)' \
  | jq
```

If empty, Prometheus is not scraping Kafka lag metrics or the consumer group has no committed offset.

---

### Duplicate or Too Many SMS Messages

Check:

```text
send_resolved must be false
PARTIAL_FAILURE_HTTP_CODE should be 200
Alertmanager group_by must include important labels
Do not route all warning alerts to SMS
Do not use dynamic values in labels
Remove always-firing test alerts
```

---

## 13. Security Notes

Never commit real secrets.

This repository should only contain:

```text
secret.example.yaml
placeholder credentials
example values
```

Recommended production secret options:

- Manual Kubernetes Secret
- SealedSecrets
- ExternalSecrets
- SOPS
- Vault integration

Rotate credentials immediately if they are accidentally pushed to GitHub.

---

## 14. Recommended Git Workflow

Use `main` for stable production state.

Use `develop` for changes:

```bash
git checkout -b develop
```

Normal change flow:

```bash
git checkout develop
git pull

# edit files

git add .
git commit -m "Describe the change"
git push
```

Then open a Pull Request:

```text
develop → main
```

Recommended release tags:

```text
v1.0.0  Initial production-ready GitOps SMS monitoring release
v1.1.0  Webhook formatter or deployment improvements
v1.2.0  PrometheusRule improvements
v1.3.0  Argo CD deployment improvements
```

---

## 15. Full Documentation

The complete implementation guide is here:

[docs/RUNBOOK.md](docs/RUNBOOK.md)

Read it when you need full details about:

- Building the image
- Air-gapped deployment
- Kubernetes manifests
- Alertmanager configuration
- PrometheusRule design
- Kafka lag testing
- Completed Job pod false-positive prevention
- Troubleshooting
- Rollback
- Production checklist

---

## 16. License

This project is provided under the license defined in the `LICENSE` file.
