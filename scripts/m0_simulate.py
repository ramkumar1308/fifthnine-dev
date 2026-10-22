"""M0 SIMULATED output generator (schema revision 2).

No scanning happens here. Facts and per-rule check outcomes below were written by hand from the dev
answer keys in benchmarks/dev/*.expected.yaml. Verdicts are NOT hand-written: each finding's verdict,
assumptions and verdict_if_assumptions_hold come from the normative derive_verdict() in
src/fifthnine/schema/v1.py, so the simulation exercises the D2/D9 logic the engine must use.
All outputs carry "simulated": true and a SIMULATED banner.

    PYTHONPATH=src python scripts/m0_simulate.py
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from fifthnine.schema.v1 import (
    AssessmentLimits,
    Assumption,
    AssumptionKind,
    AssumptionState,
    BaselineStatus,
    ComponentSupport,
    CoverageNotice,
    DefaultRef,
    Domain,
    Fact,
    FactStatus,
    Finding,
    Inventory,
    Linkage,
    LinkStatus,
    OverrideExposure,
    Reference,
    Remediation,
    ScanInfo,
    ScanResult,
    Severity,
    SourceRef,
    Subject,
    Summary,
    SupportLevel,
    ToolInfo,
    Verdict,
    blocking_ineligible_reasons,
    derive_status,
    derive_verdict,
)

OUT = Path("docs/fifthnine/m0/simulated")
TOOL = ToolInfo(version="0.1.0-simulated", rule_pack_versions={"fifthnine-core": "0.1.0"})
BANNER = "SIMULATED OUTPUT (M0, schema rev 2). Facts written by hand from the dev answer key; verdicts computed by derive_verdict(). No scanner exists yet."
PROJECTED = "Projection: assumes M2/M3 runtime fixtures pass for the detected versions, so they are reported as 'tested'. Today no version is tested (see the *.today-untested.* file)."
TODAY = "Current state: src/fifthnine/data/support_matrix.v1.yaml lists no tested versions, so every detected version is 'untested' (D9)."
DIGEST = "sha256:11ecbffae90f37ebec63f2d982acb1e2"
CFG = ".fifthnine.yml"


def fp(rule: str, key: str) -> str:
    return "sha256:" + hashlib.sha256(f"{rule}\n{key}".encode()).hexdigest()[:32]


def src(path: str, a: int, b: int | None = None, excerpt: str | None = None) -> SourceRef:
    return SourceRef(path=path, line_start=a, line_end=b or a, excerpt=excerpt)


def ref(title: str, url: str | None = None, source_path: str | None = None) -> Reference:
    return Reference(title=title, url=url, source_path=source_path)


K8S_LIFECYCLE = ref("Kubernetes: Pod termination", "https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/")
K8S_HPA = ref("Kubernetes: Horizontal Pod Autoscaling", "https://kubernetes.io/docs/concepts/workloads/autoscaling/horizontal-pod-autoscale/")
K8S_PDB = ref("Kubernetes: Disruptions", "https://kubernetes.io/docs/concepts/workloads/pods/disruptions/")
K8S_DEPLOY = ref("Kubernetes: Deployments, rolling update", "https://kubernetes.io/docs/concepts/workloads/controllers/deployment/")
SB_CLOUD = ref("Spring Boot: Kubernetes container lifecycle", "https://docs.spring.io/spring-boot/how-to/deployment/cloud.html")
SB_ACTUATOR = ref("Spring Boot: Actuator monitoring paths", "https://docs.spring.io/spring-boot/reference/actuator/monitoring.html")


def comp(name: str, version: str | None, where: SourceRef | None, tested: bool) -> ComponentSupport:
    return ComponentSupport(component=name, version=version, version_source=where,
                            support=SupportLevel.TESTED if tested else SupportLevel.UNTESTED,
                            tested_versions=[version] if tested and version else [], matrix_entry=name)


@dataclass
class Spec:
    """Hand-written rule outcome. The verdict is derived, not stated."""

    rule_id: str
    domain: Domain
    severity: Severity
    title: str
    message: str
    subject: Subject
    locations: list[SourceRef]
    requirement: str
    check: str  # fail | pass | indeterminate
    facts: list[str] = field(default_factory=list)
    uses_link: bool = False
    components: list[str] = field(default_factory=list)
    rule_assumptions: list[Assumption] = field(default_factory=list)
    computation: str | None = None
    remediation: Remediation | None = None
    references: list[Reference] = field(default_factory=list)
    unknown_reason: str | None = None


def assume(kind: AssumptionKind, statement: str) -> Assumption:
    return Assumption(kind=kind, statement=statement, material=True)


def build_finding(spec: Spec, facts: dict[str, Fact], link: Linkage, comps: dict[str, ComponentSupport],
                  declarations: dict[AssumptionKind, SourceRef]) -> Finding:
    applicability = [comps[c] for c in spec.components]
    links = [link] if spec.uses_link else []
    verdict, assumptions, if_hold = derive_verdict(spec.check, [facts[f] for f in spec.facts], links, applicability,
                                                   declarations, spec.rule_assumptions)
    open_reasons = [a.statement for a in assumptions if a.material and a.state is AssumptionState.OPEN]
    hint = {AssumptionKind.LINKAGE_INFERRED: " (run `fifthnine links accept`)",
            AssumptionKind.EXTERNAL_OVERRIDE_ENV: " (declare external_overrides in .fifthnine.yml if true)",
            AssumptionKind.CLUSTER_ADMISSION_DEFAULT: " (declare platform.admission_defaults if true)",
            AssumptionKind.UNTESTED_VERSION: " (version not in the tested support matrix)"}
    reasons = [a.statement + hint.get(a.kind, "") for a in assumptions if a.material and a.state is AssumptionState.OPEN]
    inel = blocking_ineligible_reasons(verdict, rule_blocking_capable=False, linkages=links, suppressed=False)
    return Finding(fingerprint=fp(spec.rule_id, spec.subject.key), rule_id=spec.rule_id, rule_version="0.1.0", domain=spec.domain,
                   verdict=verdict, severity=spec.severity if verdict is not Verdict.SATISFIED else None, title=spec.title,
                   message=spec.message, subject=spec.subject, locations=spec.locations, fact_ids=spec.facts,
                   requirement=spec.requirement, computation=spec.computation, assumptions=assumptions,
                   verdict_if_assumptions_hold=if_hold, conditional_reasons=reasons if verdict is Verdict.CONDITIONAL else [],
                   unknown_reason=spec.unknown_reason, applicability=applicability, remediation=spec.remediation,
                   references=spec.references, blocking_eligible=not inel,
                   blocking_ineligible_reasons=inel if verdict in (Verdict.VIOLATION, Verdict.CONDITIONAL) else [])
    _ = open_reasons


# --------------------------------------------------------------------------- sample A


def orders(mode: str) -> ScanResult:
    """mode: default | links-accepted | declared | today-untested"""
    accepted = mode in ("links-accepted", "declared")
    tested = mode != "today-untested"
    d = "k8s:Deployment/commerce/orders-service/orders-service"
    pom_v = src("pom.xml", 10, 10, "<version>3.4.13</version>")
    comps = {"spring-boot": comp("spring-boot", "3.4.13", pom_v, tested),
             "spring-framework": comp("spring-framework", "6.2.15", pom_v, tested),
             "java": comp("java", "21", src("pom.xml", 20), tested),
             "kubernetes-api": comp("kubernetes-api", "apps/v1, autoscaling/v2, policy/v1", None, tested)}
    link = Linkage(
        link_id="L1", kind="workload_container_to_source_root",
        status=LinkStatus.DECLARED if accepted else LinkStatus.INFERRED, from_ref=d,
        to_ref="source:. (Maven orders-service) via Dockerfile",
        evidence=["single Dockerfile; runtime stage copies target/orders-service-*.jar (Dockerfile:10)",
                  "image repository basename 'orders-service' equals Maven artifactId (pom.xml:15)",
                  "Dockerfile EXPOSE 8080 8081 equals containerPorts 8080, 8081 and server/management ports"],
        accept_command=None if accepted else "fifthnine links accept",
        declared_in=src(CFG, 6, 21) if accepted else None, accepted_by="links-accept" if accepted else None,
        accepted_on="2026-10-21" if accepted else None, evidence_digest=DIGEST if accepted else None,
        current_digest=DIGEST)
    declarations: dict[AssumptionKind, SourceRef] = {}
    if mode == "declared":
        declarations = {AssumptionKind.EXTERNAL_OVERRIDE_ENV: src(CFG, 23, 24),
                        AssumptionKind.CLUSTER_ADMISSION_DEFAULT: src(CFG, 25, 26)}
    env = OverrideExposure.ENV_POSSIBLE
    app = Subject(kind="spring.application", key="spring:orders-service", display="orders-service")
    ctr = Subject(kind="k8s.container", key=d, display="orders-service/orders-service")
    F = [
        Fact(fact_id="f-boot", kind="stack.spring_boot.version", subject=Subject(kind="build", key="maven:orders-service", display="orders-service"),
             value="3.4.13", status=FactStatus.DECLARED, source=pom_v, extractor="maven-pom-v1"),
        Fact(fact_id="f-shutdown-mode", kind="spring.property", subject=app, value={"key": "server.shutdown", "value": "graceful"},
             status=FactStatus.DEFAULT, override_exposure=env,
             default_ref=DefaultRef(framework="spring-boot", version_range=">=3.4.0,<3.6.0",
                                    reference=ref("ServerProperties.shutdown default", source_path="spring-boot@v3.4.13:SB-AC/web/ServerProperties.java:117")),
             extractor="spring-defaults-v1"),
        Fact(fact_id="f-phase-timeout", kind="spring.property", subject=app, value={"key": "spring.lifecycle.timeout-per-shutdown-phase", "value": "45s"},
             unit="s", status=FactStatus.DECLARED, override_exposure=env, source=src("src/main/resources/application.yml", 5), extractor="spring-config-v1"),
        Fact(fact_id="f-prestop", kind="k8s.container.prestop_seconds", subject=ctr, value=10, unit="s", status=FactStatus.DECLARED,
             source=src("k8s/deployment.yaml", 57, 59, 'command: ["sh", "-c", "sleep 10"]'), extractor="k8s-v1"),
        Fact(fact_id="f-grace", kind="k8s.pod.termination_grace_seconds", subject=ctr, value=30, unit="s", status=FactStatus.DEFAULT,
             default_ref=DefaultRef(framework="kubernetes", version_range="all", reference=K8S_LIFECYCLE), extractor="k8s-defaults-v1"),
        Fact(fact_id="f-server-port", kind="spring.property", subject=app, value={"key": "server.port", "value": 8080}, status=FactStatus.DECLARED,
             override_exposure=env, source=src("src/main/resources/application.yml", 12), extractor="spring-config-v1"),
        Fact(fact_id="f-mgmt-port", kind="spring.property", subject=app, value={"key": "management.server.port", "value": 8081},
             status=FactStatus.DECLARED, override_exposure=env, source=src("src/main/resources/application.yml", 16), extractor="spring-config-v1"),
        Fact(fact_id="f-readiness", kind="k8s.probe.readiness", subject=ctr, value={"type": "httpGet", "port": 8080, "path": "/actuator/health/readiness"},
             status=FactStatus.DECLARED, source=src("k8s/deployment.yaml", 44, 47), extractor="k8s-v1"),
        Fact(fact_id="f-liveness", kind="k8s.probe.liveness", subject=ctr, value={"type": "httpGet", "port": "management(8081)", "path": "/actuator/health/liveness"},
             status=FactStatus.DECLARED, source=src("k8s/deployment.yaml", 50, 53), extractor="k8s-v1"),
        Fact(fact_id="f-profile", kind="spring.active_profiles", subject=app, value=["prod"],
             status=FactStatus.DECLARED if accepted else FactStatus.INFERRED, source=src("k8s/deployment.yaml", 32, 33), extractor="spring-profiles-v1"),
        Fact(fact_id="f-exposure", kind="spring.property", subject=app, value={"key": "management.endpoints.web.exposure.include", "value": "*"},
             status=FactStatus.DECLARED, override_exposure=env, scope={"profile": "prod"}, source=src("src/main/resources/application-prod.yml", 7),
             extractor="spring-config-v1"),
        Fact(fact_id="f-read-timeout", kind="spring.property", subject=app, value={"key": "spring.http.client.read-timeout", "value": "2s"},
             status=FactStatus.DECLARED, override_exposure=env, source=src("src/main/resources/application.yml", 9), extractor="spring-config-v1"),
        Fact(fact_id="f-hpa", kind="k8s.hpa.metric", subject=Subject(kind="k8s.hpa", key="k8s:HorizontalPodAutoscaler/commerce/orders-service", display="hpa/orders-service"),
             value={"resource": "cpu", "target": "Utilization", "averageUtilization": 70, "min": 4, "max": 10}, status=FactStatus.DECLARED,
             source=src("k8s/hpa.yaml", 11, 19), extractor="k8s-v1"),
        Fact(fact_id="f-requests", kind="k8s.container.requests", subject=ctr, value={"memory": "768Mi"}, status=FactStatus.DECLARED,
             source=src("k8s/deployment.yaml", 40, 41), extractor="k8s-v1"),
        Fact(fact_id="f-rollout", kind="k8s.deployment.rolling_update",
             subject=Subject(kind="k8s.deployment", key="k8s:Deployment/commerce/orders-service", display="deploy/orders-service"),
             value={"maxUnavailable": "50%", "maxSurge": "25%", "replicas": 4}, status=FactStatus.DECLARED, source=src("k8s/deployment.yaml", 9, 14), extractor="k8s-v1"),
        Fact(fact_id="f-pdb", kind="k8s.pdb.min_available", subject=Subject(kind="k8s.pdb", key="k8s:PodDisruptionBudget/commerce/orders-service", display="pdb/orders-service"),
             value="75%", status=FactStatus.DECLARED, source=src("k8s/pdb.yaml", 7), extractor="k8s-v1"),
        Fact(fact_id="f-resttemplate", kind="http_client.construction",
             subject=Subject(kind="http_client", key="java:com.acme.orders.client.PaymentClient#restTemplate", display="PaymentClient.restTemplate"),
             value={"type": "RestTemplate", "factory": "SimpleClientHttpRequestFactory (HttpAccessor default)", "read_timeout": None, "connect_timeout": None},
             status=FactStatus.DECLARED, source=src("src/main/java/com/acme/orders/client/PaymentClient.java", 12, 12, "private final RestTemplate restTemplate = new RestTemplate();"),
             extractor="java-http-clients-v1"),
        Fact(fact_id="f-restclient-static", kind="http_client.construction",
             subject=Subject(kind="http_client", key="java:com.acme.orders.client.ShippingQuoteClient#restClient", display="ShippingQuoteClient.restClient"),
             value={"type": "RestClient", "builder": "static RestClient.builder()", "factory": "classpath detection"}, status=FactStatus.DECLARED,
             source=src("src/main/java/com/acme/orders/client/ShippingQuoteClient.java", 13), extractor="java-http-clients-v1"),
    ]
    facts = {f.fact_id: f for f in F}

    specs = [
        Spec("FN-CAP-002a", Domain.CAPACITY, Severity.HIGH, "HPA cannot scale on cpu: container has no cpu request",
             "HPA orders-service targets 70% cpu utilization, but container orders-service declares no cpu request. Utilization is undefined, so the HPA takes no action for this metric.",
             Subject(kind="k8s.hpa", key="k8s:HorizontalPodAutoscaler/commerce/orders-service", display="hpa/orders-service"),
             [src("k8s/hpa.yaml", 16, 19), src("k8s/deployment.yaml", 40, 41)],
             "Resource metrics with target type Utilization require a request for that resource on every container in the pod.",
             "fail", facts=["f-hpa", "f-requests"], components=["kubernetes-api"],
             rule_assumptions=[assume(AssumptionKind.CLUSTER_ADMISSION_DEFAULT, "no namespace LimitRange or webhook injects a default cpu request at admission")],
             remediation=Remediation(summary="Add resources.requests.cpu to every container in the pod template, or switch the HPA target to AverageValue.", references=[K8S_HPA]),
             references=[K8S_HPA]),
        Spec("FN-DEP-001", Domain.DEPLOYMENT_SAFETY, Severity.MEDIUM, "Rollout can drop below the PDB minAvailable floor",
             "PDB orders-service requires 75% of pods available, but the rolling update allows 50% unavailable. PDBs do not limit rolling updates.",
             Subject(kind="k8s.deployment", key="k8s:Deployment/commerce/orders-service", display="deploy/orders-service"),
             [src("k8s/deployment.yaml", 13), src("k8s/pdb.yaml", 7), src("k8s/hpa.yaml", 11, 12)],
             "Ready pods during rollout (n - floor(maxUnavailable)) should not fall below ceil(minAvailable) for any replica count the HPA allows.",
             "fail", facts=["f-rollout", "f-pdb", "f-hpa"], components=["kubernetes-api"],
             computation="n=4: 4-2=2 < 3; n=6: 6-3=3 < 5; n=10: 10-5=5 < 8. Violated for every n in 4..10.",
             remediation=Remediation(summary="Set maxUnavailable to 25% or lower, or lower minAvailable if 75% was not a real floor.", references=[K8S_DEPLOY, K8S_PDB]),
             references=[K8S_PDB, K8S_DEPLOY]),
        Spec("FN-RES-001", Domain.RESILIENCE, Severity.HIGH, "PaymentClient can wait forever on the payment service",
             "`new RestTemplate()` uses SimpleClientHttpRequestFactory with no connect or read timeout. spring.http.client.* does not apply to a RestTemplate constructed directly.",
             Subject(kind="http_client", key="java:com.acme.orders.client.PaymentClient#restTemplate", display="PaymentClient.restTemplate"),
             [src("src/main/java/com/acme/orders/client/PaymentClient.java", 12)],
             "Outbound HTTP calls on a request path need a bounded connect and read timeout.",
             "fail", facts=["f-resttemplate"], components=["spring-framework"],
             remediation=Remediation(summary="Inject RestTemplateBuilder (or RestClient.Builder) so spring.http.client.* apply, or set a factory with explicit timeouts.",
                                     references=[ref("HttpAccessor default request factory", source_path="spring-framework@v6.2.15:spring-web/.../support/HttpAccessor.java:56")])),
        Spec("FN-RES-001", Domain.RESILIENCE, Severity.MEDIUM, "ShippingQuoteClient likely has no read timeout",
             "Static RestClient.builder() ignores spring.http.client.* and picks a factory by classpath. With the declared dependencies that is the JDK client, which has no read timeout by default.",
             Subject(kind="http_client", key="java:com.acme.orders.client.ShippingQuoteClient#restClient", display="ShippingQuoteClient.restClient"),
             [src("src/main/java/com/acme/orders/client/ShippingQuoteClient.java", 13)],
             "Outbound HTTP calls on a request path need a bounded read timeout.",
             "fail", facts=["f-restclient-static"], components=["spring-framework"],
             rule_assumptions=[assume(AssumptionKind.CLASSPATH_UNRESOLVED, "no transitive dependency adds Apache HttpClient 5, Jetty or Reactor Netty (any of them changes the factory)")],
             remediation=Remediation(summary="Inject RestClient.Builder instead of calling RestClient.builder().")),
        Spec("FN-DEP-003", Domain.DEPLOYMENT_SAFETY, Severity.MEDIUM, "Shutdown budget 55s exceeds pod grace period 30s",
             "preStop sleeps 10s, then Spring waits up to 45s per shutdown phase for in-flight requests. Kubernetes sends SIGKILL at 30s.",
             ctr, [src("src/main/resources/application.yml", 5), src("k8s/deployment.yaml", 57, 59)],
             "preStop duration plus graceful drain budget must fit inside terminationGracePeriodSeconds.",
             "fail", facts=["f-boot", "f-shutdown-mode", "f-phase-timeout", "f-prestop", "f-grace"], uses_link=True, components=["spring-boot", "kubernetes-api"],
             computation="10s preStop + 45s timeout-per-shutdown-phase = 55s > 30s terminationGracePeriodSeconds (field absent, Kubernetes default)",
             remediation=Remediation(summary="Set terminationGracePeriodSeconds to at least 60, or lower timeout-per-shutdown-phase to 20s or less.", references=[SB_CLOUD, K8S_LIFECYCLE]),
             references=[SB_CLOUD, K8S_LIFECYCLE]),
        Spec("FN-DEP-002", Domain.DEPLOYMENT_SAFETY, Severity.HIGH, "Readiness probe targets a port where actuator is not served",
             "management.server.port is 8081, so /actuator/** is served only on 8081. The readiness probe calls 8080/actuator/health/readiness and will get 404.",
             Subject(kind="k8s.container", key=d + "#readiness", display="orders-service readinessProbe"),
             [src("k8s/deployment.yaml", 44, 47), src("src/main/resources/application.yml", 16)],
             "With a DIFFERENT management port, probe groups are served at management.server.port + management.server.base-path + /actuator/health/<group>.",
             "fail", facts=["f-readiness", "f-server-port", "f-mgmt-port"], uses_link=True, components=["spring-boot"],
             computation="ManagementPortType: management 8081 != server 8080 -> DIFFERENT. Expected 8081/actuator/health/readiness; probe 8080/actuator/health/readiness.",
             remediation=Remediation(summary="Point the probe at port: management (8081), as the livenessProbe does, or enable add-additional-paths and probe 8080/readyz.", references=[SB_ACTUATOR]),
             references=[SB_ACTUATOR]),
        Spec("FN-CFG-001", Domain.PRODUCTION_CONFIG, Severity.MEDIUM, "Production profile exposes all actuator endpoints",
             "SPRING_PROFILES_ACTIVE=prod activates application-prod.yml, which sets exposure.include to \"*\" (heapdump, env, configprops, loggers). The comment says this was temporary (INC-2291).",
             Subject(kind="spring.application", key="spring:orders-service#actuator-exposure", display="orders-service actuator exposure"),
             [src("src/main/resources/application-prod.yml", 7), src("k8s/deployment.yaml", 32, 33)],
             "The effective production profile should expose only the endpoints operations needs.",
             "fail", facts=["f-profile", "f-exposure"], uses_link=True, components=["spring-boot"],
             remediation=Remediation(summary="Remove the override from application-prod.yml; the base file exposes health,info,prometheus."), references=[SB_ACTUATOR]),
        Spec("FN-CAP-002b", Domain.CAPACITY, Severity.LOW, "Deployment sets spec.replicas while an HPA manages it",
             "Each kubectl apply resets replicas to 4, undoing the HPA until it reconciles.",
             Subject(kind="k8s.deployment", key="k8s:Deployment/commerce/orders-service", display="deploy/orders-service"),
             [src("k8s/deployment.yaml", 9)], "Kubernetes recommends removing spec.replicas when an HPA is enabled.",
             "fail", facts=["f-rollout", "f-hpa"], components=["kubernetes-api"],
             rule_assumptions=[assume(AssumptionKind.PLATFORM_BEHAVIOR, "the deploy tool applies spec.replicas (Argo CD and Flux can be set to ignore it)")],
             remediation=Remediation(summary="Remove spec.replicas from the manifest.", references=[K8S_HPA])),
        Spec("FN-RES-001", Domain.RESILIENCE, Severity.MEDIUM, "InventoryClient read timeout 2s",
             "Injected RestClient.Builder applies spring.http.client.read-timeout=2s (Boot 3.4+).",
             Subject(kind="http_client", key="java:com.acme.orders.client.InventoryClient#restClient", display="InventoryClient.restClient"),
             [src("src/main/java/com/acme/orders/client/InventoryClient.java", 15), src("src/main/resources/application.yml", 9)],
             "Outbound HTTP calls on a request path need a bounded read timeout.", "pass", facts=["f-read-timeout"], components=["spring-boot"]),
        Spec("FN-DEP-002", Domain.DEPLOYMENT_SAFETY, Severity.MEDIUM, "Liveness probe matches management endpoint",
             "port: management resolves to 8081; /actuator/health/liveness exists because probe groups are auto-enabled on Kubernetes.",
             Subject(kind="k8s.container", key=d + "#liveness", display="orders-service livenessProbe"),
             [src("k8s/deployment.yaml", 50, 53)], "Probe must target the resolved health group URL.",
             "pass", facts=["f-liveness", "f-mgmt-port"], uses_link=True, components=["spring-boot"]),
        Spec("FN-NET-001", Domain.NETWORK_EXPOSURE, Severity.HIGH, "Management port not reachable from outside the cluster",
             "8081 is only behind ClusterIP Service orders-service-metrics. The Ingress routes /orders to port http (8080), where actuator is not served.",
             Subject(kind="spring.application", key="spring:orders-service#management", display="orders-service management surface"),
             [src("k8s/service.yaml", 15, 29), src("k8s/ingress.yaml", 12, 18)],
             "Management endpoints should not be reachable through LoadBalancer, NodePort or Ingress.",
             "pass", facts=["f-mgmt-port"], uses_link=True, components=["spring-boot", "kubernetes-api"],
             rule_assumptions=[assume(AssumptionKind.OUT_OF_REPO_ROUTING, "no Ingress or Gateway outside this repository routes to port 8081")]),
    ]
    findings = [build_finding(s, facts, link, comps, declarations) for s in specs]

    coverage = []
    if not accepted:
        coverage.append(CoverageNotice(code="FN-OPS-002", scope="k8s/deployment.yaml",
                                       message="Service linkage L1 inferred from 3 signals. Run `fifthnine links accept` to review and declare it; the command records an evidence digest and marks the link stale if that evidence changes.",
                                       affects_rules=["FN-DEP-003", "FN-DEP-002", "FN-CFG-001", "FN-NET-001"]))
    if mode != "declared":
        coverage.append(CoverageNotice(code="FN-OPS-002", scope="k8s/deployment.yaml:37",
                                       message="Secret orders-service-secrets is referenced by envFrom but not stored in the repository. Findings that depend on Spring settings it could override stay CONDITIONAL; the repository-derived computation is kept. Declare external_overrides in .fifthnine.yml if out-of-repo sources do not override them.",
                                       affects_rules=["FN-DEP-003", "FN-DEP-002", "FN-CFG-001", "FN-RES-001"]))
    else:
        coverage.append(CoverageNotice(code="COV-TEAM-DECLARATION", scope=".fifthnine.yml:23-26",
                                       message="Findings below rely on team declarations: external_overrides mode none (orders-service) and platform.admission_defaults none. If either is wrong, those VIOLATIONs are not established.",
                                       affects_rules=["FN-DEP-003", "FN-DEP-002", "FN-CFG-001", "FN-CAP-002a"]))
    if not tested:
        coverage.append(CoverageNotice(code="COV-UNTESTED-VERSION", scope="pom.xml:10",
                                       message="spring-boot 3.4.13, spring-framework 6.2.15, java 21 and the Kubernetes APIs are inside the approved ranges but not tested (src/fifthnine/data/support_matrix.v1.yaml). Failing checks are reported as CONDITIONAL.",
                                       affects_rules=[s.rule_id for s in specs]))
    coverage += [
        CoverageNotice(code="FN-OPS-002", scope="pom.xml", message="Transitive Maven dependencies not resolved (no network, no build).", affects_rules=["FN-RES-001"]),
        CoverageNotice(code="COV-DELEGATED", scope="repository", message="Kubernetes security posture, probe presence and generic resource requests/limits are left to existing scanners.",
                       delegated_to=["kube-linter", "checkov"]),
        CoverageNotice(code="FN-OPS-001", domain=Domain.DATA_COMPATIBILITY, scope="repository", message="Data and migration compatibility is not assessed in v0.1.", delegated_to=["squawk", "atlas"]),
        CoverageNotice(code="COV-OBSERVATION", domain=Domain.RESILIENCE, scope="src/main/java/com/acme/orders/client/PaymentClient.java:20",
                       message="POST /v1/charges is retried up to 4 times; no idempotency evidence found. Not judged in v0.1 (FN-RES-003 candidate)."),
    ]
    return build(findings, coverage, [link], F, comps,
                 ScanInfo(mode="full", profile="prod", profile_source="k8s-env:k8s/deployment.yaml:33", config_file=CFG if accepted else None),
                 files=(20, 18), rules_evaluated=8, rules_na=2, note=PROJECTED if tested else TODAY)


# --------------------------------------------------------------------------- sample B


def catalog() -> ScanResult:
    d = "k8s:Deployment/default/catalog-api/api"
    comps = {"python": comp("python", "3.12", src("Dockerfile", 1), True),
             "fastapi": comp("fastapi", "0.159.1", src("pyproject.toml", 7), True),
             "tenacity": comp("tenacity", "9.2.1", src("pyproject.toml", 13), True),
             "httpx": comp("httpx", "0.28.1", src("pyproject.toml", 11), True),
             "gunicorn": comp("gunicorn", "26.2.0", src("pyproject.toml", 10), True),
             "kubernetes-api": comp("kubernetes-api", "apps/v1, autoscaling/v2, policy/v1", None, True)}
    link = Linkage(link_id="L1", kind="workload_container_to_source_root", status=LinkStatus.INFERRED, from_ref=d,
                   to_ref="source:. (python catalog-api, module app.main:app)",
                   evidence=["Deployment command names app.main:app; app/main.py defines app = FastAPI(...)", "single Dockerfile copies ./app",
                             "image basename catalog-api equals pyproject name catalog-api"],
                   accept_command="fifthnine links accept", current_digest="sha256:5d0c39a1f0b7e2c84a6f1e9b3d7c2a18")
    ctr = Subject(kind="k8s.container", key=d, display="catalog-api/api")
    F = [
        Fact(fact_id="f-graceful", kind="process.graceful_timeout_seconds", subject=ctr, value=60, unit="s", status=FactStatus.DECLARED,
             source=src("k8s/deployment.yaml", 27, 27, '- "--graceful-timeout=60"'), extractor="gunicorn-cli-v1"),
        Fact(fact_id="f-grace", kind="k8s.pod.termination_grace_seconds", subject=ctr, value=30, unit="s", status=FactStatus.DECLARED,
             source=src("k8s/deployment.yaml", 17), extractor="k8s-v1"),
        Fact(fact_id="f-retry-wh", kind="retry_policy", subject=Subject(kind="retry_policy", key="py:app.clients.inventory.fetch_warehouses", display="inventory.fetch_warehouses"),
             value={"library": "tenacity", "stop": "stop_never (default)", "wait": "wait_exponential(multiplier=0.5, max=10)"}, status=FactStatus.DECLARED,
             source=src("app/clients/inventory.py", 21, 24), extractor="python-tenacity-v1"),
        Fact(fact_id="f-retry-stock", kind="retry_policy", subject=Subject(kind="retry_policy", key="py:app.clients.inventory.fetch_stock", display="inventory.fetch_stock"),
             value={"library": "tenacity", "stop": "stop_after_attempt(3)", "wait": "wait_fixed(1)"}, status=FactStatus.DECLARED,
             source=src("app/clients/inventory.py", 9, 14), extractor="python-tenacity-v1"),
        Fact(fact_id="f-inv-timeout", kind="http_client.read_timeout", subject=Subject(kind="http_client", key="py:app.clients.inventory._client", display="inventory._client"),
             value=2.0, unit="s", status=FactStatus.DECLARED, source=src("app/clients/inventory.py", 6), extractor="python-http-clients-v1"),
        Fact(fact_id="f-deadline", kind="python.outer_deadline", subject=Subject(kind="route", key="py:app.routes.products.product_page", display="GET /v1/products/{sku}"),
             value=4.0, unit="s", status=FactStatus.DECLARED, override_exposure=OverrideExposure.ENV_POSSIBLE,
             source=src("app/settings.py", 8, 8, "product_page_budget_seconds: float = 4.0"), extractor="python-asyncio-deadline-v1"),
        Fact(fact_id="f-readiness", kind="k8s.probe.readiness", subject=ctr, value={"type": "httpGet", "port": "http(8000)", "path": "/readyz"},
             status=FactStatus.DECLARED, source=src("k8s/deployment.yaml", 40, 43), extractor="k8s-v1"),
        Fact(fact_id="f-liveness", kind="k8s.probe.liveness", subject=ctr, value={"type": "httpGet", "port": "http(8000)", "path": "/healthz"},
             status=FactStatus.DECLARED, source=src("k8s/deployment.yaml", 45, 48), extractor="k8s-v1"),
        Fact(fact_id="f-routes", kind="fastapi.routes", subject=Subject(kind="fastapi.app", key="py:app.main:app", display="app.main:app"),
             value=["GET /healthz", "GET /ready", "GET /v1/products/{sku}", "GET /v1/products/{sku}/availability"], status=FactStatus.DECLARED,
             source=src("app/main.py", 5, 7), extractor="python-fastapi-routes-v1"),
        Fact(fact_id="f-command", kind="k8s.container.command", subject=ctr, value=["gunicorn", "app.main:app", "--worker-class=uvicorn_worker.UvicornWorker", "..."],
             status=FactStatus.DECLARED, source=src("k8s/deployment.yaml", 21, 28), extractor="k8s-v1"),
    ]
    facts = {f.fact_id: f for f in F}
    specs = [
        Spec("FN-RES-004", Domain.RESILIENCE, Severity.HIGH, "fetch_warehouses retries forever",
             "@retry has no stop=, so tenacity uses stop_never. It is called from GET /v1/products/{sku}/availability: during an inventory outage each request retries indefinitely.",
             Subject(kind="retry_policy", key="py:app.clients.inventory.fetch_warehouses", display="inventory.fetch_warehouses"),
             [src("app/clients/inventory.py", 21, 25), src("app/routes/products.py", 22)],
             "Retries on a request path must be bounded by attempts or time.", "fail", facts=["f-retry-wh"], components=["tenacity"],
             remediation=Remediation(summary="Add stop=stop_after_attempt(3) or stop=stop_before_delay(<budget>).",
                                     references=[ref("tenacity default stop", source_path="tenacity==9.2.1:tenacity/__init__.py:262")])),
        Spec("FN-DEP-003", Domain.DEPLOYMENT_SAFETY, Severity.MEDIUM, "gunicorn graceful timeout 60s exceeds pod grace period 30s",
             "gunicorn waits up to 60s for workers to finish on SIGTERM, but the pod is killed at 30s.", ctr,
             [src("k8s/deployment.yaml", 27), src("k8s/deployment.yaml", 17)], "Drain budget must fit inside terminationGracePeriodSeconds.",
             "fail", facts=["f-graceful", "f-grace"], components=["gunicorn", "kubernetes-api"],
             computation="60s graceful-timeout > 30s terminationGracePeriodSeconds; no preStop. CLI flag is applied after GUNICORN_CMD_ARGS, so the out-of-repo Secret cannot override it.",
             remediation=Remediation(summary="Raise terminationGracePeriodSeconds to 70 or lower --graceful-timeout to 25.", references=[K8S_LIFECYCLE]), references=[K8S_LIFECYCLE]),
        Spec("FN-RES-002", Domain.RESILIENCE, Severity.MEDIUM, "fetch_stock retry policy cannot finish inside the 4s page budget",
             "product_page wraps fetch_stock in asyncio.timeout(4.0). fetch_stock can take 3 attempts x 2s + 2 x 1s = 8s, so the deadline cancels it during the second attempt.",
             Subject(kind="route", key="py:app.routes.products.product_page#fetch_stock", display="GET /v1/products/{sku} -> fetch_stock"),
             [src("app/routes/products.py", 13, 14), src("app/clients/inventory.py", 9, 15), src("app/settings.py", 8)],
             "Worst-case retry window must fit inside the explicit outer deadline.", "fail",
             facts=["f-retry-stock", "f-inv-timeout", "f-deadline"], components=["tenacity", "httpx"],
             computation="3 x 2.0s timeout + 2 x 1.0s wait = 8.0s > 4.0s (deadline from the Settings default)",
             remediation=Remediation(summary="Use stop=stop_before_delay(3) with a 1s per-attempt timeout, or 2 attempts with 1.5s timeout and 0.5s wait.")),
        Spec("FN-DEP-002", Domain.DEPLOYMENT_SAFETY, Severity.HIGH, "Readiness probe path /readyz is not served by the app",
             "The probe calls /readyz; app.main:app registers /healthz and /ready.",
             Subject(kind="k8s.container", key=d + "#readiness", display="catalog-api readinessProbe"),
             [src("k8s/deployment.yaml", 40, 43), src("app/routes/health.py", 11)],
             "httpGet probe path must match a route registered by the effective application.", "fail",
             facts=["f-readiness", "f-routes"], uses_link=True, components=["fastapi"],
             remediation=Remediation(summary="Change the probe path to /ready, or add a /readyz route.")),
        Spec("FN-CAP-002b", Domain.CAPACITY, Severity.LOW, "Deployment sets spec.replicas while an HPA manages it", "Each kubectl apply resets replicas to 3.",
             Subject(kind="k8s.deployment", key="k8s:Deployment/default/catalog-api", display="deploy/catalog-api"),
             [src("k8s/deployment.yaml", 8)], "Kubernetes recommends removing spec.replicas when an HPA is enabled.", "fail",
             components=["kubernetes-api"],
             rule_assumptions=[assume(AssumptionKind.PLATFORM_BEHAVIOR, "the deploy tool applies spec.replicas (Argo CD and Flux can be set to ignore it)")]),
        Spec("FN-DEP-002", Domain.DEPLOYMENT_SAFETY, Severity.MEDIUM, "Liveness probe /healthz matches a registered route", "GET /healthz is registered by app.routes.health.",
             Subject(kind="k8s.container", key=d + "#liveness", display="catalog-api livenessProbe"),
             [src("k8s/deployment.yaml", 45, 48), src("app/routes/health.py", 6)], "Probe path must match a registered route.", "pass",
             facts=["f-liveness", "f-routes"], uses_link=True, components=["fastapi"]),
        Spec("FN-CFG-001", Domain.PRODUCTION_CONFIG, Severity.MEDIUM, "Effective container command has no reload or debug mode",
             "The Deployment command (gunicorn) replaces the Dockerfile CMD, so the image's uvicorn --reload never runs in the cluster. FastAPI(debug=...) is not set.",
             Subject(kind="k8s.container", key=d + "#command", display="catalog-api effective command"),
             [src("k8s/deployment.yaml", 21, 28), src("Dockerfile", 11)], "Production command must not enable auto-reload or debug.", "pass",
             facts=["f-command", "f-routes"], uses_link=True, components=["fastapi", "gunicorn"]),
    ]
    findings = [build_finding(s, facts, link, comps, {}) for s in specs]
    coverage = [
        CoverageNotice(code="FN-OPS-002", scope="k8s/deployment.yaml", message="Service linkage L1 inferred from 3 signals. Run `fifthnine links accept` to review and declare it.",
                       affects_rules=["FN-DEP-002", "FN-CFG-001"]),
        CoverageNotice(code="COV-DELEGATED", domain=Domain.RESILIENCE, scope="app/clients/pricing.py:7, app/clients/recommendations.py:6",
                       message="Python HTTP calls without a bounded timeout were detected and are left to existing scanners: requests.get without timeout (pricing.py:7), httpx client with timeout=None (recommendations.py:6). Run `ruff check --select S113`.",
                       delegated_to=["ruff:S113", "bandit:B113"]),
        CoverageNotice(code="FN-OPS-002", scope="k8s/deployment.yaml:33",
                       message="Secret catalog-api-env is referenced by envFrom but not stored in the repository. Findings that depend on pydantic-settings fields it could override stay CONDITIONAL; the computation is kept.",
                       affects_rules=["FN-RES-002"]),
        CoverageNotice(code="COV-RULE-SCOPE", domain=Domain.DEPLOYMENT_SAFETY, scope="k8s/pdb.yaml",
                       message="PDB uses maxUnavailable; FN-DEP-001 only treats minAvailable as a declared capacity floor, so rollout capacity was not compared."),
        CoverageNotice(code="FN-OPS-001", domain=Domain.DATA_COMPATIBILITY, scope="repository", message="Data and migration compatibility is not assessed in v0.1.", delegated_to=["squawk", "atlas"]),
    ]
    return build(findings, coverage, [link], F, comps, ScanInfo(mode="full", profile=None, profile_source="none"),
                 files=(19, 17), rules_evaluated=7, rules_na=3, note=PROJECTED)


# --------------------------------------------------------------------------- assembly and rendering


def build(findings, coverage, linkages, facts, comps, scan, files, rules_evaluated, rules_na, note) -> ScanResult:
    vorder = ["VIOLATION", "CONDITIONAL", "UNKNOWN", "SATISFIED"]
    sorder = ["info", "low", "medium", "high", "critical"]
    findings = sorted(findings, key=lambda f: (vorder.index(f.verdict.value), -sorder.index(f.severity.value if f.severity else "info"), f.rule_id, f.subject.key))
    by_verdict = {v: sum(f.verdict is v for f in findings) for v in Verdict}
    by_sev = {s: sum(f.severity is s for f in findings) for s in Severity}
    blocking = sum(f.blocking_eligible and sorder.index(f.severity.value) >= sorder.index(scan.fail_on.value) for f in findings)
    status = derive_status(findings, coverage, rules_evaluated)

    def has(f: Finding, *kinds: AssumptionKind) -> bool:
        return any(a.kind in kinds and a.material and a.state is AssumptionState.OPEN for a in f.assumptions)

    cond = [f for f in findings if f.verdict is Verdict.CONDITIONAL]
    limits = AssessmentLimits(
        linkages_declared=sum(link.status is LinkStatus.DECLARED for link in linkages),
        linkages_inferred=sum(link.status in (LinkStatus.INFERRED, LinkStatus.STALE) for link in linkages),
        linkages_unresolved=sum(link.status is LinkStatus.UNRESOLVED for link in linkages),
        findings_capped_by_linkage=sum(has(f, AssumptionKind.LINKAGE_INFERRED) for f in cond),
        findings_with_override_assumption=sum(has(f, AssumptionKind.EXTERNAL_OVERRIDE_ENV, AssumptionKind.EXTERNAL_OVERRIDE_FILE) for f in findings),
        findings_conditional_on_external_config=sum(has(f, AssumptionKind.EXTERNAL_OVERRIDE_ENV, AssumptionKind.EXTERNAL_OVERRIDE_FILE, AssumptionKind.CLUSTER_ADMISSION_DEFAULT) for f in cond),
        findings_conditional_on_untested_version=sum(has(f, AssumptionKind.UNTESTED_VERSION) for f in cond),
    )
    reason = {"FAIL_VERIFIED": f"{by_verdict[Verdict.VIOLATION]} VIOLATION finding(s) with established evidence.",
              "REVIEW_REQUIRED": "No established violations; conditional findings need review.",
              "INCOMPLETE": "Required evidence missing or nothing assessable.",
              "PASS_WITH_COVERAGE": "Only satisfied checks; read the coverage section."}[status.value]
    result = ScanResult(simulated=True, tool=TOOL, scan=scan, status=status, status_reason=f"{reason} [{note}]",
                        inventory=Inventory(files_considered=files[0], files_parsed=files[1], detected_stacks=list(comps.values())),
                        linkages=linkages, findings=findings, coverage=coverage, facts=facts,
                        summary=Summary(by_verdict=by_verdict, by_severity=by_sev, blocking=blocking,
                                        new=sum(f.baseline_status is BaselineStatus.NEW for f in findings),
                                        rules_evaluated=rules_evaluated, rules_not_applicable=rules_na, assessment_limits=limits))
    return result


def console(r: ScanResult) -> str:
    v = r.summary.by_verdict
    support = ", ".join(f"{c.component} {c.version or '?'} [{c.support.value}]" for c in r.inventory.detected_stacks)
    lines = [f"[{BANNER}]", "",
             f"FifthNine Scan {r.tool.version}   .   {r.scan.policy_mode} mode   profile: {r.scan.profile or '(none)'}"
             + (f" ({r.scan.profile_source.removeprefix('k8s-env:')})" if r.scan.profile_source and r.scan.profile_source.startswith("k8s-env:") else ""),
             f"Stack: {support}",
             f"Status: {r.status.value}   {v[Verdict.VIOLATION]} violation  {v[Verdict.CONDITIONAL]} conditional  {v[Verdict.UNKNOWN]} unknown  {v[Verdict.SATISFIED]} satisfied"
             f"   would block in gate mode (>= {r.scan.fail_on.value}): {r.summary.blocking}",
             "Note: " + r.status_reason.split("[", 1)[1].rstrip("]") if "[" in r.status_reason else "", ""]
    for f in r.findings:
        sev = f.severity.value if f.severity else ""
        lines.append(f"{f.verdict.value:<11} {sev:<7} {f.rule_id:<12} {f.title}")
        lines.append(f"{'':32}at " + ", ".join(f"{s.path}:{s.line_start}" for s in f.locations))
        if f.verdict in (Verdict.VIOLATION, Verdict.CONDITIONAL):
            lines.append(f"{'':32}{f.message}")
            if f.computation:
                lines.append(f"{'':32}computed: {f.computation}")
            if f.verdict_if_assumptions_hold:
                lines.append(f"{'':32}from repository evidence: {f.verdict_if_assumptions_hold.value} if the assumptions below hold")
            for c in f.conditional_reasons:
                lines.append(f"{'':32}open assumption: {c}")
            for a in f.assumptions:
                if a.state is AssumptionState.DECLARED_BY_TEAM:
                    lines.append(f"{'':32}relies on team declaration: {a.statement} ({a.declared_in.path}:{a.declared_in.line_start})")
            if f.remediation:
                lines.append(f"{'':32}fix: {f.remediation.summary}")
            lines.append(f"{'':32}gate: {'blocking-eligible' if f.blocking_eligible else 'not blocking (' + '; '.join(f.blocking_ineligible_reasons) + ')'}")
        else:
            for a in f.assumptions:
                lines.append(f"{'':32}assumes ({a.state.value}): {a.statement}")
        lines.append("")
    lines.append("Service linkage")
    for link in r.linkages:
        lines.append(f"  {link.link_id} {link.status.value}: {link.from_ref} -> {link.to_ref}")
        if link.declared_in:
            lines.append(f"     declared at {link.declared_in.path}:{link.declared_in.line_start} by {link.accepted_by} on {link.accepted_on}; evidence digest unchanged")
        for e in link.evidence:
            lines.append(f"     - {e}")
        if link.accept_command:
            lines.append(f"     confirm with: {link.accept_command}")
    lines += ["", "Coverage and limits"]
    for c in r.coverage:
        lines += [f"  {c.code:<20} {c.scope}", f"  {'':20} {c.message}"]
    al = r.summary.assessment_limits
    lines += ["", f"Assessment limits: linkages declared={al.linkages_declared} inferred/stale={al.linkages_inferred}; "
              f"conditional because of linkage={al.findings_capped_by_linkage}, external config={al.findings_conditional_on_external_config}, "
              f"untested version={al.findings_conditional_on_untested_version}", "", r.disclaimer, "", f"[{BANNER}]"]
    return "\n".join(lines) + "\n"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.json"):
        old.unlink()
    for old in OUT.glob("*.console.txt"):
        old.unlink()
    scenarios = {"orders-service-spring.default": orders("default"),
                 "orders-service-spring.links-accepted": orders("links-accepted"),
                 "orders-service-spring.declared": orders("declared"),
                 "orders-service-spring.today-untested": orders("today-untested"),
                 "catalog-api-fastapi.default": catalog()}
    for name, result in scenarios.items():
        (OUT / f"{name}.json").write_text(json.dumps(json.loads(result.model_dump_json(by_alias=True)), indent=2) + "\n")
        (OUT / f"{name}.console.txt").write_text(console(result))
        print(f"{name:<40} {result.status.value:<16}", {k.value: n for k, n in result.summary.by_verdict.items()}, "blocking", result.summary.blocking)


if __name__ == "__main__":
    main()
