> SYNTHETIC DOCUMENT. Kestrel Commerce is a fictional company created for RAGnition.

# Runbook: notification-service

## Overview
- **Service:** `notification-service`  
- **Team:** Customer Comms  
- **Runtime:** Node.js 22  
- **Purpose:** Sends order confirmation and shipping emails via the PostRelay email API.  
- **SLO:** Confirmation email must be sent within **5 minutes** of an order being placed.  
- **Deployment:** Managed by Argo CD; Helm values stored in the `platform-config` repository.  
- **Dependencies:** Called by `order-worker`; no internal datastore dependencies.  

## Key metrics and dashboards
| Metric | Description | Typical Threshold | Dashboard |
|--------|-------------|-------------------|-----------|
| `notification_service_success_total` | Count of successfully sent emails | N/A | Grafana – Notification Service Overview |
| `notification_service_retry_total` | Count of retries performed | < 100 /min (baseline) | Grafana – Notification Service Overview |
| `notification_service_429_total` | Count of HTTP 429 responses from PostRelay | < 5 /min | Grafana – Notification Service Overview |
| `notification_service_backlog_seconds` | Age of the oldest unsent email in the internal queue | < 300 s (5 min) | Grafana – Notification Service Overview |
| `postrelay_api_rate_limit` (external) | Current usage vs. 100 msg/s limit (if exposed) | ≤ 100 msg/s | External monitoring (if available) |

## Alerts and first response
| Alert | Condition | Channel | First response |
|-------|-----------|---------|----------------|
| `NotificationServiceHigh429` | `notification_service_429_total` > 5 /min for 5 min | PagerDuty (on‑call) | Acknowledge alert, check recent logs for 429 spikes. |
| `NotificationServiceBacklog` | `notification_service_backlog_seconds` > 300 s for 2 min | PagerDuty (on‑call) | Acknowledge alert, verify queue depth and retry rate. |
| `NotificationServiceErrorRate` | Error rate > 2 % of total sends for 5 min | PagerDuty (on‑call) | Acknowledge, inspect logs for unexpected failures. |

## Diagnosis

### Provider throttling (HTTP 429)
1. **Check recent logs** for 429 responses:  
   ```bash
   kubectl logs -l app=notification-service -c app --since=15m | grep '"status":429' 
   ```
2. **Verify Retry‑After header** values:  
   ```bash
   kubectl exec -it $(kubectl get pod -l app=notification-service -o jsonpath='{.items[0].metadata.name}') -- curl -I https://api.postrelay.com/v1/send | grep -i retry-after
   ```
3. **Confirm rate limit usage** (if PostRelay provides a metrics endpoint):  
   ```bash
   curl -s https://api.postrelay.com/v1/usage | jq '.messages_per_second'
   ```
4. **Inspect retry queue depth**:  
   ```bash
   kubectl exec -it $(kubectl get pod -l app=notification-service -o jsonpath='{.items[0].metadata.name}') -- redis-cli LLEN notification_retry_queue
   ```
5. **Validate exponential backoff configuration** (Helm values):  
   ```bash
   helm get values notification-service -n prod-eu1 | grep backoff
   ```

### Retry amplification / send backlog
1. **Measure backlog age**:  
   ```bash
   kubectl exec -it $(kubectl get pod -l app=notification-service -o jsonpath='{.items[0].metadata.name}') -- redis-cli LINDEX notification_retry_queue 0
   ```
   (Check timestamp field and compute age.)
2. **Check retry rate**:  
   ```bash
   kubectl logs -l app=notification-service -c app --since=5m | grep 'retrying email' | wc -l
   ```
3. **Confirm jitter is applied** (look for jitter value in logs):  
   ```bash
   kubectl logs -l app=notification-service -c app --since=5m | grep 'jitter'
   ```
4. **Review recent scaling events** for the service (Argo CD sync history):  
   ```bash
   argocd app history notification-service -n prod-eu1
   ```

## Mitigation
1. **Honor Retry‑After**: Ensure the service respects the `Retry-After` header before scheduling the next attempt. If the header is missing, default to a 30‑second backoff.
2. **Adjust backoff parameters** (Helm values) to increase base delay or jitter:
   ```yaml
   retry:
     baseDelayMs: 5000   # increase from default if needed
     maxDelayMs: 60000
     jitterFactor: 0.3
   ```
   Apply with:
   ```bash
   helm upgrade notification-service ./chart -f values.yaml -n prod-eu1
   ```
3. **Throttle outbound requests** to stay below the shared 100 msg/s limit:
   - Enable a local token bucket limiter (e.g., 80 msg/s) in the service configuration.
4. **Scale the service** temporarily to increase parallel processing capacity:
   ```bash
   kubectl scale deployment notification-service -n prod-eu1 --replicas=6
   ```
5. **Clear excessive backlog** (only if safe and after confirming no in‑flight emails):
   ```bash
   kubectl exec -it $(kubectl get pod -l app=notification-service -o jsonpath='{.items[0].metadata.name}') -- redis-cli DEL notification_retry_queue
   ```
6. **Coordinate with Marketing team** to stagger campaign sends if they share the same PostRelay account, reducing concurrent load.

## Escalation
| Escalation Level | Trigger | Owner | Contact |
|------------------|---------|-------|---------|
| **L2 – Platform SRE** | Mitigation steps above do not reduce 429 rate or backlog within 15 min | Platform SRE lead | Slack `#sre-platform` |
| **L3 – Service Owner (Customer Comms)** | Persistent backlog > 10 min or SLO breach after L2 | Customer Comms lead | Email `customer-comms@kestrel.com` |
| **L4 – External Provider** | PostRelay rate limit cannot be increased and service impact remains critical | PostRelay support | Open ticket via support portal, reference limit 100 msg/s |

Escalation actions:
1. Notify the next level via PagerDuty and Slack channel.
2. Provide a concise status: current 429 rate, backlog age, actions taken.
3. If escalating to PostRelay, include recent `Retry-After` values and request a temporary limit increase.
