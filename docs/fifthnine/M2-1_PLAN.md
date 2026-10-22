# M2-1 implementation plan: Docker and Kubernetes facts, workload graph, override exposure, linkage proposals

Status: authorized by the CEO on 2026-10-22 (DECISIONS.md M2-1-D2) after PR #4 merged as `60118c2`. This document is the implementation plan for the M2-1 PR. Nothing below is merged until independent review and CEO approval.

Scans still report `INCOMPLETE`, there are no rules, and nothing reaches the network.

## Scope

M2-1 delivers six things.

### 1. Schema revision 3 (M2-0-D2)
- Additive fields on `Fact`: `inputs`, `exposure_sources`, `link_ids`, `precedence`, `absent`.
- Additive fields on `DefaultRef`: `applied_version` and `upstream` (`repository`, `tag`, `commit`, `path`, `line`, `anchor`, `verified_at`).
- `derive_verdict()` treats an `absent` declared fact as established.
- JSON Schemas, SCHEMAS.md and validator tests are regenerated in the same commit. SCHEMAS.md records the strict-consumer incompatibility.
- The identifier stays `fifthnine.result/v1`.

### 2. Dockerfile facts (`extract/dockerfile.py`, new)
- **Parser:** a line parser written for this purpose, with no dependency and no execution. It handles:
  - continuations and the `escape` directive;
  - stages and `AS` names;
  - `ARG` and `ENV`;
  - `WORKDIR` relative to the previous one;
  - `EXPOSE`;
  - `ENTRYPOINT` and `CMD` in exec and shell form;
  - `COPY --from`;
  - `USER`.
- **Variable substitution:** `${VAR}`, `${VAR:-x}` and `${VAR:+x}`, only from `ARG` and `ENV` values written in the file. A build argument with no written default leaves the value unresolved, with a reason.
- **Runtime stage:** the last stage. A CI `--target` flag is outside the repository and is recorded as a limit.
- **Not interpreted:** heredocs and `ONBUILD`. Instructions that use them are recorded as unsupported.

### 3. Kubernetes facts and workload graph (`extract/k8s.py`, `resolve/k8s_graph.py`, new)
Built on the existing libyaml compose path, with its line numbers and caps. Kinds covered are those approved in A10: Deployment, Service, Ingress, HPA, PDB and ConfigMap. Secrets are referenced but never read.

**Deployment facts:**
- replicas and strategy;
- pod termination grace period;
- per container:
  - image, ports and named ports;
  - `env` literals and `valueFrom`, and `envFrom` in order, with in-repository status;
  - requests and limits, with `absent` where a field is not set;
  - readiness and liveness probes, with named ports resolved;
  - `preStop` (exec, httpGet, sleep), with only the exact form `sh -c "sleep N"` parsed;
  - command and args;
  - volumes and mounts.

**Other kinds:**
- Service type, selector and port to container-port resolution.
- Ingress host, path, path type and backend, with controller-specific annotations noted (RULES.md N6).
- HPA target, minimum and maximum replicas, and metrics.
- PDB `minAvailable` or `maxUnavailable` and its selector.
- ConfigMap keys, with their lines.

**Graph rules:**
- Selectors match through `matchLabels` only. `matchExpressions` makes the relationship unresolved, with a reason.
- Objects relate only within the same namespace. A set namespace against an unset one is unresolved, not assumed.
- An unset namespace is written `_` in subject keys (M2-0-D4).

### 4. Container environment and override exposure (`resolve/overrides.py`, new)
For each container, M2-1 builds the ordered environment model that M2-2 and M2-3 consume:
- **X1:** literal env values are resolved.
- **X2:** `envFrom` of a ConfigMap in the repository is resolved key by key, and later sources win duplicates.
- **X3:** `envFrom` of an out-of-repository Secret or ConfigMap is recorded as a declared reference. Its keys are unknown, and it is the named `exposure_source` on every dependent fact.
- **X4:** `valueFrom` an out-of-repository source makes that variable unresolved.
- **X5:** a volume mounted at the image `WORKDIR` or `WORKDIR/config` marks the container `file_possible`.

Dependent application facts (Spring, Python) arrive in M2-2 and M2-3. M2-1 tests the model directly.

### 5. Effective container command (`resolve/container_command.py`, new)
- `command` set: `command` plus `args`; the image ENTRYPOINT and CMD are ignored.
- Only `args` set: the image ENTRYPOINT plus `args`.
- Neither set: the image ENTRYPOINT plus CMD.

Shell form is kept as `sh -c` with the original string. Any result that uses the image needs the image link, so the fact carries `link_ids`. With no proposable link, the fact is unresolved, with a reason.

### 6. Linkage proposals (`resolve/linkage.py`, new; moved from M2-5 under M2-0-D4)
The ARCHITECTURE.md evidence rule is implemented unchanged (M2-1-D1). A link is proposed only when all three hold:
- exactly one Dockerfile whose runtime stage copies the artifact of exactly one application module;
- the image basename matches that module's build identity (Maven `artifactId`, Gradle `rootProject.name` as a literal, `pyproject` name), or the container command names a module present in the repository;
- the ports agree where both sides declare them.

Outcomes:
- A proposal is `inferred`. Anything else is `unresolved`, and the evidence that is missing is named.
- No `links accept`, no stale detection and no `.fifthnine.yml` writes; those stay in M2-5.
- Build identity facts are added to support this.

### Supporting work

