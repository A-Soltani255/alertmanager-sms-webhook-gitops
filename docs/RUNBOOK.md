# Alertmanager SMS Webhook GitOps Runbook

## Purpose

This runbook documents the complete end-to-end implementation of SMS alerting for a Kubernetes monitoring stack based on `kube-prometheus-stack`, Alertmanager, a custom Python SMS webhook, Argo CD GitOps deployment, and production Prometheus alert rules.

The goal is that another engineer can repeat the same scenario from zero without searching for missing steps.

The final solution provides:

- A production-style Python webhook that receives Alertmanager webhooks and sends SMS through an HTTP SMS gateway.
- A container image built with Gunicorn.
- Kubernetes Deployment, Service, ConfigMap, and Secret structure.
- Alertmanager routing to the webhook.
- Production-ready `additionalPrometheusRulesMap` rules.
- Fast SMS alerts for important Kubernetes workload failures.
- Kafka consumer lag alerts that include the current lag count in the SMS body.
- Safe behavior for `0/1 Completed` Job/CronJob pods.
- Argo CD deployment workflow.
- Verification, testing, troubleshooting, and rollback steps.

---

## Final Architecture

```text
Kubernetes workloads / exporters
        |
        v
Prometheus scrapes metrics
        |
        v
PrometheusRule evaluates alerts
        |
        v
Only firing alerts are sent to Alertmanager
        |
        v
Alertmanager route filters/grouping/inhibition
        |
        v
Webhook receiver: http://sms-webhook-alertmanager.monlog.svc.cluster.local:5000/send_sms
        |
        v
Python Gunicorn SMS webhook
        |
        v
SMS Gateway HTTP API
        |
        v
Phone numbers receive SMS
```

Important rule:

```text
Pending Prometheus alerts are not sent to Alertmanager.
Only firing alerts are sent.
```

This means SMS delay is affected by:

1. exporter update interval,
2. Prometheus scrape interval,
3. Prometheus rule evaluation interval,
4. the rule `for:` value,
5. Alertmanager `group_wait`, `group_interval`, and `repeat_interval`,
6. SMS gateway response time.

---

## Recommended GitHub Repository

Recommended repository name:

```text
alertmanager-sms-webhook-gitops
```

Alternative names:

```text
k8s-alertmanager-sms-webhook
monlog-alerting-gitops
kube-prometheus-sms-alerting
```

Recommended topics/tags in GitHub:

```text
kubernetes
prometheus
alertmanager
gitops
argocd
sms-webhook
monitoring
kube-prometheus-stack
strimzi
kafka
redis
rabbitmq
fluentd
logstash
openebs
```

Recommended Git branches:

```text
main                production-ready manifests
staging             test changes before production
feature/<name>      feature branches
hotfix/<name>       urgent production fixes
```

Recommended Git tags:

```text
webhook-v1.0.0      first working Flask version
webhook-v1.1.0      Gunicorn production image
webhook-v1.2.0      improved message formatting and all-alert processing
webhook-v1.3.0      Kafka lag count in SMS and final production rules
rules-v2026.07.12   production Prometheus rules baseline
```

Recommended container image tags:

```text
REGISTRY/prometheus/webhook/sms-webhook-alertmanager:1.0.0
REGISTRY/prometheus/webhook/sms-webhook-alertmanager:1.1.0
REGISTRY/prometheus/webhook/sms-webhook-alertmanager:1.2.0
REGISTRY/prometheus/webhook/sms-webhook-alertmanager:1.3.0
REGISTRY/prometheus/webhook/sms-webhook-alertmanager:stable
```

Use immutable version tags in production. Avoid deploying only `latest`.

---

## Repository Structure

Use this structure:

```text
alertmanager-sms-webhook-gitops/
├── README.md
├── LICENSE
├── .gitignore
├── app/
│   ├── main.py
│   ├── requirements.txt
│   ├── requirements-prod.txt
│   ├── Dockerfile
│   ├── Dockerfile.gunicorn
│   ├── .dockerignore
│   └── tests/
│       ├── payload-single.json
│       └── payload-kafka-lag.json
├── deploy/
│   ├── kubernetes/
│   │   ├── kustomization.yaml
│   │   ├── namespace.yaml
│   │   └── webhook/
│   │       ├── configmap.yaml
│   │       ├── deployment.yaml
│   │       ├── service.yaml
│   │       └── secret.example.yaml
│   ├── helm/
│   │   └── values-alertmanager-sms.example.yaml
│   └── prometheus-rules/
│       └── additionalPrometheusRulesMap-production-final-with-kafka-lag-value.yaml
├── scripts/
│   ├── build-and-push.sh
│   ├── collect-monlog-rule-audit.sh
│   ├── kafka-lag-test-restricted-pod.yaml
│   └── render-check.sh
└── docs/
    └── RUNBOOK.md
```

