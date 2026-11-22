# FifthNine scorecard: spring-petclinic/spring-petclinic-microservices (customers-service)

- Repository: https://github.com/spring-petclinic/spring-petclinic-microservices
- Commit: e8b747a
- Service scored: customers-service (the repository holds 8 services)
- Standard: 1.0 (current), with draft 2 and draft 1 kept below
- Runs: two per version, each in a fresh agent context, on 22 November 2026

## Version 1.0

### Run 1

```
FifthNine scorecard          standard 1.0
Repo: spring-petclinic/spring-petclinic-microservices (customers-service)   Commit: e8b747a
Platform: other (Docker Compose containers)   Languages: Java, shell, YAML

Score: 3 of 16 checks passed (19)
Verdict: Not ready (critical checks FN-1.1 and FN-3.2 failed; score below 70)

1 Embracing risk             0 of 2
2 Service level objectives   0 of 2
3 Eliminating toil           0 of 2
4 Monitoring                 0 of 2
5 Automation                 2 of 2
6 Release engineering        0 of 2
7 Simplicity                 0 of 2
8 Security                   1 of 2

Results
FN-1.1  Fail         docker-compose.yml:37
FN-1.2  No evidence  outside repo: config server repo github.com/spring-petclinic/spring-petclinic-microservices-config
FN-2.1  No evidence  none
FN-2.2  Fail         docker/prometheus/prometheus.yml:2
FN-3.1  Fail         docker-compose.yml:38
FN-3.2  Fail         docker-compose.yml:35
FN-4.1  No evidence  outside repo: config server repo github.com/spring-petclinic/spring-petclinic-microservices-config
FN-4.2  Fail         spring-petclinic-customers-service/src/main/resources/logback-spring.xml:3
FN-5.1  Pass         docker-compose.yml:35
FN-5.2  Pass         .github/workflows/maven-build.yml:29
FN-6.1  Fail         docker-compose.yml:36
FN-6.2  Fail         docker-compose.yml:38
FN-7.1  No evidence  outside repo: config server repo github.com/spring-petclinic/spring-petclinic-microservices-config
FN-7.2  Fail         spring-petclinic-customers-service/pom.xml:88
FN-8.1  Pass         spring-petclinic-customers-service/src/main/resources/application.yml:5
FN-8.2  Fail         docker/Dockerfile:8

Fix first
1. FN-1.1  A fixed container_name allows one instance only, and no replicas are set
           docker-compose.yml:37
2. FN-3.2  customers-service has no healthcheck and no restart policy
           docker-compose.yml:35
3. FN-2.2  Prometheus config has no rule_files and no burn-rate alert rules
           docker/prometheus/prometheus.yml:2
4. FN-3.1  The deploy block has no replicas or scaling settings
           docker-compose.yml:38
5. FN-4.2  Logback uses the default plain-text base.xml, not JSON; no alert rules in repo
           spring-petclinic-customers-service/src/main/resources/logback-spring.xml:3

No evidence: FN-1.2 (DB, Eureka and Zipkin client timeouts come from the external config server, application.yml:5/13), FN-2.1 (no SLO file or written target in repo), FN-4.1 (Micrometer, @Timed and a scrape job exist, but the management/actuator exposure that would publish the endpoint is loaded from the config server), FN-7.1 (server.port 8081 is not set in this repo; it comes from the config server)
```

### Run 2

