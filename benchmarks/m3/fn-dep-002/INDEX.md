# Probe alignment benchmark: case index

36 cases. Counts: ALIGNED 16, MISALIGNED 15, NOT_DETERMINABLE 5. Each Boot version (3.2.12, 3.3.13, 3.4.13, 3.5.16) has 9 cases.

Each case lives at `cases/<id>/`: `repo/` is what the tool under test sees, `case.yaml` holds the expected outcome, and `runtime/` (NOT_DETERMINABLE cases only) holds the alternative manifests the harness applies one at a time.

| id | boot | expected | dimensions |
|---|---|---|---|
| pb-01 | 3.2.12 | ALIGNED | management-port-different, container-port-undeclared, probe-groups-auto-kubernetes, startup-probe |
| pb-02 | 3.3.13 | MISALIGNED | management-port-8080-server-port-unset, management-port-same, context-path-same-port |
| pb-03 | 3.4.13 | ALIGNED | placeholder-default, env-override-deployment, management-port-equals-default-8080, management-port-different, context-path-separate-management-port |
| pb-04 | 3.5.16 | MISALIGNED | management-port-negative, named-probe-port |
| pb-05 | 3.2.12 | NOT_DETERMINABLE | envfrom-secret-missing, env-override-relaxed-binding, probe-groups-auto-kubernetes, startup-probe |
| pb-06 | 3.3.13 | ALIGNED | management-port-different, management-server-base-path, properties-format, startup-probe |
| pb-07 | 3.4.13 | MISALIGNED | profile-specific-file, spring-profiles-active-deployment, endpoints-web-base-path, named-probe-port |
| pb-08 | 3.5.16 | ALIGNED | endpoints-web-base-path-root, health-path-mapping, probe-groups-auto-kubernetes, named-probe-port, startup-probe |
| pb-09 | 3.2.12 | MISALIGNED | management-port-equal-server-port, management-server-base-path, management-port-same, properties-format |
| pb-10 | 3.3.13 | ALIGNED | context-path-separate-management-port, management-port-different, probes-enabled-true |
| pb-11 | 3.4.13 | MISALIGNED | context-path-same-port, management-port-same, startup-probe, named-probe-port |
| pb-12 | 3.5.16 | MISALIGNED | context-path-separate-management-port, context-path-applied-wrongly, management-port-different, named-probe-port |
| pb-13 | 3.2.12 | ALIGNED | probes-enabled-false, root-health-path |
| pb-14 | 3.3.13 | MISALIGNED | probes-enabled-false, group-not-enabled, availability-state-indicators-without-groups |
| pb-15 | 3.4.13 | ALIGNED | probes-enabled-unset-kubernetes, enable-service-links-false, default-exposure, startup-probe |
| pb-16 | 3.5.16 | MISALIGNED | probes-enabled-unset-kubernetes, cloud-platform-override, group-not-enabled, liveness-vs-readiness-paths |
| pb-17 | 3.2.12 | ALIGNED | add-additional-paths, management-port-different, named-probe-port, startup-probe |
| pb-18 | 3.3.13 | NOT_DETERMINABLE | envfrom-configmap-missing, env-override-relaxed-binding, management-port-different |
| pb-19 | 3.4.13 | MISALIGNED | add-additional-paths, management-port-different, named-probe-port, startup-probe-ok-liveness-misaligned, probes-enabled-true |
| pb-20 | 3.5.16 | ALIGNED | endpoint-access-3.4-plus, access-default-none, access-read-only |
| pb-21 | 3.2.12 | MISALIGNED | add-additional-paths, exposure-include, endpoint-not-exposed, properties-format |
| pb-22 | 3.3.13 | ALIGNED | endpoint-access-pre-3.4, access-property-ignored, startup-probe |
| pb-23 | 3.4.13 | MISALIGNED | exposure-exclude, exposure-include-wildcard, endpoint-not-exposed |
| pb-24 | 3.5.16 | MISALIGNED | endpoint-enabled-legacy-on-3.4-plus, enabled-by-default-false, endpoint-not-accessible |
| pb-25 | 3.2.12 | MISALIGNED | named-probe-port, named-port-wrong-container-port, management-port-different |
| pb-26 | 3.3.13 | ALIGNED | named-probe-port, management-port-different, probes-enabled-true, custom-probe-group-members, startup-probe |
| pb-27 | 3.4.13 | NOT_DETERMINABLE | configmapkeyref-missing, spring-profiles-active-deployment, profile-specific-file, named-probe-port, management-port-different, startup-probe |
| pb-28 | 3.5.16 | ALIGNED | profile-specific-file, spring-profiles-active-deployment, env-override-relaxed-binding, management-port-different |
| pb-29 | 3.2.12 | ALIGNED | envfrom-configmap-in-repo, configmapkeyref-in-repo, env-override-relaxed-binding, endpoints-web-base-path, management-port-different, startup-probe |
| pb-30 | 3.3.13 | MISALIGNED | command-line-args-deployment, dockerfile-shell-form-entrypoint, named-probe-port |
| pb-31 | 3.4.13 | ALIGNED | command-line-args-deployment, kubernetes-var-expansion, named-probe-port, startup-probe |
| pb-32 | 3.5.16 | NOT_DETERMINABLE | configmap-volume-missing, external-config-file, management-port-different, named-probe-port, startup-probe |
| pb-33 | 3.2.12 | MISALIGNED | startup-probe-misaligned-others-ok, management-port-different, named-probe-port |
| pb-34 | 3.3.13 | ALIGNED | multi-document-yaml, on-cloud-platform-activation, management-port-different, probe-groups-auto-kubernetes |
| pb-35 | 3.4.13 | ALIGNED | configmap-volume-in-repo, external-config-file, management-port-different, named-probe-port |
| pb-36 | 3.5.16 | NOT_DETERMINABLE | envfrom-configmap-optional-missing, env-override-relaxed-binding, named-probe-port, startup-probe |

## NOT_DETERMINABLE deciding inputs

| id | missing input | runtime files |
|---|---|---|
| pb-05 | Secret `app-runtime-env` (envFrom, required) | `secret-a.yaml` -> ALIGNED, `secret-b.yaml` -> MISALIGNED |
| pb-18 | ConfigMap `app-env` (envFrom, required) | `configmap-a.yaml` -> ALIGNED, `configmap-b.yaml` -> MISALIGNED |
| pb-27 | ConfigMap `release-channel` key `profile` (configMapKeyRef for SPRING_PROFILES_ACTIVE) | `configmap-a.yaml` -> ALIGNED, `configmap-b.yaml` -> MISALIGNED |
| pb-32 | ConfigMap `contract-store-config` (volume at /app/config) | `configmap-a.yaml` -> ALIGNED, `configmap-b.yaml` -> MISALIGNED |
| pb-36 | ConfigMap `lead-scoring-overrides` (envFrom, optional) | `configmap-a.yaml` -> ALIGNED, `configmap-b.yaml` -> MISALIGNED |
