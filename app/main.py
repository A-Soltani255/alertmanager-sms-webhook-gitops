import logging
import os
import re
from datetime import datetime
from typing import Dict, Iterable, List, Tuple

import pytz
import requests
from flask import Flask, jsonify, request
from urllib3.exceptions import InsecureRequestWarning
import urllib3

urllib3.disable_warnings(InsecureRequestWarning)


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    try:
        return int(value)
    except ValueError:
        return default


LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOCAL_TIMEZONE = os.getenv("LOCAL_TIMEZONE", "Asia/Tehran")
SMS_GATEWAY_URL = os.getenv("SMS_GATEWAY_URL", "")
SMS_USERNAME = os.getenv("SMS_USERNAME", "")
SMS_PASSWORD = os.getenv("SMS_PASSWORD", "")
SMS_SOURCE = os.getenv("SMS_SOURCE", "")
SMS_DRY_RUN = env_bool("SMS_DRY_RUN", False)
SMS_TIMEOUT_SECONDS = env_int("SMS_TIMEOUT_SECONDS", 15)
MAX_MESSAGE_CHARS = env_int("MAX_MESSAGE_CHARS", 700)
PARTIAL_FAILURE_HTTP_CODE = env_int("PARTIAL_FAILURE_HTTP_CODE", 200)
DEFAULT_PHONE_NUMBERS = os.getenv("DEFAULT_PHONE_NUMBERS", "")
CLUSTER_NAME = os.getenv("CLUSTER_NAME", "")

SUCCESS_CODES_RAW = os.getenv("SMS_SUCCESS_STATUS_CODES", "200")
SMS_SUCCESS_STATUS_CODES = set()
for item in SUCCESS_CODES_RAW.split(","):
    item = item.strip()
    if item.isdigit():
        SMS_SUCCESS_STATUS_CODES.add(int(item))
if not SMS_SUCCESS_STATUS_CODES:
    SMS_SUCCESS_STATUS_CODES = {200}

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("sms-webhook-alertmanager")

app = Flask(__name__)


def mask_secret(value: str, visible: int = 3) -> str:
    if not value:
        return ""
    value = str(value)
    if len(value) <= visible:
        return "*" * len(value)
    return "*" * (len(value) - visible) + value[-visible:]


def mask_phone(value: str) -> str:
    return mask_secret(value, 4)


def split_phones(raw: str) -> List[str]:
    if not raw:
        return []
    phones = []
    for part in re.split(r"[,;\s]+", raw):
        part = part.strip()
        if part:
            phones.append(part)
    return list(dict.fromkeys(phones))


def parse_timestamp(ts: str) -> Tuple[str, str]:
    if not ts or ts.startswith("0001-01-01"):
        return "Unknown", "Unknown"
    try:
        # Alertmanager normally sends RFC3339 timestamps such as 2026-07-11T14:50:38Z.
        normalized = ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        local_tz = pytz.timezone(LOCAL_TIMEZONE)
        dt_local = dt.astimezone(local_tz)
        return dt_local.strftime("%Y-%m-%d"), dt_local.strftime("%H:%M:%S")
    except Exception:
        logger.exception("failed_to_parse_timestamp timestamp=%s", ts)
        return "Unknown", "Unknown"


def render_leftover_templates(text: str, labels: Dict[str, str]) -> str:
    """Best-effort cleanup for accidentally unrendered Prometheus label templates.

    Correct rules should render annotations in Prometheus before they reach this webhook. This function exists only as
    a defensive layer for legacy rules that accidentally produced literal {{$labels.x}} text or SMS mojibake.
    """
    if not text:
        return ""

    def repl(match: re.Match) -> str:
        key = match.group(1)
        return str(labels.get(key, f"<missing:{key}>"))

    text = re.sub(r"\{\{\s*\$labels\.([A-Za-z_][A-Za-z0-9_]*)\s*\}\}", repl, text)
    text = re.sub(r"Ä+¤labels\.([A-Za-z_][A-Za-z0-9_]*)ñ+Ñ?", repl, text)
    return text


def pick_namespace(labels: Dict[str, str]) -> str:
    return (
        labels.get("namespace")
        or labels.get("exported_namespace")
        or labels.get("kubernetes_namespace")
        or labels.get("ingress_namespace")
        or labels.get("job_namespace")
        or "Unknown"
    )


def pick_affected_object(labels: Dict[str, str]) -> str:
    namespace = pick_namespace(labels)
    candidates = [
        ("pod", "Pod"),
        ("deployment", "Deployment"),
        ("statefulset", "StatefulSet"),
        ("daemonset", "DaemonSet"),
        ("replicaset", "ReplicaSet"),
        ("job_name", "Job"),
        ("job", "Job"),
        ("ingress", "Ingress"),
        ("service", "Service"),
        ("consumergroup", "Kafka ConsumerGroup"),
        ("topic", "Kafka Topic"),
    ]
    for key, kind in candidates:
        if labels.get(key):
            if kind.startswith("Kafka"):
                return f"{kind} {labels.get(key)}"
            return f"{kind} {namespace}/{labels.get(key)}"
    if labels.get("node"):
        return f"Node {labels.get('node')}"
    if labels.get("name"):
        return f"Object {namespace}/{labels.get('name')}"
    return "Unknown"