```
FifthNine scorecard          standard 1.0
Repo: spring-petclinic/spring-petclinic-microservices (customers-service)   Commit: e8b747a
Platform: other (Docker Compose containers)   Languages: Java, Shell

Score: 2 of 16 checks passed (13)
Verdict: Not ready (critical checks FN-1.1 and FN-3.2 failed; score below 70)

1 Embracing risk             0 of 2
2 Service level objectives   0 of 2
3 Eliminating toil           0 of 2
4 Monitoring                 0 of 2
5 Automation                 2 of 2
6 Release engineering        0 of 2
7 Simplicity                 0 of 2
8 Security                   0 of 2

Results
FN-1.1  Fail         docker-compose.yml:37
FN-1.2  No evidence  outside repo: spring-petclinic-microservices-config (config server import, spring-petclinic-customers-service/src/main/resources/application.yml:13)
FN-2.1  No evidence  none
FN-2.2  Fail         docker/prometheus/prometheus.yml:2
FN-3.1  Fail         docker-compose.yml:38
FN-3.2  Fail         docker-compose.yml:35
FN-4.1  No evidence  outside repo: spring-petclinic-microservices-config (management endpoint exposure)
FN-4.2  Fail         spring-petclinic-customers-service/src/main/resources/logback-spring.xml:3
FN-5.1  Pass         docker-compose.yml:35
FN-5.2  Pass         .github/workflows/maven-build.yml:29
FN-6.1  Fail         docker-compose.yml:36
FN-6.2  Fail         docker-compose.yml:38
FN-7.1  No evidence  outside repo: spring-petclinic-microservices-config (server.port)
FN-7.2  Fail         spring-petclinic-customers-service/pom.xml:84
FN-8.1  No evidence  outside repo: spring-petclinic-microservices-config (datasource credentials)
FN-8.2  Fail         docker/Dockerfile:8

Fix first
1. FN-1.1  Single instance: fixed container_name, no replicas, so it cannot run more than one copy
           docker-compose.yml:37
2. FN-3.2  customers-service has no healthcheck and no restart policy
           docker-compose.yml:35
3. FN-2.2  Prometheus config has no rule_files and no burn-rate alerts
           docker/prometheus/prometheus.yml:2
4. FN-3.1  No replicas or scaling bounds in the service's deploy block
           docker-compose.yml:38
5. FN-4.2  Logging uses Spring Boot's plain-text base.xml, not JSON, and the repo has no alert rules
           spring-petclinic-customers-service/src/main/resources/logback-spring.xml:3

No evidence: FN-1.2 (DB, Eureka and Zipkin client timeouts come from the external config repo), FN-2.1 (no SLO file or target document in the repo), FN-4.1 (Micrometer Prometheus registry at pom.xml:80 and scrape job at prometheus.yml:19, but the endpoint exposure is set in the external config repo), FN-7.1 (the app's listen port is set in the external config repo, so the 8081 mapping cannot be checked), FN-8.1 (no secrets found in this repo's files for the service; the datasource credentials live in the external config repo)
```

### Hand check

Every cited line was opened and read. All of them exist and say what the run claims.

- **Pass mark missed.** The runs disagree on one check, FN-8.1, and it is critical. Run 1 is Pass, run 2 is No evidence (`outside repo`).
- **Cause.** Run 2 applied the new outside-repo rule to FN-8.1. FN-8.1 asks whether this repo contains secrets, so the location of the real credentials does not change it. The standard does not yet say that the outside-repo rule is limited to checks with a "Where it belongs" field, and FN-8.1 has none.
- **The draft 2 disagreements are settled.** FN-1.2 and FN-4.1 are No evidence in both runs, and both name the external config repository (`application.yml:5` and `:13` import it; `spring-petclinic-config-server/src/main/resources/application.yml:7` points to it). FN-7.1 is the same in both runs too.
- **FN-7.2 is Fail in both runs on different dependencies.** Run 1 cites `spring-boot-starter-restclient` (`pom.xml:88`), run 2 cites `chaos-monkey-spring-boot` (`pom.xml:84`). Each run gives a reason for not citing the other.
- **Both verdicts are Not ready**, on the same failed critical checks (FN-1.1 and FN-3.2).

## Draft 2

### Run 1 (draft 2)

```
FifthNine scorecard          standard 1.1
Repo: spring-petclinic/spring-petclinic-microservices (customers-service)   Commit: e8b747a
Platform: Other (containers on Docker Compose)   Languages: Java, SQL, Bash

Score: 3 of 16 checks passed (19)
Verdict: Not ready (critical checks FN-1.1 and FN-3.2 failed; score below 70)

1 Embracing risk             0 of 2
2 Service level objectives   0 of 2
3 Eliminating toil           0 of 2
4 Monitoring                 0 of 2
5 Automation                 2 of 2
6 Release engineering        0 of 2
7 Simplicity                 0 of 2
8 Security                   1 of 2

Results
FN-1.1  Fail         docker-compose.yml:37
FN-1.2  No evidence  spring-petclinic-customers-service/src/main/resources/application.yml:5
FN-2.1  No evidence  none
FN-2.2  Fail         docker/prometheus/prometheus.yml:2
FN-3.1  Fail         docker-compose.yml:35
FN-3.2  Fail         docker-compose.yml:35
FN-4.1  No evidence  spring-petclinic-customers-service/src/main/resources/application.yml:5
FN-4.2  Fail         spring-petclinic-customers-service/src/main/resources/logback-spring.xml:3
FN-5.1  Pass         docker-compose.yml:35
FN-5.2  Pass         .github/workflows/maven-build.yml:6
FN-6.1  Fail         docker-compose.yml:36
FN-6.2  Fail         docker-compose.yml:35
FN-7.1  No evidence  spring-petclinic-customers-service/src/main/resources/application.yml:5
FN-7.2  Fail         spring-petclinic-customers-service/pom.xml:84
FN-8.1  Pass         spring-petclinic-customers-service/src/main/resources/application.yml:5
FN-8.2  Fail         docker/Dockerfile:8

Fix first
1. FN-1.1  Single fixed-name container (container_name, no replicas), so one failure is a full outage
           docker-compose.yml:37
2. FN-3.2  customers-service has no healthcheck or restart policy, unlike config-server and discovery-server
           docker-compose.yml:35
3. FN-2.2  Prometheus config has no rule_files and no burn-rate alerts
           docker/prometheus/prometheus.yml:2
4. FN-3.1  No scaling config (replicas, minimum or maximum) for customers-service
           docker-compose.yml:35
5. FN-4.2  Logging uses Spring Boot's plain-text base.xml, not JSON, and no alert rules are in the repo
           spring-petclinic-customers-service/src/main/resources/logback-spring.xml:3

No evidence: FN-1.2 (datasource, Eureka, Zipkin and config-client settings live in the external config repo, so no timeouts or retry caps can be seen here); FN-2.1 (no SLO file or written availability and latency target); FN-4.1 (Micrometer Prometheus, @Timed and a scrape job at prometheus.yml:19 are present, but no config in this repo exposes the actuator/prometheus endpoint); FN-7.1 (the port the service listens on in the docker profile is set in the external config repo, so the 8081 port mapping cannot be checked)
```

