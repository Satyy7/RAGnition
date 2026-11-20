# EVAL-005: order-worker crash looping

_Synthetic incident report (Kestrel Commerce is fictional)._

**Reported at:** 2026-11-13T11:05:00Z

## Reporter note

Orders are not reaching the warehouse. order-worker pods keep restarting.

## Alerts

- OrderHandoffDelay: oldest unprocessed order 38m
- KubePodCrashLooping: order-worker-7c9d6 restarts=14

## Logs

```
kubectl describe pod order-worker-7c9d6: Last State: Terminated  Reason: OOMKilled  Exit Code: 137
kubectl describe pod order-worker-7c9d6: Limits: cpu 1, memory 512Mi
2026-11-13T10:58:12Z order-worker INFO building fulfilment document order=K-99120412 lines=37 gift_message=true
```
