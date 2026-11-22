# reference-service runbook

## Release

A merge to `main` is a release. Argo CD (`deploy/argocd-application.yaml`) syncs `deploy/overlays/production`. The Deployment rolls one new pod at a time (`maxSurge: 1`, `maxUnavailable: 0`), and an old pod is removed only when a new pod passes its readiness probe. A rollout that makes no progress for 300 seconds is marked failed.

## Rollback

1. Revert the merge commit that changed the image tag in `deploy/base/deployment.yaml`, and merge the revert. Argo CD rolls production back to the previous image.
2. If Argo CD is unavailable, `kubectl -n reference-service rollout undo deployment/reference-service` returns to the previous ReplicaSet. Ten revisions are kept (`revisionHistoryLimit: 10`). Then revert the commit in git so Argo CD does not reapply the bad version.

## Alerts

### QuoteAvailabilityFastBurn

More than 1.44% of /quote requests are failing over both the last hour and the last five minutes. If a release went out in the last hour, roll back. Otherwise, check the `upstream_request_duration_seconds{outcome="error"}` rate to see whether the pricing service is failing.

### QuoteAvailabilitySlowBurn

More than 0.6% of /quote requests are failing over six hours. Find the failing route and status code in `http_requests_total` and open an incident if the cause is not known.

### QuoteLatencyFastBurn

More than 14.4% of /quote requests take longer than 300 ms. Compare `upstream_request_duration_seconds` with `http_request_duration_seconds` to see whether the time is spent upstream.

### QuoteLatencySlowBurn

More than 6% of /quote requests take longer than 300 ms over six hours. Check for saturation (below) and recent releases.

### QuoteNearConcurrencyLimit

An instance has used more than 80% of `MAX_IN_FLIGHT` for ten minutes. Requests above the limit get a 503. Check that the autoscaler is not at `maxReplicas`, and raise it in `deploy/base/hpa.yaml` if it is.