### Why this structure is good

- `app/` contains only the webhook application and Docker build context.
- `deploy/kubernetes/` contains plain Kubernetes manifests for the webhook.
- `deploy/helm/` contains Helm value snippets for kube-prometheus-stack.
- `deploy/prometheus-rules/` contains the final additional rules.
- `scripts/` contains repeatable operational commands.
- `docs/` contains this runbook.
- Real secrets are excluded from Git.

---

## Secret Handling Policy

Do not commit live credentials to GitHub.

Never commit:

```text
SMS_GATEWAY_URL with real internal IP if sensitive
SMS_USERNAME
SMS_PASSWORD
SMS_SOURCE
Grafana admin password
basic auth htpasswd entries if private
```

Use one of these approaches:

1. Create the secret manually with `kubectl`.
2. Use SealedSecrets.
3. Use ExternalSecrets.
4. Use SOPS-encrypted Kubernetes Secret files.
5. Use Argo CD Vault Plugin.

For a simple air-gapped environment, manual secret creation is acceptable.

Example:

```bash
kubectl -n monlog create secret generic sms-webhook-secret \
  --from-literal=SMS_GATEWAY_URL='https://SMS_GATEWAY_IP/magfaHttpService?service=Enqueue&domain=magfa' \
  --from-literal=SMS_USERNAME='YOUR_USERNAME' \
  --from-literal=SMS_PASSWORD='YOUR_PASSWORD' \
  --from-literal=SMS_SOURCE='YOUR_SOURCE_NUMBER' \
  --dry-run=client -o yaml | kubectl apply -f -
```

Verify:

```bash
kubectl -n monlog get secret sms-webhook-secret
```

---

## Python SMS Webhook Design

The webhook exposes:

```text
GET  /healthz
GET  /readyz
POST /send_sms
```

Alertmanager sends JSON payloads to `/send_sms`.

The app:

- receives all alerts in the Alertmanager payload,
- processes every alert, not only the first alert,
- gets phone numbers from `annotations.phone_numbers`, then from `labels.phone`, then from `DEFAULT_PHONE_NUMBERS`,
- formats a clean SMS message,
- masks phone numbers and sensitive values in logs,
- supports dry-run mode,
- supports timezone conversion,
- supports maximum SMS length truncation,
- returns HTTP 200 for partial SMS failure if configured, preventing Alertmanager retry storms.

### Environment variables

| Variable | Example | Purpose |
|---|---|---|
| `SMS_GATEWAY_URL` | `https://SMS_GATEWAY_IP/magfaHttpService?service=Enqueue&domain=magfa` | SMS provider URL |
| `SMS_USERNAME` | `user` | SMS gateway username |
| `SMS_PASSWORD` | `password` | SMS gateway password |
| `SMS_SOURCE` | `983000...` | SMS sender number |
| `SMS_DRY_RUN` | `false` | When true, do not call real gateway |
| `SMS_TIMEOUT_SECONDS` | `15` | HTTP timeout to SMS gateway |
| `SMS_SUCCESS_STATUS_CODES` | `200` | Accepted HTTP codes |
| `PARTIAL_FAILURE_HTTP_CODE` | `200` | Avoid retry storm on partial failure |
| `DEFAULT_PHONE_NUMBERS` | `0912...` | Fallback phone numbers |
| `LOCAL_TIMEZONE` | `Asia/Tehran` | SMS timestamp timezone |
| `MAX_MESSAGE_CHARS` | `700` | SMS truncation length |
| `CLUSTER_NAME` | `shahkar` | Cluster name replacement in descriptions |
| `LOG_LEVEL` | `INFO` | Logging level |

### Why `PARTIAL_FAILURE_HTTP_CODE=200`

If the webhook returns non-2xx to Alertmanager, Alertmanager retries the notification. With SMS, retries can create duplicate SMS messages. For this design, the webhook logs partial failures internally but returns 200 to Alertmanager when configured.

### Why `send_resolved=false`

SMS should usually alert when a problem starts, not again when it resolves. If `send_resolved=true`, every alert can generate both firing and resolved SMS messages.

Final receiver setting:

```yaml
send_resolved: false
```

---

## Build the Webhook Image

### Standard online build

Set variables:

```bash
export REGISTRY='172.20.117.211:7049'
export IMAGE_NAME='prometheus/webhook/sms-webhook-alertmanager'
export VERSION='1.3.0'
export IMAGE="${REGISTRY}/${IMAGE_NAME}:${VERSION}"
```

Build:

```bash
docker build -f app/Dockerfile.gunicorn -t "$IMAGE" .
```

Push:

```bash
docker push "$IMAGE"
```

Tag stable if needed:

