# EVAL-123: Order emails bouncing

_Synthetic incident report (Kestrel Commerce is fictional)._

**Reported at:** 2027-01-19T09:00:00Z

## Reporter note

No order confirmations delivered since yesterday afternoon. There was a template engine release yesterday but it was rolled back.

## Alerts

- EmailSendFailures: 100% permanent failures

## Logs

```
2027-01-19T08:40:02Z notification-service error postrelay send failed status=550 reason="5.7.26 Unauthenticated email from mail.kestrel.example is not accepted"
```