**Kubernetes defaults table (`resolve/defaults/kubernetes.yaml`).** Every entry cites the upstream line at v1.37.1 and lists `verified_at`. An entry without at least two verified releases fails a test. Entries:
- the termination grace period;
- the Deployment strategy type, `maxUnavailable` and `maxSurge`;
- HPA `minReplicas`, to be verified across 1.34 to 1.37 before it is added;
- Service type.

The supported range 1.34 to 1.37 is recorded in the packaged support matrix as a new `kubernetes-server` entry. This is data, not a schema change.

**Fact registry and determinism (`core/facts.py`, new).**
- Stable `fact_id` from kind, subject, scope and source.
- Sorted output, and input references by fact id.
- Excerpts redacted as in M1.

**Output.**
- `--facts all` emits M2-1 facts and linkages.
- The console gets a short facts summary, with counts by kind and every unresolved relationship with its reason.

**Coverage notices.**
- Helm charts and Kustomize overlays are not rendered (E3). A base directory referenced by an overlay is flagged: its values may be patched outside what was read.
- Unsupported kinds.
- Unresolved selectors and namespaces.
- Unresolved linkage.

**Scorer and provenance oracle (`tests/support/scorer.py`, new).**
- The scorer matches extractor facts to key facts on `(kind, subject, scope)` and applies the M2-0 scoring rules, then evaluates the negative keys with `violates()`.
- The oracle re-reads each emitted fact's source line and checks it for the field name that `kind` declares.

## Acceptance criteria for the M2-1 PR

1. **Key facts are correct.** All 32 Spring and 26 FastAPI key facts tagged M2-1 score correct, on every scored field: value, unit, status, `absent`, exposure and its sources, source line, inputs, precedence, `link_ids`, `overridden_by` and default citation. Negative keys SP-N02, CA-N01 and CA-N03 are not violated. The labelled links SP-L01 and CA-L01 are produced as `inferred`, with the labelled evidence.
2. **Provenance is accurate.** The oracle finds zero mismatches on both samples and on all 26 corpus repositories at their pinned commits. The corpus run is local, because CI cannot fetch the corpus. The figures go in the PR, and CI runs the oracle on the samples and fixtures.
3. **Linkage is measured, not tuned.** The linkage result is reported per corpus repository and per cohort, and compared with the `linkage_expectation` recorded in M2-0. Every disagreement is explained in the PR. The evidence rule is not changed to reduce disagreements; if the M2-0 label was wrong, the label is corrected with the reason recorded.
4. **Unknowns are honest.**
   - Every unresolved fact or relationship gives a reason.
   - Nothing out of the repository is given a value: no Secret contents, no cluster namespace, no Helm values.
   - Kubernetes defaults appear only from the verified table.
5. **Fixtures pass.** Fixtures cover RULES.md X1 to X5, F13 and F14 (effective command), D5 (Recreate), H3 versus H1 (request presence), N6 (Ingress annotations), selector and namespace mismatches, multi-stage Dockerfiles, and `ARG` substitution with and without defaults.
6. **M1 guarantees still hold.**
   - Byte-identical JSON across runs and paths.
   - No network in tests, and `unshare --net` in CI.
   - Exit codes are unchanged.
   - The new Dockerfile parser gets hostile-input tests: very long lines, deep continuations, many stages, NUL bytes, invalid UTF-8.
7. **Schema revision 3 validates.** Emitted output validates against the regenerated JSON Schema and the Pydantic models. SCHEMAS.md records the revision and the compatibility note.
8. **Performance is measured.** Scan time and peak memory are reported for both samples and all 26 corpus repositories, against the M1 baseline.
9. **Review and CI.** An independent reviewer who has not seen the implementation reviews the PR before it is presented. CI is green on all seven jobs.

## Known limitations at M2-1 exit

- Items inside `kind: List` are not inventoried.
- StatefulSet, DaemonSet, Job, CronJob and Argo Rollout stay unsupported kinds, listed in coverage.
- Helm and Kustomize are not rendered (E3).
- The Docker build target, build arguments supplied by CI, and the cluster version are not in the repository.
- Admission webhooks and LimitRanges can change values after the manifest is applied. These stay rule-side assumptions.
- Images built without a Dockerfile (buildpacks, Jib, Gradle or Maven image plugins) cannot be linked under the evidence rule. This is by design (M2-1-D1), and measured.
- Application facts that depend on the container environment arrive in M2-2 (Spring) and M2-3 (Python).

## Sequence and estimate

The work runs in this order:
1. Schema revision 3.
2. Dockerfile parser and facts.
3. Kubernetes extraction and graph.
4. Environment model and exposure.
5. Effective command.
6. Linkage.
7. Scorer, oracle and corpus run.
8. Independent review, fixes, PR.

Estimate: four to five working days, within the approved two-to-three-week M2 envelope. No new runtime dependency is needed; tree-sitter arrives with M2-4.

## HX2

The HX2 protocol is drafted after M2-1 merges, as the CEO directed (M2-1-D3 to D6). It will specify:
- identical repository and source access, with the same investigation budget for both agent arms;
- the independent case author and the independent SRE adjudicator, or a disclosed combined role with blinded adjudication;
- no answer-key access for runners;
- the measures the CEO listed.

It will also define the security requirements in D5 (bounded file access, prompt-injection defences, secret handling, an audit record of transmitted data) as experiment controls, so that they carry into any later implementation proposal. Nothing in HX2 adds AI code to FifthNine.