```bash
docker tag "$IMAGE" "${REGISTRY}/${IMAGE_NAME}:stable"
docker push "${REGISTRY}/${IMAGE_NAME}:stable"
```

### Air-gapped build notes

For an air-gapped environment, you need:

1. Python base image available in your private registry.
2. Python wheels downloaded for the exact Python version in the image.
3. Gunicorn wheel included if using the Gunicorn Dockerfile.

The earlier issue was caused by Python 3.12 trying to install wheels built for Python 3.9. Use Python 3.9 if your offline wheel set is for `cp39`.

Recommended base image:

```text
python:3.9-slim
```

If your Dockerfile installs from local wheels, use:

```dockerfile
COPY app/pip-req/ /wheels/
RUN python -m pip install --no-index --find-links=/wheels -r requirements.txt -r requirements-prod.txt
```

---

## Kubernetes Webhook Deployment

Use namespace `monlog` unless your environment uses another monitoring namespace.

### ConfigMap

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: sms-webhook-config
  namespace: monlog
  labels:
    app: sms-webhook-alertmanager
data:
  LOG_LEVEL: "INFO"
  LOCAL_TIMEZONE: "Asia/Tehran"
  SMS_DRY_RUN: "false"
  SMS_TIMEOUT_SECONDS: "15"
  MAX_MESSAGE_CHARS: "700"
  SMS_SUCCESS_STATUS_CODES: "200"
  PARTIAL_FAILURE_HTTP_CODE: "200"
  DEFAULT_PHONE_NUMBERS: "0912..."
  CLUSTER_NAME: "shahkar"
```

### Deployment

Use a restricted-compatible pod spec:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: sms-webhook-alertmanager
  namespace: monlog
  labels:
    app: sms-webhook-alertmanager
spec:
  replicas: 2
  selector:
    matchLabels:
      app: sms-webhook-alertmanager
  template:
    metadata:
      labels:
        app: sms-webhook-alertmanager
    spec:
      automountServiceAccountToken: false
      securityContext:
        runAsNonRoot: true
        runAsUser: 10001
        runAsGroup: 10001
        fsGroup: 10001
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: sms-webhook-alertmanager
          image: 172.20.117.211:7049/prometheus/webhook/sms-webhook-alertmanager:1.3.0
          imagePullPolicy: IfNotPresent
          ports:
            - name: http
              containerPort: 5000
          envFrom:
            - secretRef:
                name: sms-webhook-secret
            - configMapRef:
                name: sms-webhook-config
          readinessProbe:
            httpGet:
              path: /readyz
              port: http
            initialDelaySeconds: 5
            periodSeconds: 10
            timeoutSeconds: 2
            failureThreshold: 3
          livenessProbe:
            httpGet:
              path: /healthz
              port: http
            initialDelaySeconds: 10
            periodSeconds: 20
            timeoutSeconds: 2
            failureThreshold: 3
          resources:
            requests:
              cpu: 50m
              memory: 128Mi
            limits:
              cpu: 500m
              memory: 256Mi
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            runAsNonRoot: true
            runAsUser: 10001
            runAsGroup: 10001
            capabilities:
              drop:
                - ALL
          volumeMounts:
            - name: tmp
              mountPath: /tmp
      volumes:
        - name: tmp
          emptyDir: {}
```

### Service

```yaml
apiVersion: v1
kind: Service
metadata:
  name: sms-webhook-alertmanager
  namespace: monlog
  labels:
    app: sms-webhook-alertmanager
spec:
  type: ClusterIP
  selector:
    app: sms-webhook-alertmanager
  ports:
    - name: http
      port: 5000
      targetPort: http
```

Final internal URL:

```text
http://sms-webhook-alertmanager.monlog.svc.cluster.local:5000/send_sms
```

---

## Alertmanager Configuration

Use `apiVersion: v2`.

Use these principles:

- Drop noisy self-monitoring alerts from SMS.
- Send all `critical` alerts to SMS.
- Send `warning` alerts to SMS only when they explicitly have `sms="true"`.
- Use `send_resolved: false`.
- Include important grouping labels such as `consumergroup` and `topic`.

Recommended route:

```yaml
alertmanager:
  enabled: true
  apiVersion: v2
  config:
    global:
      resolve_timeout: 5m
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
    receivers:
      - name: 'null'
      - name: 'magfa-sms'
        webhook_configs:
          - url: 'http://sms-webhook-alertmanager.monlog.svc.cluster.local:5000/send_sms'
            send_resolved: false
```

### Important matcher syntax

Correct regex matcher:

```yaml
- severity =~ "warning|critical"
```

Wrong exact matcher:

```yaml
- severity = "warning|critical"
```

The wrong version searches for exactly `severity="warning|critical"`, which real alerts do not have.

For the final production design, prefer two routes:

```yaml
- severity = "critical"
```

and:

```yaml
- severity = "warning"
- sms = "true"
```

---

## Prometheus Rules Strategy

The final `additionalPrometheusRulesMap` is stored at:

```text
deploy/prometheus-rules/additionalPrometheusRulesMap-production-final-with-kafka-lag-value.yaml
```

It includes:

- fast Kubernetes workload SMS alerts,
- Argo CD alerts,
- Redis alerts,
- Strimzi Kafka and Kafka consumer lag alerts,
- PVC and storage alerts,
- future-ready monitoring-gap alerts for exporters not currently exposed,
- Kafka lag current count in SMS annotations.

### Completed pod fix

A pod in `0/1 Completed` is usually normal for Job/CronJob pods.

Do not alert `PodNotReady` for completed Job pods.

Final rule logic:

```promql
(
  kube_pod_status_ready{condition="true"} == 0
)
and on (namespace, pod)
(
  kube_pod_status_phase{phase=~"Pending|Running|Unknown"} == 1
)
unless on (namespace, pod)
(
  kube_pod_owner{owner_kind="Job"}
)
```

This excludes:

```text
Succeeded / Completed pods
Job-owned pods
CronJob-created Job pods
```

Unexpected completed non-Job pods are handled separately:

```text
SmsFastPodCompletedUnexpectedly
```

### Kafka lag count in SMS

Kafka lag alert annotation must include `{{ printf "%.0f" $value }}` in the annotation, not in labels.

Correct:

```yaml
annotations:
  description: "Kafka consumer group {{ $labels.consumergroup }} has current lag {{ printf \"%.0f\" $value }} on topic {{ $labels.topic }}. Threshold: > 5000."
```

Wrong:

```yaml
labels:
  lag: "{{ $value }}"
```

Do not put current lag value in labels because labels create alert identity. A changing lag label can create many new alert instances.

### Kafka lag warning rule

```yaml
- alert: KafkaConsumerLagWarning
  expr: |
    sum by (namespace, consumergroup, topic) (
      kafka_consumergroup_lag{consumergroup!=""}
    ) > 5000
  for: 2m
  labels:
    severity: warning
    category: custom-alert
    sms: "true"
  annotations:
    phone_numbers: "0912..."
    summary: "Kafka consumer group lag warning"
    description: "Kafka consumer group {{ $labels.consumergroup }} has current lag {{ printf \"%.0f\" $value }} on topic {{ $labels.topic }}. Threshold: > 5000."
```

### Kafka lag critical rule

```yaml
- alert: KafkaConsumerLagCritical
  expr: |
    sum by (namespace, consumergroup, topic) (
      kafka_consumergroup_lag{consumergroup!=""}
    ) > 60000
  for: 1m
  labels:
    severity: critical
    category: custom-alert
    sms: "true"
  annotations:
    phone_numbers: "0912..."
    summary: "Kafka consumer group lag critical"
    description: "Kafka consumer group {{ $labels.consumergroup }} has current lag {{ printf \"%.0f\" $value }} on topic {{ $labels.topic }}. Threshold: > 60000."
```

---

## Collect Existing Rules Before Adding New Ones

Before adding a large rule pack, collect current rules to avoid duplicates.

Run:

```bash
NS=monlog ./scripts/collect-monlog-rule-audit.sh
```

Then port-forward Prometheus:

```bash
kubectl -n monlog port-forward svc/kube-prometheus-stack-prometheus 9090:9090
```

In another terminal:

```bash
OUT="$(cat /tmp/monlog-rule-audit-last-dir)"

curl -s 'http://127.0.0.1:9090/api/v1/rules' \
  | jq . > "$OUT/prometheus/api-rules.json"

curl -s 'http://127.0.0.1:9090/api/v1/alerts' \
  | jq . > "$OUT/prometheus/api-alerts.json"

curl -s 'http://127.0.0.1:9090/api/v1/targets' \
  | jq . > "$OUT/prometheus/api-targets.json"

curl -s 'http://127.0.0.1:9090/api/v1/label/__name__/values' \
  | jq -r '.data[]' > "$OUT/prometheus/all-metric-names.txt"

egrep -i '^(argocd_|redis_|kafka_|strimzi_|zookeeper_|consul_|openebs_|lvm_|logstash_|rabbitmq_|fluentd_|fluentbit_|kube_persistentvolume|kubelet_volume|container_|kube_pod|kube_deployment|kube_statefulset|kube_daemonset|kube_job)' \
  "$OUT/prometheus/all-metric-names.txt" \
  > "$OUT/prometheus/relevant-metric-names.txt"

for prefix in argocd redis kafka strimzi zookeeper consul openebs lvm logstash rabbitmq fluentd fluentbit kube_persistentvolume kubelet_volume; do
  curl -sG 'http://127.0.0.1:9090/api/v1/series' \
    --data-urlencode "match[]={__name__=~\"${prefix}.*\"}" \
    | jq . > "$OUT/prometheus/series-${prefix}.json"
done
```

