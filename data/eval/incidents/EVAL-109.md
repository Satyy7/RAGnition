# EVAL-109: Order confirmation emails delayed during campaign

_Synthetic incident report (Kestrel Commerce is fictional)._

**Reported at:** 2026-12-17T11:00:00Z

## Reporter note

Confirmation emails are 40 minutes behind. Marketing started the Christmas campaign at 10:00. We deployed a new email template yesterday.

## Alerts

- EmailQueueBacklog: 61,000 messages, oldest 41m

## Logs

```
2026-12-17T10:31:12Z notification-service warn sender status=429 retry_in=30s honoured_retry_after=true attempt=2
2026-12-17T10:31:12Z notification-service info postrelay account usage 100/100 msg/s
```
