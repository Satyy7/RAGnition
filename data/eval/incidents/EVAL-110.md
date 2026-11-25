# EVAL-110: order-worker restarting, orders stuck

_Synthetic incident report (Kestrel Commerce is fictional)._

**Reported at:** 2026-12-18T10:20:00Z

## Reporter note

order-worker pods keep restarting since yesterday afternoon, orders reach the warehouse late. The memory limit was already put back to 1Gi at the start of the month.

## Alerts

- OrderHandoffDelay: oldest order waiting 48m
- KubePodCrashLooping: order-worker

## Logs

```
kubectl describe pod order-worker-7c9d5-k2xq: Last State: Terminated, Reason: OOMKilled, Exit Code: 137; Limits: memory 1Gi
2026-12-18T10:02:51Z order-worker INFO building packing slip order=K-8812031 lines=14 images=14
```