### Run 2 (draft 2)

```
FifthNine scorecard          standard 1.1
Repo: spring-petclinic/spring-petclinic-microservices (customers-service)   Commit: e8b747a
Platform: other (Docker Compose containers)   Languages: Java, Shell, SQL

Score: 3 of 16 checks passed (19)
Verdict: Not ready (critical checks FN-1.1, FN-1.2, FN-3.2 failed; score below 70)

1 Embracing risk             0 of 2
2 Service level objectives   0 of 2
3 Eliminating toil           0 of 2
4 Monitoring                 0 of 2
5 Automation                 2 of 2
6 Release engineering        0 of 2
7 Simplicity                 0 of 2
8 Security                   1 of 2

Results
FN-1.1  Fail         docker-compose.yml:37
FN-1.2  Fail         spring-petclinic-customers-service/src/main/resources/application.yml:13
FN-2.1  No evidence  none
FN-2.2  Fail         docker/prometheus/prometheus.yml:4
FN-3.1  Fail         docker-compose.yml:35
FN-3.2  Fail         docker-compose.yml:35
FN-4.1  Fail         spring-petclinic-customers-service/src/main/resources/application.yml:1
FN-4.2  Fail         spring-petclinic-customers-service/src/main/resources/logback-spring.xml:3
FN-5.1  Pass         docker-compose.yml:35
FN-5.2  Pass         .github/workflows/maven-build.yml:29
FN-6.1  Fail         docker-compose.yml:36
FN-6.2  Fail         docker-compose.yml:38
FN-7.1  No evidence  none
FN-7.2  Fail         spring-petclinic-customers-service/pom.xml:88
FN-8.1  Pass         spring-petclinic-customers-service/src/main/resources/application.yml:5
FN-8.2  Fail         docker/Dockerfile:8

Fix first
1. FN-1.1  Single instance: fixed container_name and host port 8081 allow only one replica
           docker-compose.yml:37
2. FN-1.2  No explicit timeout or retry limit for config server, Eureka, DB or Zipkin clients
           spring-petclinic-customers-service/src/main/resources/application.yml:13
3. FN-3.2  customers-service has no healthcheck, unlike config-server (line 9)
           docker-compose.yml:35
4. FN-2.2  Prometheus config has no rule_files and no burn-rate alerts
           docker/prometheus/prometheus.yml:4
5. FN-3.1  No scaling with a minimum and maximum for customers-service
           docker-compose.yml:35

No evidence: FN-2.1 (no SLO file or written availability/latency target in the repo); FN-7.1 (the port the app listens on in the docker profile is set in the external config repo, so the mapping of 8081 to a listening port cannot be checked)
```

### Hand check (draft 2)

Every new citation was opened and read. All of them exist.

- **Pass mark missed.** The runs disagree on two checks, and one of them is critical.
- **FN-1.2 is critical:** run 1 is No evidence, run 2 is Fail.
- **FN-4.1:** run 1 is No evidence, run 2 is Fail.
- **Cause of both:** the missing-configuration rule. The service's `application.yml` exists, but it only imports the real config from a separate repository (`application.yml:5`, `:13`). Run 2 treated the local file as "the file where the setting belongs". Run 1 treated the external config repo as that place, and it is not in this repository.
- **Both verdicts are Not ready either way.**
- **FN-5.1 changed from Fail (draft 1) to Pass in both runs.** The new wording excludes optional steps, and the README presents MySQL as an option. Both runs called this a generous reading, because Compose is the only infrastructure code.
- **The draft 1 arithmetic error is gone.** Both scores add up.