Package:

```bash
OUT="$(cat /tmp/monlog-rule-audit-last-dir)"
tar -czf "${OUT}.tar.gz" -C "$(dirname "$OUT")" "$(basename "$OUT")"
ls -lh "${OUT}.tar.gz"
```

---

## Merge Rules into kube-prometheus-stack Values

If your `values.yaml` already contains:

```yaml
additionalPrometheusRulesMap:
```

Do not create a second top-level key.

Merge the children under the existing key.

Bad:

```yaml
additionalPrometheusRulesMap:
  old-rules: ...

additionalPrometheusRulesMap:
  new-rules: ...
```

Good:

```yaml
additionalPrometheusRulesMap:
  old-rules: ...
  sms-fast-kubernetes-workload-alerts: ...
  kafka-alerts: ...
```

Remove old duplicate test rules:

```text
Sms-test1
```

Remove old per-consumer Kafka lag rules if replacing them with generic rules:

```text
KafkaConsumerGroupLagWarning_shahkar
KafkaConsumerGroupLagCritical_shahkar
KafkaConsumerGroupLagWarning_dis_bank
KafkaConsumerGroupLagCritical_dis_bank
...
```

Use generic rules:

```text
KafkaConsumerLagWarning
KafkaConsumerLagCritical
```

---

## Render and Validate Before Argo CD Sync

Run Helm template before pushing:

```bash
helm template kube-prometheus-stack ./kube-prometheus-stack \
  -n monlog \
  -f values.yaml \
  > /tmp/kps-rendered.yaml
```

Check Alertmanager config:

```bash
grep -nE 'magfa-sms|send_resolved|sms-webhook-alertmanager|severity = "warning\|critical"|severity =~ "warning\|critical"|ciritical|CIRITICAL' /tmp/kps-rendered.yaml
```

Expected:

```text
send_resolved: false
```

Not expected:

```text
send_resolved: flase
severity = "warning|critical"
ciritical
CIRITICAL
```

Check new rules:

```bash
grep -nE 'SmsFastPod|KafkaConsumerLag|ArgoCD|Redis|PVC|RabbitMQ|Consul|Logstash|Fluentd' /tmp/kps-rendered.yaml
```

---

## Argo CD Deployment

### Option A: One Argo CD application for webhook Kubernetes manifests

Example:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: monlog-sms-webhook
  namespace: argocd
spec:
  project: default
  source:
    repoURL: https://github.com/YOUR_ORG/alertmanager-sms-webhook-gitops.git
    targetRevision: main
    path: deploy/kubernetes
  destination:
    server: https://kubernetes.default.svc
    namespace: monlog
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
```

### Option B: kube-prometheus-stack Helm application

Use your existing kube-prometheus-stack Argo CD app and point it to your values file.

Make sure Argo CD applies:

- kube-prometheus-stack Helm values,
- webhook Kubernetes manifests,
- Prometheus rules,
- manual or sealed SMS secret.

### Common Argo CD mistake

If the Argo CD application points only to the Helm chart path, it will not apply plain YAML files under `deploy/kubernetes/` unless configured as a separate app or multi-source app.

Verify Argo CD path:

```bash
argocd app get <APP_NAME>
```

Look for:

```text
Path:
Chart:
Directory:
Helm:
```

---

## Post-Deployment Verification

Check webhook resources:

```bash
kubectl -n monlog get deploy sms-webhook-alertmanager
kubectl -n monlog get pod -l app=sms-webhook-alertmanager -o wide
kubectl -n monlog get svc sms-webhook-alertmanager
kubectl -n monlog get cm sms-webhook-config
kubectl -n monlog get secret sms-webhook-secret
```

Check pod rollout:

```bash
kubectl -n monlog rollout status deployment/sms-webhook-alertmanager
```

Check logs:

```bash
kubectl -n monlog logs deploy/sms-webhook-alertmanager --tail=100
```

Check health:

```bash
kubectl -n monlog port-forward svc/sms-webhook-alertmanager 5000:5000
```

```bash
curl -s http://127.0.0.1:5000/healthz | jq
curl -s http://127.0.0.1:5000/readyz | jq
```

Expected:

```json
{"status":"ok"}
```

and:

```json
{"status":"ready","dry_run":false}
```

Check environment:

```bash
kubectl -n monlog exec deploy/sms-webhook-alertmanager -- sh -c '
echo "SMS_DRY_RUN=$SMS_DRY_RUN"
echo "PARTIAL_FAILURE_HTTP_CODE=$PARTIAL_FAILURE_HTTP_CODE"
echo "DEFAULT_PHONE_NUMBERS=$DEFAULT_PHONE_NUMBERS"
echo "CLUSTER_NAME=$CLUSTER_NAME"
echo "MAX_MESSAGE_CHARS=$MAX_MESSAGE_CHARS"
'
```

Expected:

```text
SMS_DRY_RUN=false
PARTIAL_FAILURE_HTTP_CODE=200
CLUSTER_NAME=shahkar
MAX_MESSAGE_CHARS=700
```

---

## Verify Generated Alertmanager Configuration

Do not trust only Git. Check the live generated secret:

```bash
kubectl -n monlog get secret alertmanager-kube-prometheus-stack-alertmanager \
  -o jsonpath='{.data.alertmanager\.yaml}' | base64 -d \
  > /tmp/alertmanager.yaml
