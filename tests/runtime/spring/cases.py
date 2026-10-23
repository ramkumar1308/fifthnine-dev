"""M2-D3 runtime cases: small Spring Boot applications whose configuration the scanner reads and the JVM then runs.

Each case builds the same fixture application (fixture_app) with its own `src/main/resources`, a Dockerfile and a Deployment. The
Deployment's `env`, `args` and the Dockerfile's ENV and ENTRYPOINT are exactly what the harness gives the JVM, so the scan and the
run see the same inputs. `run_env` adds variables only the run sees (a value behind a `valueFrom` reference the scan cannot read).
"""

# ruff: noqa: E501  (case data: one case per line reads better than wrapped dictionaries)

from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_ENTRYPOINT = ["java", "-jar", "/app/app.jar"]


@dataclass
class Case:
    name: str
    boot: str = "3.4.13"
    resources: dict[str, str] = field(default_factory=dict)
    env: dict[str, str | None] = field(default_factory=dict)  # None: a valueFrom reference, value taken from run_env
    run_env: dict[str, str] = field(default_factory=dict)
    args: list[str] | None = None
    docker_env: list[str] = field(default_factory=list)  # "NAME=value"
    entrypoint: list[str] | None = None
    kubernetes: bool = True  # give the JVM the variables a pod has (KUBERNETES_SERVICE_HOST, KUBERNETES_SERVICE_PORT)
    profile: str | None = None  # scan option, not used by the run
    covers: str = ""
    expect_runtime: dict[str, str | None] = field(default_factory=dict)  # property -> value the JVM must report (for facts the scan leaves unresolved)


def _yml(text: str) -> str:
    return text.lstrip("\n")


APP = "server:\n  port: 8081\n"
INC = "management.endpoints.web.exposure.include"
JL = "org.springframework.boot.loader.launch.JarLauncher"

