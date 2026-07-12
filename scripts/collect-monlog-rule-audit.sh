#!/usr/bin/env bash
set -u

NS="${NS:-monlog}"
OUT="/tmp/monlog-rule-audit-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$OUT"/{k8s,prometheus,argocd,helm,summary}

echo "[INFO] Output directory: $OUT"

kubectl get prometheusrule -A -o yaml > "$OUT/k8s/prometheusrules-all.yaml" 2>&1 || true
kubectl get prometheusrule -A --show-labels > "$OUT/k8s/prometheusrules-list.txt" 2>&1 || true
kubectl get servicemonitor -A -o yaml > "$OUT/k8s/servicemonitors-all.yaml" 2>&1 || true
kubectl get podmonitor -A -o yaml > "$OUT/k8s/podmonitors-all.yaml" 2>&1 || true
kubectl get probe -A -o yaml > "$OUT/k8s/probes-all.yaml" 2>&1 || true
kubectl get scrapeconfig -A -o yaml > "$OUT/k8s/scrapeconfigs-all.yaml" 2>&1 || true
kubectl -n "$NS" get prometheus -o yaml > "$OUT/k8s/prometheus-cr.yaml" 2>&1 || true
kubectl -n "$NS" get alertmanager -o yaml > "$OUT/k8s/alertmanager-cr.yaml" 2>&1 || true
kubectl -n "$NS" get secret alertmanager-kube-prometheus-stack-alertmanager -o jsonpath='{.data.alertmanager\.yaml}' 2>/dev/null | base64 -d > "$OUT/k8s/generated-alertmanager.yaml" 2>/dev/null || true
kubectl -n argocd get applications.argoproj.io -o wide > "$OUT/argocd/applications-wide.txt" 2>&1 || true
kubectl -n argocd get applications.argoproj.io -o yaml > "$OUT/argocd/applications.yaml" 2>&1 || true
kubectl get pods -A -o wide | egrep -i 'argocd|redis|kafka|strimzi|zookeeper|consul|openebs|lvm|logstash|rabbitmq|fluentd' > "$OUT/k8s/target-pods.txt" 2>&1 || true
kubectl get svc -A -o wide | egrep -i 'argocd|redis|kafka|strimzi|zookeeper|consul|openebs|lvm|logstash|rabbitmq|fluentd' > "$OUT/k8s/target-services.txt" 2>&1 || true
kubectl get deploy,sts,ds -A -o wide | egrep -i 'argocd|redis|kafka|strimzi|zookeeper|consul|openebs|lvm|logstash|rabbitmq|fluentd' > "$OUT/k8s/target-workloads.txt" 2>&1 || true
kubectl get pvc,pv -A -o wide > "$OUT/k8s/pv-pvc.txt" 2>&1 || true
kubectl get sc -o wide > "$OUT/k8s/storageclasses.txt" 2>&1 || true
kubectl get kafkas.kafka.strimzi.io -A -o yaml > "$OUT/k8s/strimzi-kafkas.yaml" 2>&1 || true
kubectl get kafkaconnects.kafka.strimzi.io -A -o yaml > "$OUT/k8s/strimzi-kafkaconnects.yaml" 2>&1 || true
kubectl get kafkatopics.kafka.strimzi.io -A -o yaml > "$OUT/k8s/strimzi-kafkatopics.yaml" 2>&1 || true
kubectl get kafkausers.kafka.strimzi.io -A -o yaml > "$OUT/k8s/strimzi-kafkausers.yaml" 2>&1 || true

kubectl get prometheusrule -A -o json 2>/dev/null | jq -r '
  .items[] as $item
  | ($item.metadata.namespace + "/" + $item.metadata.name) as $source
  | $item.spec.groups[]?
  | .name as $group
  | .rules[]?
  | select(.alert != null)
  | [.alert, $source, $group, (.expr|tostring), (.for // "no-for")] | @tsv
' > "$OUT/summary/all-alert-rules.tsv" 2>/dev/null || true

awk -F '\t' '{print $1}' "$OUT/summary/all-alert-rules.tsv" 2>/dev/null | sort | uniq -c | sort -nr > "$OUT/summary/duplicate-alert-names.txt" 2>/dev/null || true

echo "$OUT" > /tmp/monlog-rule-audit-last-dir
cat <<MSG
[INFO] Kubernetes-side collection complete.
[INFO] Now port-forward Prometheus and run the Prometheus API collection commands from the runbook.
[INFO] Last dir written to /tmp/monlog-rule-audit-last-dir
MSG