```

Check:

```bash
grep -nE 'magfa-sms|send_resolved|group_by|group_wait|group_interval|repeat_interval|sms-webhook-alertmanager' /tmp/alertmanager.yaml
```

Expected:

```text
send_resolved: false
sms-webhook-alertmanager.monlog.svc.cluster.local
```

Check Alertmanager logs:

```bash
kubectl -n monlog logs alertmanager-kube-prometheus-stack-alertmanager-0 \
  -c alertmanager \
  --tail=200 | egrep -i 'reload|error|failed|yaml|config'
```

---

## Direct Webhook Test

Port-forward webhook:

```bash
kubectl -n monlog port-forward svc/sms-webhook-alertmanager 5000:5000
```

Send test payload:

```bash
curl -s -X POST http://127.0.0.1:5000/send_sms \
  -H 'Content-Type: application/json' \
  --data-binary @app/tests/payload-single.json | jq
```

Expected:

```json
{
  "status": "success",
  "failures": 0
}
```

Watch logs:

```bash
kubectl -n monlog logs deploy/sms-webhook-alertmanager -f
```

---

## End-to-End Alert Test

### Prometheus alert state check

Port-forward Prometheus:

```bash
kubectl -n monlog port-forward svc/kube-prometheus-stack-prometheus 9090:9090
```

Check alerts:

```bash
curl -s 'http://127.0.0.1:9090/api/v1/alerts' | jq '.data.alerts[] | {alertname: .labels.alertname, state: .state, labels: .labels}'
```

If an alert is `pending`, no SMS will be sent yet.

### Alertmanager alert check

Port-forward Alertmanager:

```bash
kubectl -n monlog port-forward svc/kube-prometheus-stack-alertmanager 9093:9093
```

Check received alerts:

```bash
curl -s 'http://127.0.0.1:9093/api/v2/alerts' | jq '.[] | {alertname: .labels.alertname, status: .status, labels: .labels}'
```

If the alert exists in Prometheus as `firing` but not in Alertmanager, check Prometheus-to-Alertmanager configuration.

---

## Test Fast Pod Alerts

### ImagePullBackOff test

A bad image causes:

```text
ErrImagePull
ImagePullBackOff
```

Not CrashLoopBackOff.

Patch a deployment to a bad image:

```bash
kubectl -n shahkar set image deployment/apim-gateway-core \
  apim-gateway-core=not-exist.local/apim-gateway-core:notfound
```

Watch pod:

```bash
kubectl -n shahkar get pod -w
```

Check Prometheus:

```bash
curl -s 'http://127.0.0.1:9090/api/v1/alerts' \
  | jq '.data.alerts[] | select(.labels.alertname=="SmsFastPodImagePullBackOff") | {state: .state, labels: .labels, annotations: .annotations}'
```

Wait until:

```text
state: firing
```

Then check Alertmanager and webhook logs.

### Roll back image

```bash
kubectl -n shahkar rollout undo deployment/apim-gateway-core
```

---

## Test CrashLoopBackOff Alert

CrashLoopBackOff means the image starts but the process exits repeatedly.

Example test deployment:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: crashloop-test
  namespace: test-alerts
spec:
  replicas: 1
  selector:
    matchLabels:
      app: crashloop-test
  template:
    metadata:
      labels:
        app: crashloop-test
    spec:
      containers:
        - name: crashloop-test
          image: busybox:1.36
          command: ["/bin/sh", "-c", "exit 1"]
          securityContext:
            allowPrivilegeEscalation: false
            runAsNonRoot: true
            runAsUser: 1000
            capabilities:
              drop: ["ALL"]
      securityContext:
        seccompProfile:
          type: RuntimeDefault
```

Apply only in a test namespace and delete after validation.

---

## Kafka Lag Manual Test

### Important concept

Kafka lag exists only when a consumer group has committed offsets and new messages are produced after those offsets.

Do not only produce messages. First create a consumer group offset.

### Restricted policy pod

If `kubectl run` is blocked by restricted policy, use the manifest in:

```text
scripts/kafka-lag-test-restricted-pod.yaml
```