## Draft 1

### Run 1 (draft 1)

```
FifthNine scorecard          standard 1.0
Repo: spring-petclinic/spring-petclinic-microservices (customers-service)   Commit: e8b747a
Platform: other (Docker Compose, single host)   Languages: Java, Bash

Score: 13 / 100              Verdict: Not ready (critical checks FN-1.1 and FN-3.2 failed; score below 70)

1 Embracing risk             0 of 2
2 Service level objectives   0 of 2
3 Eliminating toil           0 of 2
4 Monitoring                 0 of 2
5 Automation                 1 of 2
6 Release engineering        0 of 2
7 Simplicity                 0 of 2
8 Security                   1 of 2

Fix first
1. FN-1.1  customers-service runs as one container with a fixed container_name, so no replicas and no zone spread
           docker-compose.yml:37
2. FN-3.2  customers-service has no healthcheck and no restart policy, though actuator is on the classpath
           docker-compose.yml:35
3. FN-2.2  Prometheus config loads no rule_files, so no burn-rate or other alert rules are evaluated
           docker/prometheus/prometheus.yml:4
4. FN-3.1  deploy block sets only a memory limit: no replicas, no minimum or maximum, no scaling
           docker-compose.yml:38
5. FN-4.2  logging uses Spring Boot base.xml plain-text pattern, not JSON or key-value
           spring-petclinic-customers-service/src/main/resources/logback-spring.xml:3

No evidence: FN-1.2 (DB, config-server, Eureka and Zipkin client timeouts and retries would be set in the external config repo, not in this repo); FN-2.1 (no SLO file or written availability or latency target anywhere in the repo); FN-4.1 (Micrometer, @Timed and the scrape job are present, but endpoint exposure is set outside the repo and no saturation metric is shown); FN-7.1 (the service's listen port comes from the external config repo, and README.md:23 says "random port", so the 8081 mapping can't be confirmed)
```

### Run 2 (draft 1)

```
FifthNine scorecard          standard 1.0
Repo: spring-petclinic/spring-petclinic-microservices (customers-service)   Commit: e8b747a
Platform: other (Docker containers on Docker Compose)   Languages: Java, Shell, YAML

Score: 25 / 100              Verdict: Not ready (critical checks FN-1.1 and FN-3.2 failed; score below 70)

1 Embracing risk             0 of 2
2 Service level objectives   0 of 2
3 Eliminating toil           0 of 2
4 Monitoring                 1 of 2
5 Automation                 1 of 2
6 Release engineering        0 of 2
7 Simplicity                 0 of 2
8 Security                   1 of 2

Fix first
1. FN-1.1  Single instance: fixed container_name stops replicas, and there is no zone spread or disruption budget
           docker-compose.yml:37
2. FN-3.2  customers-service has no healthcheck, though config-server and discovery-server have one
           docker-compose.yml:35
3. FN-2.2  Prometheus config has no rule_files, so no error-budget burn alerts are evaluated
           docker/prometheus/prometheus.yml:4
4. FN-3.1  No autoscaling: one fixed container with no minimum or maximum
           docker-compose.yml:37
5. FN-4.2  Logging uses Spring Boot's base.xml plain-text pattern (not JSON), and no alert rule files are in the repo
           spring-petclinic-customers-service/src/main/resources/logback-spring.xml:3

No evidence: FN-1.2 (no timeouts or retry limits for the DB, config-server, Eureka or Zipkin clients in the repo; datasource and client settings come from the external config repo), FN-2.1 (no SLO file or written availability and latency target anywhere in the repo), FN-7.1 (container port 8081 cannot be matched to a listener in the repo; server.port for the docker profile is set in the external config repo)
```

### Hand check (draft 1)

Every cited file and line was opened and read.

- **Run 2's score is wrong.** Run 2 passed 3 of 16 checks, which is 19, but it printed 25. The scorecard above is kept as printed.
- **FN-4.1 disagrees.** Run 1 is No evidence, run 2 is Pass. Run 2's Pass is wrong. `micrometer-registry-prometheus` (`spring-petclinic-customers-service/pom.xml:80`), `@Timed` and the scrape job (`docker/prometheus/prometheus.yml:19`) are present. But nothing in this repository exposes `/actuator/prometheus` (that setting lives in the external config repo), and no saturation metric is shown. No evidence is the correct result.
- **FN-5.1 cites the wrong line in both runs.** `README.md:169` describes an optional step ("You may also manually create..."). The Fail itself holds: `README.md:172` and `README.md:176-177` are required manual edits for a MySQL deployment, and the repository has no infrastructure code.
- **Every other finding was verified.**
