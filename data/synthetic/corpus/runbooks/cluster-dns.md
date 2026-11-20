> SYNTHETIC DOCUMENT. Kestrel Commerce is a fictional company created for RAGnition.

# Runbook: cluster-dns

## Overview
**Service:** `cluster-dns` (CoreDNS 1.11)  
**Team:** Platform  
**Purpose:** Provides in‑cluster DNS for service discovery on the production Kubernetes cluster `prod-eu1` (48 nodes).  
**SLO:** DNS lookup p99 < 50 ms.  
**Deployment:** Managed via Helm values in the `platform-config` repo and deployed through Argo CD.  
**Scaling:** Replica count is controlled by the cluster‑proportional‑autoscaler (minimum 6 replicas).

## Key metrics and dashboards
| Metric | Description | Typical threshold |
|--------|-------------|-------------------|
| `coredns_dns_request_duration_seconds{quantile="0.99"}` | 99th‑percentile DNS lookup latency | ≤ 0.05 s (50 ms) |
| `coredns_cpu_seconds_total` | Cumulative CPU time used by CoreDNS pods | Watch for rapid increase |
| `coredns_memory_usage_bytes` | Memory consumption per pod | No hard limit, monitor for spikes |
| `coredns_up` | Pod health (1 = up, 0 = down) | Must be 1 for all replicas |
| `kube_pod_container_status_restarts_total{pod=~"coredns-.*"}` | Restart count per CoreDNS pod | Should be 0 or low |

Dashboards (Prometheus/Grafana) should display the above metrics per replica and aggregate across the service.

## Alerts and first response
| Alert | Condition | Initial action |
|-------|-----------|----------------|
| `ClusterDNSLookupLatencyHigh` | `coredns_dns_request_duration_seconds{quantile="0.99"} > 0.05` for > 5 min | Acknowledge, proceed to Diagnosis |
| `ClusterDNSCPUHigh` | CPU usage > 80 % on any CoreDNS pod for > 2 min | Acknowledge, proceed to Diagnosis |
| `ClusterDNSPodDown` | `coredns_up == 0` for any replica | Acknowledge, proceed to Diagnosis |

**First response checklist**
1. Acknowledge the alert in the incident management system.  
2. Verify the alert is still firing (re‑query Prometheus).  
3. Pull the latest logs from the affected CoreDNS pods.  

## Diagnosis
### CoreDNS CPU saturation
**Symptoms:** DNS lookups time out across the cluster; alert `ClusterDNSCPUHigh` fires; latency alert may also fire.

**Concrete checks:**
1. **Inspect pod CPU usage**
   ```bash
   kubectl top pod -n kube-system -l k8s-app=kube-dns
   ```
   Look for any pod reporting CPU close to or exceeding the node’s allocatable capacity.

2. **Check CoreDNS metrics directly**
   ```bash
   curl -s http://<coredns-pod-ip>:9153/metrics | grep cpu_seconds_total
   ```
   Identify a steep upward slope indicating rapid CPU consumption.

3. **Review recent logs for spikes**
   ```bash
   kubectl logs -n kube-system -l k8s-app=kube-dns --tail=200 | grep -i "error\|panic"
   ```
   Look for repeated errors (e.g., upstream timeouts, malformed queries) that could drive CPU.

4. **Validate autoscaler state**
   ```bash
   kubectl get hpa -n kube-system coredns -o yaml | grep -i replicas
   ```
   Ensure the replica count is at least the minimum (6) and has not been reduced unexpectedly.

5. **Check ndots configuration impact**
   Verify that pods are not generating excessive external lookups due to the default `ndots:5`.  
   ```bash
   kubectl get pod <sample-pod> -o yaml | grep ndots
   ```

## Mitigation
1. **Scale CoreDNS temporarily**
   ```bash
   kubectl scale deployment coredns -n kube-system --replicas=12
   ```
   Increase replicas to distribute load while investigating root cause.

2. **Restart the hottest pod(s)**
   ```bash
   POD=$(kubectl get pods -n kube-system -l k8s-app=kube-dns -o jsonpath="{.items[?(@.metrics.cpu>80)].metadata.name}")
   kubectl delete pod $POD -n kube-system
   ```
   Deleting forces a fresh pod start; the autoscaler will replace it.

3. **Adjust autoscaler minimum if needed**
   ```bash
   kubectl edit hpa coredns -n kube-system
   ```
   Raise `minReplicas` (e.g., from 6 to 8) to provide headroom.

4. **Mitigate ndots‑related query explosion**
   - Review Helm values for CoreDNS `ndots` setting; consider lowering to 2‑3 if many services perform external lookups.
   - Deploy the change via Argo CD after testing.

5. **If the issue is query‑pattern related** (e.g., a misbehaving service generating many external lookups), identify the offending service via logs and coordinate with its owning team to fix the DNS usage pattern.

## Escalation
| Escalation level | When to trigger | Contact |
|------------------|-----------------|---------|
| **Platform on‑call** | After initial mitigation steps fail to bring CPU < 80 % and latency < 50 ms within 10 min | PagerDuty: `platform-oncall` |
| **Platform lead** | If the autoscaler is not responding or scaling limits are hit | Slack: `#platform-leads` |
| **Site Reliability Manager** | If the outage persists > 30 min or impacts multiple critical services | Email: `sre-manager@kestrel.com` |
| **Kubernetes Cluster Owner** | If node‑level resource exhaustion is suspected (e.g., node CPU pressure) | PagerDuty: `cluster-owner-eu1` |

When escalating, include:
- Current CPU and latency metrics.
- Actions already taken (scaling, restarts, config changes).
- Any identified offending workloads.