Get Kafka namespace and cluster:

```bash
kubectl get kafka -A
```

Set variables:

```bash
export KAFKA_NS="kafka"
export KAFKA_CLUSTER="my-cluster"
export KAFKA_IMAGE="$(kubectl -n "$KAFKA_NS" get pod \
  -l strimzi.io/name=${KAFKA_CLUSTER}-kafka \
  -o jsonpath='{.items[0].spec.containers[0].image}')"
```

Create the pod manifest:

```bash
sed \
  -e "s#REPLACE_KAFKA_NAMESPACE#${KAFKA_NS}#g" \
  -e "s#REPLACE_KAFKA_IMAGE#${KAFKA_IMAGE}#g" \
  scripts/kafka-lag-test-restricted-pod.yaml \
  > /tmp/kafka-lag-tester.yaml

kubectl apply -f /tmp/kafka-lag-tester.yaml
kubectl -n "$KAFKA_NS" wait pod/kafka-lag-tester --for=condition=Ready --timeout=120s
kubectl -n "$KAFKA_NS" exec -it kafka-lag-tester -- /bin/sh
```

Inside the pod:

```bash
export BOOTSTRAP="${KAFKA_CLUSTER}-kafka-bootstrap.${KAFKA_NS}.svc:9092"
export TOPIC="sms-lag-test"
export GROUP="sms-lag-test-group"
```

Create topic:

```bash
/opt/kafka/bin/kafka-topics.sh \
  --bootstrap-server "$BOOTSTRAP" \
  --create \
  --if-not-exists \
  --topic "$TOPIC" \
  --partitions 1 \
  --replication-factor 1
```

Create initial offset:

```bash
echo "bootstrap-message" | /opt/kafka/bin/kafka-console-producer.sh \
  --bootstrap-server "$BOOTSTRAP" \
  --topic "$TOPIC"
```

Consume one message:

```bash
/opt/kafka/bin/kafka-console-consumer.sh \
  --bootstrap-server "$BOOTSTRAP" \
  --topic "$TOPIC" \
  --group "$GROUP" \
  --from-beginning \
  --max-messages 1 \
  --timeout-ms 30000
```

Produce warning lag:

```bash
seq 1 6000 | /opt/kafka/bin/kafka-console-producer.sh \
  --bootstrap-server "$BOOTSTRAP" \
  --topic "$TOPIC"
```

Check lag:

```bash
/opt/kafka/bin/kafka-consumer-groups.sh \
  --bootstrap-server "$BOOTSTRAP" \
  --describe \
  --group "$GROUP"
```

Expected:

```text
LAG around 6000
```

Produce critical lag:

```bash
seq 1 65000 | /opt/kafka/bin/kafka-console-producer.sh \
  --bootstrap-server "$BOOTSTRAP" \
  --topic "$TOPIC"
```

### Verify Prometheus sees lag

```bash
curl -sG 'http://127.0.0.1:9090/api/v1/query' \
  --data-urlencode 'query=sum by (namespace, consumergroup, topic) (kafka_consumergroup_lag{consumergroup="sms-lag-test-group"})' \
  | jq
```

Expected value:

```text
> 5000 for warning
> 60000 for critical
```

### Verify alert state

```bash
curl -s 'http://127.0.0.1:9090/api/v1/alerts' \
  | jq '.data.alerts[] | select(.labels.alertname=="KafkaConsumerLagWarning" or .labels.alertname=="KafkaConsumerLagCritical") | {state: .state, labels: .labels, annotations: .annotations}'
```

Expected SMS description:

```text
Kafka consumer group sms-lag-test-group has current lag 6123 on topic sms-lag-test. Threshold: > 5000.
```

### Cleanup Kafka test

Inside Kafka pod:

```bash
/opt/kafka/bin/kafka-consumer-groups.sh \
  --bootstrap-server "$BOOTSTRAP" \
  --group "$GROUP" \
  --topic "$TOPIC" \
  --reset-offsets \
  --to-latest \
  --execute

/opt/kafka/bin/kafka-topics.sh \
  --bootstrap-server "$BOOTSTRAP" \
  --delete \
  --topic "$TOPIC"
```

Outside pod:

```bash
kubectl -n "$KAFKA_NS" delete pod kafka-lag-tester
```

---

## Troubleshooting

### No SMS received

Check in order:

1. Is webhook running?

```bash
kubectl -n monlog get pod -l app=sms-webhook-alertmanager
```

2. Is webhook receiving POST requests?

```bash
kubectl -n monlog logs deploy/sms-webhook-alertmanager -f
```

3. Is the alert firing in Prometheus?

```bash
curl -s 'http://127.0.0.1:9090/api/v1/alerts' | jq '.data.alerts[] | select(.state=="firing") | .labels.alertname'
```