CASES: list[Case] = [
    # -- versions: the defaults table against the bound defaults of the real application
    *[Case(f"defaults-{v}", boot=v, resources={"application.yml": "spring:\n  application:\n    name: demo\n"}, covers="default table")
      for v in ("3.2.12", "3.3.13", "3.4.13", "3.5.16")],
    Case("values-and-types", resources={"application.yml": _yml("""
server:
  port: 8081
  shutdown: graceful
spring:
  lifecycle:
    timeout-per-shutdown-phase: 45s
management:
  endpoints:
    web:
      exposure:
        include: health,info
""")}, covers="typed values, list from comma string"),
    Case("yaml-list-and-iso-duration", resources={"application.yml": _yml("""
server:
  port: 020000
spring:
  lifecycle:
    timeout-per-shutdown-phase: PT1M
management:
  endpoints:
    web:
      exposure:
        include:
          - health
          - info
""")}, covers="YAML 1.1 octal (020000 is 8192), ISO duration, YAML sequence"),
    # -- profiles
    Case("profile-active-in-file", resources={
        "application.yml": "spring:\n  profiles:\n    active: prod\nserver:\n  port: 8081\n",
        "application-prod.yml": "server:\n  port: 8082\nspring:\n  lifecycle:\n    timeout-per-shutdown-phase: 10s\n"}, covers="F: active in file"),
    Case("profile-on-profile-documents", resources={"application.yml": _yml("""
server:
  port: 8081
---
spring:
  config:
    activate:
      on-profile: prod
server:
  port: 8083
---
spring:
  config:
    activate:
      on-profile: "!prod"
server:
  port: 8084
""")}, env={"SPRING_PROFILES_ACTIVE": "prod"}, covers="F: on-profile documents, active from env"),
    Case("profile-groups-and-include", resources={
        "application.yml": "spring:\n  profiles:\n    include: base\n    group:\n      prod: db,metrics\nserver:\n  port: 8081\n",
        "application-base.yml": "server:\n  port: 8085\n", "application-db.yml": "server:\n  port: 8086\n",
        "application-metrics.yml": "server:\n  port: 8087\n"}, args=["--spring.profiles.active=prod"], covers="F: group order and include"),
    Case("profile-default-file", resources={
        "application.yml": "server:\n  port: 8081\n", "application-default.yml": "server:\n  port: 8088\n"}, covers="application-default"),
    Case("profile-spring-profiles-default", resources={
        "application.yml": "spring:\n  profiles:\n    default: dev\nserver:\n  port: 8081\n",
        "application-dev.yml": "server:\n  port: 8089\n", "application-default.yml": "server:\n  port: 8088\n"}, covers="spring.profiles.default"),
    Case("profile-default-from-env", resources={
        "application.yml": "server:\n  port: 8081\n", "application-dev.yml": "server:\n  port: 8089\n"},
        env={"SPRING_PROFILES_DEFAULT": "dev"}, covers="spring.profiles.default from the environment"),
    Case("profile-default-in-profile-file-ignored", resources={
        "application.yml": "server:\n  port: 8081\n",
        "application-dev.yml": "spring:\n  profiles:\n    default: dev\nserver:\n  port: 8089\n"}, covers="spring.profiles.default in a profile file"),
    Case("profile-active-overrides-default", resources={
        "application.yml": "spring:\n  profiles:\n    default: dev\n    active: prod\nserver:\n  port: 8081\n",
        "application-dev.yml": "server:\n  port: 8089\n", "application-prod.yml": "server:\n  port: 8090\n"}, covers="active ends the default profile"),
    Case("file-order-properties-yml-config", resources={
        "application.yaml": "server:\n  port: 8001\n", "application.yml": "server:\n  port: 8002\n",
        "application.properties": "server.port=8003\n", "config/application.yml": "server:\n  port: 8004\n"}, covers="file and location order"),
    Case("file-order-properties-over-yml", resources={
        "application.yml": "server:\n  port: 8002\n", "application.properties": "server.port=8003\n"}, covers="properties over yml"),
    # -- precedence: one case per layer, the strongest layer removed each time
    Case("precedence-all-layers", resources={"application.yml": "server:\n  port: 8080\n"},
         docker_env=["SERVER_PORT=8181"], env={"SERVER_PORT": "8282", "JAVA_TOOL_OPTIONS": "-Dserver.port=8383",
                                              "SPRING_APPLICATION_JSON": '{"server":{"port":8484}}'}, args=["--server.port=8585"],
         covers="command line wins"),
    Case("precedence-no-args", resources={"application.yml": "server:\n  port: 8080\n"},
         docker_env=["SERVER_PORT=8181"], env={"SERVER_PORT": "8282", "JAVA_TOOL_OPTIONS": "-Dserver.port=8383",
                                              "SPRING_APPLICATION_JSON": '{"server":{"port":8484}}'}, covers="JSON wins"),
    Case("precedence-no-json", resources={"application.yml": "server:\n  port: 8080\n"},
         docker_env=["SERVER_PORT=8181"], env={"SERVER_PORT": "8282", "JAVA_TOOL_OPTIONS": "-Dserver.port=8383"}, covers="system property wins"),
    Case("precedence-env-over-docker", resources={"application.yml": "server:\n  port: 8080\n"},
         docker_env=["SERVER_PORT=8181"], env={"SERVER_PORT": "8282"}, covers="container env over Dockerfile ENV"),
    Case("precedence-docker-env-over-file", resources={"application.yml": "server:\n  port: 8080\n"},
         docker_env=["SERVER_PORT=8181"], covers="Dockerfile ENV over file"),
    Case("env-legacy-underscore-names", resources={"application.yml": "server:\n  servlet:\n    context-path: /a\n"},
         env={"SERVER_SERVLET_CONTEXT_PATH": "/b", "MANAGEMENT_ENDPOINTS_WEB_BASE_PATH": "/m"}, covers="uniform and legacy env names"),
    Case("entrypoint-dash-d-and-arg", resources={"application.yml": "server:\n  port: 8080\n"},
         entrypoint=["java", "-Dserver.port=8686", "-jar", "/app/app.jar", "--management.endpoints.web.base-path=/ops"],
         covers="ENTRYPOINT options"),
    Case("args-dash-d-after-jar-ignored", resources={"application.yml": "server:\n  port: 8080\n"}, args=["-Dserver.port=9999"],
         covers="-D after -jar is a program argument"),
    Case("java-tool-options-two-props", resources={"application.yml": "server:\n  port: 8080\n"},
         env={"JAVA_TOOL_OPTIONS": "-Dserver.port=8383 -Dserver.servlet.context-path=/jt"}, covers="several -D words"),
    # -- placeholders
    Case("placeholder-default-used", resources={"application.yml": _yml("""
server:
  port: ${APP_PORT:8091}
spring:
  lifecycle:
    timeout-per-shutdown-phase: ${APP_WAIT:30s}
""")}, covers="placeholder default"),
    Case("placeholder-env-value", resources={"application.yml": _yml("""
server:
  port: ${APP_PORT:8091}
spring:
  lifecycle:
    timeout-per-shutdown-phase: ${APP_WAIT:30s}
""")}, env={"APP_PORT": "8092", "APP_WAIT": "45s"}, covers="placeholder from env"),
    Case("placeholder-property-reference", resources={"application.yml": _yml("""
base:
  port: 8093
server:
  port: ${base.port}
client:
  url: http://${host.name:localhost}:${server.port}/api
""")}, covers="placeholder to another property, nested defaults"),
    Case("placeholder-nested-default", resources={"application.yml": "server:\n  port: ${A_PORT:${B_PORT:8094}}\n"}, env={"B_PORT": "8095"},
         covers="nested default"),
    Case("placeholder-unreadable-value", resources={"application.yml": "server:\n  servlet:\n    context-path: ${CTX}\n"},
         env={"CTX": None}, run_env={"CTX": "/c"}, covers="valueFrom: scan must not claim a value"),
    # -- actuator
    Case("actuator-default-kubernetes", resources={"application.yml": "spring:\n  application:\n    name: demo\n"}, covers="R: default"),
    Case("actuator-cloud-platform-none", resources={"application.yml": "spring:\n  main:\n    cloud-platform: none\n"}, covers="probes off"),
    Case("actuator-cloud-platform-kubernetes", resources={"application.yml": "spring:\n  main:\n    cloud-platform: kubernetes\n"},
         kubernetes=False, covers="probes on by property"),
    Case("actuator-management-port", resources={"application.yml": _yml("""
server:
  port: 8096
  servlet:
    context-path: /app
management:
  server:
    port: 8097
    base-path: /mgmt
  endpoints:
    web:
      base-path: /ops
""")}, covers="R: different port, base path, context path"),
    Case("actuator-management-port-8080-rule", resources={"application.yml": "management:\n  server:\n    port: 8080\n"},
         covers="R: management port 8080 with server.port unset is the same port"),
    Case("actuator-management-port-equal", resources={"application.yml": "server:\n  port: 8098\nmanagement:\n  server:\n    port: 8098\n"},
         covers="R: equal ports"),
    Case("actuator-management-disabled", resources={"application.yml": "management:\n  server:\n    port: -1\n"}, covers="R: -1 disables"),
    Case("actuator-path-mapping", resources={"application.yml": _yml("""
management:
  endpoints:
    web:
      base-path: /manage
      path-mapping:
        health: status
""")}, covers="R: path mapping"),
    Case("actuator-additional-paths", resources={"application.yml": _yml("""
server:
  servlet:
    context-path: /app
management:
  endpoint:
    health:
      probes:
        add-additional-paths: true
""")}, covers="R: /livez /readyz"),
    Case("actuator-exclude-health", resources={"application.yml": "management:\n  endpoints:\n    web:\n      exposure:\n        exclude: health\n"},
         covers="R: excluded"),
    Case("actuator-include-info-only", resources={"application.yml": "management:\n  endpoints:\n    web:\n      exposure:\n        include: info\n"},
         covers="R: health not exposed"),
    Case("actuator-include-wildcard", resources={"application.yml": 'management:\n  endpoints:\n    web:\n      exposure:\n        include: "*"\n'},
         covers="R: wildcard"),
    Case("actuator-probes-disabled", resources={"application.yml": "management:\n  endpoint:\n    health:\n      probes:\n        enabled: false\n"},
         covers="R: probes off"),
    Case("actuator-boot33-enabled-false", boot="3.3.13",
         resources={"application.yml": "management:\n  endpoint:\n    health:\n      enabled: false\n"}, covers="R: enabled on 3.3"),
    Case("actuator-boot33-access-ignored", boot="3.3.13",
         resources={"application.yml": "management:\n  endpoint:\n    health:\n      access: none\n"}, covers="R: access ignored on 3.3"),
    Case("actuator-boot35-access-none", boot="3.5.16",
         resources={"application.yml": "management:\n  endpoint:\n    health:\n      access: none\n"}, covers="R: access on 3.5"),
    Case("actuator-boot34-default-access-none", resources={"application.yml": "management:\n  endpoints:\n    access:\n      default: none\n"},
         covers="R: endpoints.access.default"),
    Case("actuator-boot33-enabled-by-default-false", boot="3.3.13",
         resources={"application.yml": "management:\n  endpoints:\n    enabled-by-default: false\n"}, covers="R: enabled-by-default"),
    # -- conversions (review round 1)
    Case("convert-boolean-words", boot="3.3.13", resources={"application.properties": _yml("""
management.endpoint.health.enabled=yes
management.endpoints.enabled-by-default=false
management.endpoint.health.probes.enabled=on
""")}, covers="yes/on as booleans"),
    Case("convert-empty-values", resources={"application.properties": _yml("""
management.endpoints.web.exposure.include=
server.port=
management.server.port=8099
""")}, covers="empty value is unset"),
    # -- precedence paths added after review round 2
    Case("profile-active-from-system-property", resources={
        "application.yml": "server:\n  port: 8081\n", "application-prod.yml": "server:\n  port: 8082\n"},
        env={"JAVA_TOOL_OPTIONS": "-Dspring.profiles.active=prod"}, covers="active profile from a system property"),
    Case("profile-two-active-later-wins", resources={
        "application.yml": "server:\n  port: 8081\n", "application-a.yml": "server:\n  port: 8082\n", "application-b.yml": "server:\n  port: 8083\n"},
        args=["--spring.profiles.active=a,b"], covers="later active profile wins"),
    Case("profile-file-over-on-profile-document", resources={"application.yml": _yml("""
server:
  port: 8081
---
spring:
  config:
    activate:
      on-profile: prod
server:
  port: 8082
"""), "application-prod.yml": "server:\n  port: 8083\n"}, env={"SPRING_PROFILES_ACTIVE": "prod"}, covers="profile file over on-profile document"),
    Case("profile-active-from-json", resources={
        "application.yml": "server:\n  port: 8081\n", "application-prod.yml": "server:\n  port: 8082\n"},
        env={"SPRING_APPLICATION_JSON": '{"spring":{"profiles":{"active":"prod"}}}'}, covers="active profile from SPRING_APPLICATION_JSON"),
    Case("properties-multi-document", resources={"application.properties": _yml("""
server.port=8081
#---
spring.config.activate.on-profile=prod
server.port=8082
""")}, env={"SPRING_PROFILES_ACTIVE": "prod"}, covers="#--- documents in a properties file"),
    Case("json-list-and-nested", resources={"application.yml": "server:\n  port: 8080\n"},
         env={"SPRING_APPLICATION_JSON": '{"management":{"endpoints":{"web":{"exposure":{"include":["health","info"]}}}},"server":{"port":8101}}'},
         covers="JSON list and nested keys"),
    Case("env-index-list", resources={"application.yml": "server:\n  port: 8080\n"},
         env={"MANAGEMENT_ENDPOINTS_WEB_EXPOSURE_INCLUDE": "health,info"}, covers="comma list from the environment"),
    Case("config-location-replaces-defaults", resources={"application.yml": "server:\n  port: 8081\n"},
         env={"SPRING_CONFIG_LOCATION": "optional:file:/nonexistent/"}, expect_runtime={"server.port": None},
         covers="spring.config.location replaces the default locations: packaged files unused"),
    Case("config-name-other", resources={"application.yml": "server:\n  port: 8081\n"},
         args=["--spring.config.name=other"], expect_runtime={"server.port": None}, covers="spring.config.name hides application.yml"),
    Case("config-location-keeps-defaults", resources={"application.yml": "server:\n  port: 8081\n"},
         env={"SPRING_CONFIG_LOCATION": "optional:classpath:/,optional:classpath:/config/,optional:file:/nonexistent/"}, covers="defaults still searched"),
    Case("config-additional-location", resources={"application.yml": "server:\n  port: 8081\n"},
         env={"SPRING_CONFIG_ADDITIONAL_LOCATION": "optional:file:/nonexistent/"}, covers="additional location keeps the packaged files"),
    Case("placeholder-relaxed-env", resources={"application.yml": "server:\n  port: ${app.http-port:8080}\n"}, env={"APP_HTTPPORT": "8102"},
         covers="placeholder resolved from a relaxed env name"),
    Case("args-over-json-same-key", resources={"application.yml": "server:\n  port: 8080\n"},
         env={"SPRING_APPLICATION_JSON": '{"server":{"port":8103}}'}, args=["--server.port=8104"], covers="command line over JSON"),
    # -- cases from independent review round 2 (JVM decides)

    Case("args-duplicate-active", resources={"application.yml": APP, "application-x.yml": "spring:\n  lifecycle:\n    timeout-per-shutdown-phase: 10s\n",
         "application-y.yml": "server:\n  port: 8083\n"}, args=["--spring.profiles.active=x", "--spring.profiles.active=y"], covers="repeated option joins with commas: both profiles active"),
    Case("args-duplicate-string", resources={"application.yml": "spring:\n  application:\n    name: demo\n"},
         args=["--spring.application.name=a", "--spring.application.name=b"], covers="repeated option value is 'a,b'"),
    Case("args-end-of-options", resources={"application.yml": APP}, args=["--", "--server.port=8101"], covers="UNSURE: args after bare -- are not options"),
    Case("main-class-dash-d-is-program-arg", resources={"application.yml": APP},
         entrypoint=["java", "-cp", "/app/app.jar", JL, "-Dserver.port=8101"], covers="-D after the main class is not a system property"),
    Case("profile-group-from-args", resources={"application.yml": APP, "application-gx.yml": "server:\n  port: 8082\n", "application-gy.yml": "server:\n  port: 8083\n"},
         args=["--spring.profiles.active=grp", "--spring.profiles.group.grp=gx,gy"], covers="group defined on the command line"),
    Case("profile-group-from-env", resources={"application.yml": APP, "application-gx.yml": "server:\n  port: 8082\n", "application-gy.yml": "server:\n  port: 8083\n"},
         env={"SPRING_PROFILES_ACTIVE": "grp", "SPRING_PROFILES_GROUP_GRP": "gx,gy"}, covers="UNSURE: group defined in the environment"),
    Case("profile-group-from-json", resources={"application.yml": APP, "application-gx.yml": "server:\n  port: 8082\n"},
         env={"SPRING_APPLICATION_JSON": '{"spring.profiles.active":"grp","spring.profiles.group.grp":"gx"}'}, covers="group defined in JSON"),
    Case("env-indexed-list", resources={"application.yml": f"{INC}: health,info\n".replace(INC, "management:\n  endpoints:\n    web:\n      exposure:\n        include") },
         env={"MANAGEMENT_ENDPOINTS_WEB_EXPOSURE_INCLUDE_0_": "metrics"}, covers="indexed env name replaces the whole list"),
    Case("env-indexed-active-profile", resources={"application.yml": APP, "application-x.yml": "server:\n  port: 8082\n"},
         env={"SPRING_PROFILES_ACTIVE_0_": "x"}, covers="indexed env name for spring.profiles.active"),
    Case("sysprop-application-json", resources={"application.yml": APP},
         entrypoint=["java", '-Dspring.application.json={"server":{"port":8101}}', "-jar", "/app/app.jar"], covers="spring.application.json as system property"),
    Case("args-application-json", resources={"application.yml": APP},
         args=['--spring.application.json={"server.port":8101}'], covers="spring.application.json as argument"),
    Case("sysprop-index-override", resources={"application.yml": "management:\n  endpoints:\n    web:\n      exposure:\n        include: health\n"},
         env={"JAVA_TOOL_OPTIONS": f"-D{INC}[0]=info -D{INC}[1]=metrics"},
         entrypoint=["java", f"-D{INC}[0]=health", "-jar", "/app/app.jar"], covers="same index in two -D words: last wins, list is [health, metrics]"),
    Case("args-config-import", resources={"application.yml": APP, "extra.yml": "server:\n  port: 8110\n"},
         args=["--spring.config.import=optional:classpath:extra.yml"], covers="import supplied on the command line"),
    Case("sysprop-additional-location", resources={"application.yml": APP, "extra/application.yml": "server:\n  port: 8110\n"},
         entrypoint=["java", "-Dspring.config.additional-location=optional:classpath:/extra/", "-jar", "/app/app.jar"], covers="additional location as system property"),
    Case("config-location-reversed", resources={"application.yml": APP, "config/application.yml": "server:\n  port: 8082\n"},
         env={"SPRING_CONFIG_LOCATION": "optional:classpath:/config/,optional:classpath:/"}, covers="later location wins: classpath:/ over config/"),
    Case("config-location-explicit-profile-order", resources={"application-x.yml": "server:\n  port: 8082\n", "config/application.yml": "server:\n  port: 8083\n"},
         env={"SPRING_CONFIG_LOCATION": "optional:classpath:/,optional:classpath:/config/", "SPRING_PROFILES_ACTIVE": "x"}, covers="UNSURE: separate locations vs default grouped classpath locations"),
    Case("on-cloud-platform-document", resources={"application.yml": APP + "---\nspring:\n  config:\n    activate:\n      on-cloud-platform: kubernetes\nserver:\n  port: 8090\n"},
         covers="on-cloud-platform document active in a pod"),
    Case("json-empty-array", resources={"application.yml": "management:\n  endpoints:\n    web:\n      exposure:\n        include: health,info\n"},
         env={"SPRING_APPLICATION_JSON": '{"management":{"endpoints":{"web":{"exposure":{"include":[]}}}}}'}, covers="UNSURE: empty JSON array"),
    Case("jdk-java-options-over-tool-options", resources={"application.yml": APP},
         env={"JAVA_TOOL_OPTIONS": "-Dserver.port=8101", "JDK_JAVA_OPTIONS": "-Dserver.port=8102"}, covers="UNSURE: JDK_JAVA_OPTIONS wins"),
    Case("placeholder-random-default", resources={"application.yml": "spring:\n  application:\n    name: n-${random.value:x}\n"}, covers="random.* is supplied by the JVM, the inline text is not its value"),
    Case("placeholder-hostname-default", resources={"application.yml": "spring:\n  application:\n    name: ${HOSTNAME:svc}\n"}, run_env={"HOSTNAME": "pod-1"}, covers="kubelet sets HOSTNAME"),
    Case("placeholder-name-case", resources={"application.yml": "my.name: real\nspring:\n  application:\n    name: ${My.Name:dflt}\n"}, covers="UNSURE: placeholder names are case-sensitive"),
    Case("group-in-profile-file", resources={"application.yml": APP, "application-x.yml": "spring:\n  profiles:\n    group:\n      x: y\n", "application-y.yml": "server:\n  port: 8082\n"},
         env={"SPRING_PROFILES_ACTIVE": "x"}, covers="UNSURE: group set in a profile-specific file"),
]
