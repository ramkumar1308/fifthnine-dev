# AI coding-agent baseline prompt (frozen)

Model: the strongest model available to the CTO session at run time, named in the results. One fresh agent per batch of six cases, with no access to `case.yaml`, `runtime/`, this benchmark's results, the FifthNine source tree or the FifthNine docs. Tools: read the case repositories, read the upstream Spring Boot source trees provided for 3.2.12, 3.3.13, 3.4.13 and 3.5.16, and web search or fetch. No Maven, no Docker, no cluster.

The text below is sent verbatim, with `<CASE_DIRS>` replaced by the six repository paths and `<SB_SRC>` by the upstream source path.

---

You are reviewing Kubernetes deployments of Spring Boot services before release. For each repository below, answer one question: in the production deployment described by the repository, will every Kubernetes `httpGet` probe (readiness, liveness and startup, where present) reach a Spring Boot Actuator health endpoint that returns HTTP 200 when the application is healthy?

Repositories: <CASE_DIRS>

Each repository contains a Maven build, Spring configuration under `src/main/resources`, a Dockerfile and Kubernetes manifests under `k8s/`. Assume the image is built from this Dockerfile and that only the manifests in `k8s/` are applied, plus anything the manifests reference that exists outside the repository.

Upstream Spring Boot sources for the four versions in use are at <SB_SRC>. You may read them and the Spring Boot and Kubernetes documentation.

For each repository, return a JSON object:

```
{"repo": "<dir name>",
 "verdict": "ALIGNED" | "MISALIGNED" | "NOT_DETERMINABLE",
 "probes": [{"kind": "readiness|liveness|startup", "port": <number>, "path": "<path>", "expected_status": "200|404|connection_refused|other"}],
 "deciding_input": "<only for NOT_DETERMINABLE: the input outside the repository that decides it>",
 "confidence": "high" | "medium" | "low",
 "reason": "<two to four sentences citing the files and lines you relied on>"}
```

Use `NOT_DETERMINABLE` only when the answer depends on input that is not in the repository. Do not guess. Return a JSON array with one object per repository and nothing else.