4. Is Alertmanager receiving it?

```bash
curl -s 'http://127.0.0.1:9093/api/v2/alerts' | jq '.[] | .labels.alertname'
```

5. Does Alertmanager config have correct route?

```bash
grep -nE 'magfa-sms|send_resolved|severity|sms-webhook' /tmp/alertmanager.yaml
```

6. Is matcher wrong?

Wrong:

```yaml
severity = "warning|critical"
```

Correct regex:

```yaml
severity =~ "warning|critical"
```

Preferred final design:

```yaml
severity = "critical"
```

and:

```yaml
severity = "warning"
sms = "true"
```

7. Is `send_resolved` misspelled?

Wrong:

```yaml
send_resolved: flase
```

Correct:

```yaml
send_resolved: false
```

8. Is phone number missing?

Use either:

```yaml
annotations:
  phone_numbers: "0912..."
```

or ConfigMap:

```yaml
DEFAULT_PHONE_NUMBERS: "0912..."
```

9. Is `SMS_DRY_RUN=true`?

```bash
kubectl -n monlog exec deploy/sms-webhook-alertmanager -- printenv SMS_DRY_RUN
```

10. Is Argo CD applying the webhook manifests?

```bash
argocd app get <APP_NAME>
kubectl -n monlog get deploy sms-webhook-alertmanager
```

### Alert is delayed

Check:

- Prometheus alert `state`: pending or firing.
- Rule `for:` duration.
- Prometheus scrape interval.
- Alertmanager `group_wait`.
- Alertmanager `group_interval`.

Default kube-prometheus-stack rules may wait 15 minutes or 1 hour. Use fast custom rules for SMS-critical conditions.

### Duplicate SMS

Common reasons:

- `send_resolved: true`
- webhook returns non-2xx and Alertmanager retries
- multiple similar alert rules exist
- old test rule `Sms-test1` still enabled
- `group_by` missing important labels such as `consumergroup` and `topic`
- `repeat_interval` too short

Final recommended settings:

```yaml
send_resolved: false
PARTIAL_FAILURE_HTTP_CODE: "200"
repeat_interval: 2h or 4h
```

### Garbled SMS text

Common reasons:

- SMS gateway does not handle long multipart messages well.
- alert annotations contain unrendered templates.
- special characters are not supported by provider.

Use:

```yaml
MAX_MESSAGE_CHARS: "700"
```

Keep SMS descriptions short.

### Completed pods triggering PodNotReady

A pod in `0/1 Completed` is normal for Job/CronJob pods. Use the final `SmsFastPodNotReady` rule that excludes Job-owned and Succeeded pods.

---

## Rollback

### Roll back webhook image

```bash
kubectl -n monlog set image deployment/sms-webhook-alertmanager \
  sms-webhook-alertmanager=172.20.117.211:7049/prometheus/webhook/sms-webhook-alertmanager:PREVIOUS_TAG
```

### Roll back with Git

```bash
git log --oneline
git revert <bad_commit>
git push
argocd app sync <APP_NAME>
```

### Disable SMS temporarily

Set dry run:

```bash
kubectl -n monlog patch configmap sms-webhook-config \
  --type merge \
  -p '{"data":{"SMS_DRY_RUN":"true"}}'

kubectl -n monlog rollout restart deployment/sms-webhook-alertmanager
```

Or route SMS to null by temporarily removing `magfa-sms` routes in Alertmanager values and syncing Argo CD.

---

## Production Checklist

Before declaring production-ready:

- [ ] Webhook image uses immutable version tag.
- [ ] Image exists in private registry.
- [ ] No real secrets committed to Git.
- [ ] `sms-webhook-secret` exists in `monlog` namespace.
- [ ] `SMS_DRY_RUN=false`.
- [ ] `PARTIAL_FAILURE_HTTP_CODE=200`.
- [ ] `send_resolved=false`.
- [ ] Alertmanager route uses correct matcher syntax.
- [ ] `group_by` includes `consumergroup` and `topic`.
- [ ] Old always-firing `Sms-test1` removed.
- [ ] Old duplicate Kafka lag rules removed or disabled.
- [ ] `SmsFastPodNotReady` excludes Completed Job pods.
- [ ] Kafka lag SMS includes current lag count.
- [ ] Helm render check passes.
- [ ] Argo CD sync is healthy.
- [ ] Webhook logs show successful SMS send.
- [ ] End-to-end test completed.
- [ ] Kafka lag test completed and cleaned up.

---

## Final Operational Model

Use this SMS policy:

```text
Critical alerts: SMS immediately with short group_wait.
Warning alerts: SMS only when label sms="true" exists.
Resolved alerts: no SMS.
Default noisy alerts: routed to null or handled in Grafana/Alertmanager UI.
```

This prevents SMS storms while keeping critical failures visible.