def build_message(alert: Dict) -> str:
    labels = alert.get("labels", {}) or {}
    annotations = alert.get("annotations", {}) or {}

    state = alert.get("status", "Unknown")
    alertname = labels.get("alertname", "Unknown")
    severity = labels.get("severity", "Unknown")
    namespace = pick_namespace(labels)
    affected_object = pick_affected_object(labels)

    summary = render_leftover_templates(annotations.get("summary", "No summary provided"), labels)
    description = render_leftover_templates(annotations.get("description", "No description provided"), labels)

    if CLUSTER_NAME:
        description = description.replace("on cluster .", f"on cluster {CLUSTER_NAME}.")
        description = description.replace("cluster .", f"cluster {CLUSTER_NAME}.")

    if str(state).lower() == "resolved":
        date, time = parse_timestamp(alert.get("endsAt", ""))
    else:
        date, time = parse_timestamp(alert.get("startsAt", ""))

    msg = f"""Alert: {alertname}
State: {state}
Severity: {severity}
Namespace: {namespace}
Object: {affected_object}
Summary: {summary}
Description: {description}
Date: {date}
Time: {time}"""

    msg = msg.replace("\r\n", "\n").replace("\r", "\n")
    if MAX_MESSAGE_CHARS > 0 and len(msg) > MAX_MESSAGE_CHARS:
        msg = msg[: MAX_MESSAGE_CHARS - 15].rstrip() + "\n...truncated"
    return msg


def get_alert_phone_numbers(alert: Dict) -> List[str]:
    labels = alert.get("labels", {}) or {}
    annotations = alert.get("annotations", {}) or {}
    raw = annotations.get("phone_numbers") or labels.get("phone") or DEFAULT_PHONE_NUMBERS
    return split_phones(raw)


def send_one_sms(phone: str, message: str) -> Dict:
    if SMS_DRY_RUN:
        logger.info("dry_run_sms destination=%s chars=%s", mask_phone(phone), len(message))
        return {"phone": mask_phone(phone), "status": "dry_run", "http_status": None}

    if not SMS_GATEWAY_URL:
        raise RuntimeError("SMS_GATEWAY_URL is not configured")

    params = {
        "username": SMS_USERNAME,
        "password": SMS_PASSWORD,
        "from": SMS_SOURCE,
        "to": phone,
        "message": message,
    }

    logger.info("sending_sms destination=%s chars=%s", mask_phone(phone), len(message))
    response = requests.get(
        SMS_GATEWAY_URL,
        params=params,
        timeout=SMS_TIMEOUT_SECONDS,
        verify=False,
    )
    logger.info("sms_gateway_response destination=%s http_status=%s", mask_phone(phone), response.status_code)
    return {
        "phone": mask_phone(phone),
        "status": "success" if response.status_code in SMS_SUCCESS_STATUS_CODES else "failure",
        "http_status": response.status_code,
        "body_sample": response.text[:200],
    }


@app.get("/healthz")
def healthz():
    return jsonify({"status": "ok"})


@app.get("/readyz")
def readyz():
    return jsonify({"status": "ready", "dry_run": SMS_DRY_RUN})


@app.post("/send_sms")
def send_sms():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"status": "failure", "message": "invalid or missing JSON body"}), 400

    alerts = payload.get("alerts") or []
    if not isinstance(alerts, list):
        return jsonify({"status": "failure", "message": "alerts must be a list"}), 400

    logger.info("received_alertmanager_webhook alerts_count=%s", len(alerts))
    results = []
    failures = 0

    for index, alert in enumerate(alerts, start=1):
        labels = alert.get("labels", {}) or {}
        alertname = labels.get("alertname", "Unknown")
        phones = get_alert_phone_numbers(alert)
        if not phones:
            logger.warning("no_phone_numbers alert_index=%s alertname=%s", index, alertname)
            results.append({"alert_index": index, "alertname": alertname, "status": "skipped_no_phone"})
            continue

        message = build_message(alert)
        for phone in phones:
            try:
                result = send_one_sms(phone, message)
                result.update({"alert_index": index, "alertname": alertname})
                if result["status"] == "failure":
                    failures += 1
                results.append(result)
            except Exception as exc:
                failures += 1
                logger.exception("sms_send_failed alert_index=%s alertname=%s destination=%s", index, alertname, mask_phone(phone))
                results.append({
                    "alert_index": index,
                    "alertname": alertname,
                    "phone": mask_phone(phone),
                    "status": "failure",
                    "error": str(exc),
                })

    status = "success" if failures == 0 else "partial_failure"
    http_code = 200 if failures == 0 else PARTIAL_FAILURE_HTTP_CODE
    return jsonify({"status": status, "failures": failures, "results": results}), http_code


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
