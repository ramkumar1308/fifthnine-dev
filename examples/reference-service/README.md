# reference-service

A minimal service, written for this repository, that meets all 16 checks of the FifthNine standard. It shows what passing evidence looks like. It is not a product.

The service has one endpoint, `GET /quote`. It fetches a price from an upstream pricing service and returns it.

Its pipeline is [`.github/workflows/reference-service.yml`](../../.github/workflows/reference-service.yml) at the root of this repository. It runs the unit tests and builds the image on every pull request and push to main that changes this folder.

## Layout

| Path | What |
| --- | --- |
| `service.py` | The service: timeouts and capped retries on the upstream call, health endpoints, Prometheus metrics, JSON logs |
| `tests/` | Unit tests, run by the pipeline |
| `Dockerfile` | Image on a pinned base, running as a non-root user |
| `deploy/base/` | Kubernetes manifests: Deployment, Service, autoscaler, disruption budget, metrics scrape, alert rules, secret reference |
| `deploy/overlays/production/` | The production overlay |
| `deploy/argocd-application.yaml` | Argo CD keeps production in sync with the overlay |
| `slo/slos.yaml` | Availability and latency objectives (OpenSLO) |
| `RUNBOOK.md` | Release, rollback and alert response |

## Service level objectives

- Availability: 99.9% of `/quote` requests succeed over a rolling 30 days.
- Latency: 99% of `/quote` requests complete within 300 ms over a rolling 30 days.

## Platform

The cluster provides Argo CD, the Prometheus Operator, External Secrets Operator and metrics-server. The upstream token is stored in the platform secret store and never appears in this repository.
