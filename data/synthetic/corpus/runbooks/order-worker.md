> SYNTHETIC DOCUMENT. Kestrel Commerce is a fictional company created for RAGnition.

# Runbook: order-worker

## Overview
- **Service:** `order-worker`  
- **Team:** Fulfilment  
- **Runtime:** Python 3.12  
- **Primary function:** Consumes the `orders` Kafka topic, builds fulfilment documents, and pushes them to the warehouse system.  
- **SLO:** Orders must be handed to the warehouse within **10 minutes**.  
- **Deployment:** Helm chart values `order-worker.resources` (resource requests/limits) in the `platform-config` repo, applied via Argo CD to the `prod-eu1` Kubernetes cluster (48 nodes).  
- **Dependencies:** Calls to `inventory-api` and `notification-service`. No inbound callers.  
- **Datastore:** Kafka topic `orders`.  

## Key metrics and dashboards
| Metric | Source | Typical threshold | Dashboard |
|--------|--------|-------------------|-----------|
| Pod CPU usage | Prometheus (`container_cpu_usage_seconds_total`) | < 80 % of request | Grafana: *order‑worker CPU* |
| Pod memory usage | Prometheus (`container_memory_working_set_bytes`) | < 90 % of limit (≈ 700 MiB peak) | Grafana: *order‑worker Memory* |
| Kafka consumer lag | Prometheus (`kafka_consumer_group_lag`) for group `order-worker` on topic `orders` | < 5 min lag (≈ 600 msg) | Grafana: *order‑worker Kafka Lag* |
| Pod restarts | Prometheus (`kube_pod_container_status_restarts_total`) | 0 → 1 per hour | Grafana: *order‑worker Restarts* |
| HTTP error rate to `inventory-api` / `notification-service` | Prometheus (`http_requests_total{status=~"5.."}`) | < 1 % | Grafana: *order‑worker Dep Calls* |

All dashboards are available in the shared Grafana instance under the **Fulfilment** folder.

## Alerts and first response
| Alert | Condition | Channel | First response |
|-------|-----------|---------|----------------|
| `OrderWorkerMemoryOOM` | Pod enters `OOMKilled` (exit code 137) or memory usage > 95 % of limit for > 5 min | PagerDuty (Fulfilment on‑call) | Acknowledge, check pod status (`kubectl get pod -n fulfilment -l app=order-worker`) |
| `OrderWorkerCrashLoopBackOff` | Pod status `CrashLoopBackOff` for > 2 min | PagerDuty (Fulfilment on‑call) | Acknowledge, verify if OOM is the cause (see Diagnosis) |
| `OrderWorkerKafkaLag` | Consumer lag > 10 min for > 5 min | Slack #alerts‑fulfilment | Acknowledge, verify pod health |

## Diagnosis
### OOMKilled (exit code 137) → CrashLoopBackOff
1. **Check pod status**  
   ```bash
   kubectl get pod -n fulfilment -l app=order-worker -o wide
   ```
   Look for `STATUS` = `OOMKilled` or `CrashLoopBackOff`.

2. **Inspect recent events**  
   ```bash
   kubectl describe pod <pod-name> -n fulfilment | grep -i oom
   ```

3. **Review memory usage history**  
   - Open the *order‑worker Memory* Grafana panel for the last 30 min.  
   - Confirm that usage spikes to ~700 MiB (as measured in PM‑2026‑005) and exceeds the configured limit.

4. **Check Helm resource limits**  
   ```bash
   helm get values order-worker -n fulfilment | grep -A2 resources
   ```
   Verify the `limits.memory` value.

5. **Confirm no recent code change**  
   ```bash
   git -C /path/to/platform-config diff origin/main -- helm/charts/order-worker/values.yaml
   ```
   Ensure the memory limit was not unintentionally lowered.

### (No other known failure modes)

## Mitigation
1. **Increase memory limit**  
   - Edit `platform-config/helm/charts/order-worker/values.yaml`:
     ```yaml
     order-worker:
       resources:
         limits:
           memory: 1Gi   # or a value safely above 700MiB
         requests:
           memory: 800Mi
     ```
   - Commit and push the change; Argo CD will apply the new manifest.

2. **Roll out the updated deployment** (if immediate rollout is required):
   ```bash
   kubectl rollout restart deployment/order-worker -n fulfilment
   ```

3. **Verify pod recovery**  
   ```bash
   kubectl get pod -n fulfilment -l app=order-worker -w
   ```
   Ensure the pod reaches `Running` without OOM events.

4. **Monitor** the memory dashboard for at least 15 min to confirm stability.

5. **If memory increase is not possible** (e.g., node pressure):
   - Scale the `order-worker` deployment to multiple replicas to spread load:
     ```bash
     kubectl scale deployment/order-worker -n fulfilment --replicas=2
     ```
   - Re‑evaluate Kafka consumer group partitioning to ensure balanced consumption.

## Escalation
- **Primary escalation:** Fulfilment Team Lead (on‑call rotation).  
- **Secondary escalation:** Platform SRE (Kubernetes/cluster health) if the issue persists after memory limit adjustment or if node‑level OOM events are observed.  
- **Tertiary escalation:** Database/Message Queue SREs (PostgreSQL, Kafka) only if downstream errors are detected after the worker stabilizes.  

Escalation should include:
- Alert ID and timestamp.  
- Summary of observed memory usage and current resource limits.  
- Steps already taken (limit change, pod restart).  
- Any relevant logs (`kubectl logs <pod-name> -n fulfilment`).
