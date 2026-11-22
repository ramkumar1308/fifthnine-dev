"""M2-2 closing fixes: the silent-wrong-value items left open after review round 3 (M2-2.md, "Open, not fixed").

Each test builds a small repository with the M2-2 fixture helpers and checks the property the item was about. Where the
scanner cannot know a value, the expected result is `unresolved` or override exposure, never a guessed value.
"""

from __future__ import annotations

from pathlib import Path

from fifthnine.schema.v1 import FactStatus, OverrideExposure

from .test_m2_2_spring import Repo, _notices, _profiles, _props, _scan

PORT = "server.port"


def _sub(tmp: Path, name: str) -> Path:
    d = tmp / name
    d.mkdir()
    return d


def _port(tmp: Path, repo: Repo, *, oracle: bool = True):  # noqa: ANN202 - test helper
    r = _scan(tmp, repo, oracle=oracle)
    return r, _props(r)[PORT]


# ---------------------------------------------------------------- shell-wrapped commands


def test_shell_form_entrypoint_does_not_pass_kubernetes_args_to_java(tmp_path: Path) -> None:
    # ENTRYPOINT java -jar ... (shell form) runs as sh -c "<string>"; the Kubernetes args become $0 and $1 and never reach java
    r, p = _port(tmp_path, Repo(entrypoint="ENTRYPOINT java -jar /app/app.jar\n", command=["--server.port=9000"]))
    assert p.status is FactStatus.DEFAULT and p.value == 8080, (p, _notices(r))


def test_shell_script_passes_positional_arguments_through_quoted_at(tmp_path: Path) -> None:
    entry = 'ENTRYPOINT ["sh", "-c", "exec java -jar /app/app.jar \\"$@\\"", "app"]\n'
    r, p = _port(tmp_path, Repo(entrypoint=entry, command=["--server.port=9000"]))
    assert p.value == 9000 and p.status is FactStatus.INFERRED, (p, _notices(r))
    assert p.source is not None and p.source.path == "deploy.yaml"


def test_java_opts_expanded_by_a_shell_is_live(tmp_path: Path) -> None:
    entry = 'ENTRYPOINT ["sh", "-c", "java $JAVA_OPTS -jar /app/app.jar"]\n'
    r, p = _port(tmp_path, Repo(entrypoint=entry, env={"JAVA_OPTS": "-Xmx512m -Dserver.port=9100"}))
    assert p.value == 9100, (p, _notices(r))
    assert "JAVA_OPTS is not read" not in _notices(r)


def test_java_opts_without_a_shell_is_still_ignored(tmp_path: Path) -> None:
    r, p = _port(tmp_path, Repo(env={"JAVA_OPTS": "-Dserver.port=9100"}))
    assert p.value == 8080
    assert "JAVA_OPTS is not read" in _notices(r)


def test_java_opts_from_an_external_secret_is_exposure(tmp_path: Path) -> None:
    entry = 'ENTRYPOINT ["sh", "-c", "java $JAVA_OPTS -jar /app/app.jar"]\n'
    r, p = _port(tmp_path, Repo(entrypoint=entry, secret=True))
    assert p.override_exposure is OverrideExposure.ENV_POSSIBLE
    assert any("JAVA_OPTS" in s.variables for s in p.exposure_sources), p.exposure_sources


def test_java_opts_from_an_unresolved_value_from_is_exposure(tmp_path: Path) -> None:
    entry = 'ENTRYPOINT ["sh", "-c", "java $JAVA_OPTS -jar /app/app.jar"]\n'
    r, p = _port(tmp_path, Repo(entrypoint=entry, env={"JAVA_OPTS": None}))
    assert p.override_exposure is OverrideExposure.ENV_POSSIBLE
    assert any(s.object and s.object.endswith("#JAVA_OPTS") for s in p.exposure_sources), p.exposure_sources


def test_shell_value_from_an_unknown_variable_is_unresolved(tmp_path: Path) -> None:
    entry = 'ENTRYPOINT ["sh", "-c", "exec java -jar /app/app.jar --server.port=\\"$PORT\\""]\n'
    r, p = _port(tmp_path, Repo(entrypoint=entry, env={"PORT": None}))
    assert p.status is FactStatus.UNRESOLVED and "PORT" in (p.unresolved_reason or ""), p


def test_shell_value_from_a_declared_variable_is_resolved(tmp_path: Path) -> None:
    entry = 'ENTRYPOINT ["sh", "-c", "exec java -jar /app/app.jar --server.port=${PORT:-8080}"]\n'
    _r, p = _port(tmp_path, Repo(entrypoint=entry, env={"PORT": "9300"}))
    assert p.value == 9300
    _r2, p2 = _port(_sub(tmp_path, "unset"), Repo(entrypoint=entry))
    assert p2.value == 8080  # unset: the shell default applies


def test_a_script_the_scanner_cannot_model_makes_every_property_unresolved(tmp_path: Path) -> None:
    entry = 'ENTRYPOINT ["sh", "-c", "echo starting; exec java -jar /app/app.jar"]\n'
    r, p = _port(tmp_path, Repo(entrypoint=entry, resources={"application.yml": "server:\n  port: 8085\n"}))
    assert p.status is FactStatus.UNRESOLVED and "container command" in (p.unresolved_reason or ""), p
    assert _profiles(r).status is FactStatus.UNRESOLVED


# ---------------------------------------------------------------- exec-form words and kubelet references


def test_an_exec_argument_with_a_space_is_one_word(tmp_path: Path) -> None:
    r, p = _port(tmp_path, Repo(command=['--spring.application.json={"server": {"port": 9200}}']))
    assert p.value == 9200, (p, _notices(r))


def test_kubelet_reference_is_expanded_from_the_container_env(tmp_path: Path) -> None:
    r, p = _port(tmp_path, Repo(command=["--server.port=$(PORT)"], env={"PORT": "9400"}), oracle=False)
    assert p.value == 9400, (p, _notices(r))


def test_kubelet_reference_to_an_unresolved_variable_is_unresolved(tmp_path: Path) -> None:
    r, p = _port(tmp_path, Repo(command=["--server.port=$(PORT)"], env={"PORT": None}), oracle=False)
    assert p.status is FactStatus.UNRESOLVED, p


def test_kubelet_reference_to_an_unset_variable_stays_literal(tmp_path: Path) -> None:
    # the kubelet leaves $(PORT) unchanged when PORT is not defined, so Boot reads the text "$(PORT)"
    r, _p = _port(tmp_path, Repo(command=["--spring.application.name=$(NAME)"]), oracle=False)
    assert _props(r)["spring.application.name"].value == "$(NAME)"


def test_a_whole_unknown_kubelet_argument_can_set_anything(tmp_path: Path) -> None:
    r, p = _port(tmp_path, Repo(command=["$(EXTRA)"], env={"EXTRA": None}), oracle=False)
    assert p.status is FactStatus.UNRESOLVED or p.override_exposure is OverrideExposure.ENV_POSSIBLE, p


# ---------------------------------------------------------------- java launcher options


def test_add_modules_value_is_not_the_main_class(tmp_path: Path) -> None:
    entry = 'ENTRYPOINT ["java", "--add-modules", "java.se", "-Dserver.port=9500", "-jar", "/app/app.jar"]\n'
    r, p = _port(tmp_path, Repo(entrypoint=entry))
    assert p.value == 9500, (p, _notices(r))


def test_main_module_ends_the_jvm_options(tmp_path: Path) -> None:
    entry = 'ENTRYPOINT ["java", "-m", "app/bench.App", "-Dserver.port=9600"]\n'
    r, p = _port(tmp_path, Repo(entrypoint=entry))
    assert p.value == 8080, (p, _notices(r))  # -D after the main module is a program argument, which Boot ignores


# ---------------------------------------------------------------- profile groups


def test_profile_group_names_are_matched_by_exact_spelling(tmp_path: Path) -> None:
    yml = "spring:\n  profiles:\n    active: prod-eu\n    group:\n      prodeu: [extra]\n"
    r = _scan(tmp_path, Repo(resources={"application.yml": yml}))
    assert _profiles(r).value == ["prod-eu"]
    yml2 = "spring:\n  profiles:\n    active: prod-eu\n    group:\n      prod-eu: [extra]\n"
    r2 = _scan(_sub(tmp_path, "b"), Repo(resources={"application.yml": yml2}))
    assert _profiles(r2).value == ["prod-eu", "extra"]


def test_profile_group_from_the_environment_uses_its_lower_case_name(tmp_path: Path) -> None:
    yml = "spring:\n  profiles:\n    active: prod\n"
    r = _scan(tmp_path, Repo(resources={"application.yml": yml}, env={"SPRING_PROFILES_GROUP_PROD": "extra"}))
    assert _profiles(r).value == ["prod", "extra"]


# ---------------------------------------------------------------- conditional documents


COND = "server:\n  port: ${app.port:8080}\n---\nspring:\n  config:\n    activate:\n      on-cloud-platform: kubernetes\napp:\n  port: 9700\n"


def test_placeholder_input_set_in_an_unevaluated_document_is_unresolved(tmp_path: Path) -> None:
    yml = "spring:\n  main:\n    cloud-platform: ${PLATFORM}\n" + "---\n" + COND
    r, p = _port(tmp_path, Repo(resources={"application.yml": yml}), oracle=False)
    assert p.status is FactStatus.UNRESOLVED and "app.port" in (p.unresolved_reason or ""), (p, _notices(r))


def test_os_environment_beats_an_unevaluated_document(tmp_path: Path) -> None:
    yml = ("spring:\n  main:\n    cloud-platform: ${PLATFORM}\n---\nspring:\n  config:\n    activate:\n"
           "      on-cloud-platform: kubernetes\nserver:\n  port: 9800\n")
    r, p = _port(tmp_path, Repo(resources={"application.yml": yml}, docker_env="ENV SERVER_PORT=9900\n"), oracle=False)
    assert p.value == 9900 and p.status is not FactStatus.UNRESOLVED, (p, _notices(r))


# ---------------------------------------------------------------- indexed names with a gap


def test_environment_list_with_a_missing_index_is_unresolved(tmp_path: Path) -> None:
    r = _scan(tmp_path, Repo(env={"MANAGEMENT_ENDPOINTS_WEB_EXPOSURE_INCLUDE_1_": "health"}), oracle=False)
    p = _props(r)["management.endpoints.web.exposure.include"]
    assert p.status is FactStatus.UNRESOLVED and "index" in (p.unresolved_reason or ""), p


def test_argument_list_with_a_missing_index_is_unresolved(tmp_path: Path) -> None:
    r = _scan(tmp_path, Repo(command=["--management.endpoints.web.exposure.include[1]=health"]), oracle=False)
    p = _props(r)["management.endpoints.web.exposure.include"]
    assert p.status is FactStatus.UNRESOLVED and "index" in (p.unresolved_reason or ""), p


# ---------------------------------------------------------------- review round 4 findings


def test_shell_options_before_c_are_read(tmp_path: Path) -> None:
    for i, entry in enumerate(('SHELL ["/bin/bash", "-o", "pipefail", "-c"]\nENTRYPOINT java -Dserver.port=9000 -jar /app/app.jar\n',
                               'ENTRYPOINT ["sh", "-e", "-c", "exec java -Dserver.port=9000 -jar /app/app.jar"]\n',
                               'ENTRYPOINT ["sh", "-c", "--", "exec java -Dserver.port=9000 -jar /app/app.jar"]\n',
                               'ENTRYPOINT ["bash", "--norc", "-c", "exec java -Dserver.port=9000 -jar /app/app.jar"]\n')):
        r, p = _port(_sub(tmp_path, str(i)), Repo(entrypoint=entry))
        assert p.value == 9000, (entry, p, _notices(r))


def test_commands_that_do_not_start_java_make_properties_unresolved(tmp_path: Path) -> None:
    for i, entry in enumerate(('ENTRYPOINT ["/app/start.sh"]\n', 'ENTRYPOINT ["sh", "-c", "exec /app/start.sh"]\n',
                               'ENTRYPOINT ["sh", "/app/start.sh"]\n', 'ENTRYPOINT ["env", "SERVER_PORT=9000", "java", "-jar", "/app/app.jar"]\n',
                               'ENTRYPOINT ["sh", "-c", "exec sh -c \'java -jar /app/app.jar\'"]\n',
                               'ENTRYPOINT ["zsh", "-c", "exec java $JAVA_OPTS -jar /app/app.jar"]\n',
                               'ENTRYPOINT ["bash", "-lc", "exec java -jar /app/app.jar"]\n',
                               'ENTRYPOINT ["java", "@/app/jvm.args", "-jar", "/app/app.jar"]\n')):
        r, p = _port(_sub(tmp_path, str(i)), Repo(entrypoint=entry))
        assert p.status is FactStatus.UNRESOLVED, (entry, p, _notices(r))


def test_wrappers_that_keep_the_environment_are_followed(tmp_path: Path) -> None:
    for i, entry in enumerate(('ENTRYPOINT ["tini", "--", "java", "-Dserver.port=9000", "-jar", "/app/app.jar"]\n',
                               'ENTRYPOINT ["dumb-init", "--", "sh", "-c", "exec java -Dserver.port=9000 -jar /app/app.jar"]\n')):
        r, p = _port(_sub(tmp_path, str(i)), Repo(entrypoint=entry))
        assert p.value == 9000, (entry, p, _notices(r))


def test_assigning_expansions_and_brace_expansion_are_opaque(tmp_path: Path) -> None:
    for i, entry in enumerate(('ENTRYPOINT ["sh", "-c", "exec java -Dserver.port=${P:=9000} -jar /app/app.jar"]\n',
                               'ENTRYPOINT ["bash", "-c", "exec java -jar /app/app.jar --spring.profiles.active={a,b}"]\n')):
        r, p = _port(_sub(tmp_path, str(i)), Repo(entrypoint=entry))
        assert p.status is FactStatus.UNRESOLVED, (entry, p)


def test_braced_positional_and_redirections_are_modelled(tmp_path: Path) -> None:
    for i, entry in enumerate(('ENTRYPOINT ["sh", "-c", "exec java ${JAVA_OPTS} -jar /app/app.jar ${@}", "app"]\n',
                               'ENTRYPOINT ["sh", "-c", "exec java -jar /app/app.jar \\"$@\\" 2>&1", "app"]\n')):
        r, p = _port(_sub(tmp_path, str(i)), Repo(entrypoint=entry, command=["--server.port=9000"]))
        assert p.value == 9000, (entry, p, _notices(r))


def test_module_equals_form_ends_jvm_options(tmp_path: Path) -> None:
    r, p = _port(tmp_path, Repo(entrypoint='ENTRYPOINT ["java", "--module=app/bench.App", "-Dserver.port=9000"]\n'))
    assert p.value == 8080


def test_kubelet_service_link_variables_are_unknown(tmp_path: Path) -> None:
    r, p = _port(tmp_path, Repo(command=["--server.port=$(REDIS_SERVICE_PORT)"]), oracle=False)
    assert p.status is FactStatus.UNRESOLVED, p


def test_env_value_with_a_kubelet_reference_is_unresolved(tmp_path: Path) -> None:
    r, p = _port(tmp_path, Repo(env={"B": "9000", "SERVER_PORT": "$(B)"}), oracle=False)
    assert p.status is FactStatus.UNRESOLVED, p


def test_indexed_environment_group_matches_the_profile(tmp_path: Path) -> None:
    for i, var in enumerate(("SPRING_PROFILES_GROUP_PROD_0_", "SPRING_PROFILES_GROUP_PROD_0")):
        r = _scan(_sub(tmp_path, str(i)), Repo(resources={"application.yml": "server:\n  port: 8081\n", "application-db.yml": "server:\n  port: 9000\n"},
                                                env={"SPRING_PROFILES_ACTIVE": "prod", var: "db"}), oracle=False)
        assert _profiles(r).value == ["prod", "db"], var


def test_file_list_with_a_missing_index_is_unresolved(tmp_path: Path) -> None:
    r = _scan(tmp_path, Repo(resources={"application.properties": "management.endpoints.web.exposure.include[1]=health\n"}), oracle=False)
    p = _props(r)["management.endpoints.web.exposure.include"]
    assert p.status is FactStatus.UNRESOLVED, p


def test_environment_list_without_trailing_underscore(tmp_path: Path) -> None:
    r = _scan(tmp_path, Repo(env={"MANAGEMENT_ENDPOINTS_WEB_EXPOSURE_INCLUDE_0": "health", "MANAGEMENT_ENDPOINTS_WEB_EXPOSURE_INCLUDE_1": "info"}),
              oracle=False)
    assert _props(r)["management.endpoints.web.exposure.include"].value == ["health", "info"]


# ---------------------------------------------------------------- review round 5 findings


def test_env_options_change_the_environment(tmp_path: Path) -> None:
    for i, entry in enumerate(('ENTRYPOINT ["env", "-i", "java", "-jar", "/app/app.jar"]\n',
                               'ENTRYPOINT ["env", "-u", "SERVER_PORT", "java", "-jar", "/app/app.jar"]\n')):
        r, p = _port(_sub(tmp_path, str(i)), Repo(entrypoint=entry, env={"SERVER_PORT": "9100"}))
        assert p.status is FactStatus.UNRESOLVED and "env before java" in (p.unresolved_reason or ""), (entry, p)


def test_options_files_make_properties_unresolved(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    r, p = _port(tmp_path / "a", Repo(env={"JDK_JAVA_OPTIONS": "@/app/jvm.args"}))
    assert p.status is FactStatus.UNRESOLVED
    r, p = _port(_sub(tmp_path, "b"), Repo(entrypoint='ENTRYPOINT ["java", "-XX:VMOptionsFile=/app/vm.opts", "-jar", "/app/app.jar"]\n'))
    assert p.status is FactStatus.UNRESOLVED


def test_ansi_c_quoting_and_quoted_brace_bodies_are_opaque(tmp_path: Path) -> None:
    for i, entry in enumerate(('ENTRYPOINT ["bash", "-c", "exec java -jar /app/app.jar --server.port=$\'9000\'"]\n',
                               'ENTRYPOINT ["bash", "-c", "exec java -jar /app/app.jar --spring.profiles.active={\\"a\\",b}"]\n')):
        r, p = _port(_sub(tmp_path, str(i)), Repo(entrypoint=entry))
        assert p.status is FactStatus.UNRESOLVED, (entry, p)


def test_java_from_java_home_is_followed(tmp_path: Path) -> None:
    r, p = _port(tmp_path, Repo(entrypoint='ENTRYPOINT ["sh", "-c", "exec $JAVA_HOME/bin/java -Dserver.port=9000 -jar /app/app.jar"]\n'))
    assert p.value == 9000, (p, _notices(r))


def test_tini_options_with_values_are_skipped(tmp_path: Path) -> None:
    r, p = _port(tmp_path, Repo(entrypoint='ENTRYPOINT ["tini", "-e", "143", "--", "java", "-Dserver.port=9000", "-jar", "/app/app.jar"]\n'))
    assert p.value == 9000


def test_shell_service_link_variable_is_unknown(tmp_path: Path) -> None:
    entry = 'ENTRYPOINT ["sh", "-c", "exec java -jar /app/app.jar --spring.data.redis.port=\\"${REDIS_SERVICE_PORT:-6379}\\""]\n'
    r = _scan(tmp_path, Repo(entrypoint=entry), oracle=False)
    assert _props(r)["spring.data.redis.port"].status is FactStatus.UNRESOLVED


def test_both_index_spellings_set_is_unresolved(tmp_path: Path) -> None:
    r = _scan(tmp_path, Repo(env={"MANAGEMENT_ENDPOINTS_WEB_EXPOSURE_INCLUDE_0": "health", "MANAGEMENT_ENDPOINTS_WEB_EXPOSURE_INCLUDE_0_": "info"}),
              oracle=False)
    assert _props(r)["management.endpoints.web.exposure.include"].status is FactStatus.UNRESOLVED


def test_combined_shell_flags_with_c(tmp_path: Path) -> None:
    r, p = _port(tmp_path, Repo(entrypoint='ENTRYPOINT ["sh", "-ec", "exec java -Dserver.port=9000 -jar /app/app.jar"]\n'))
    assert p.value == 9000, (p, _notices(r))


def test_field_splitting_uses_only_space_tab_newline() -> None:
    from fifthnine.resolve import shellcmd

    words = shellcmd.expand("java $V", [], lambda n: ("a\x0bb c", True)).words
    assert [w.text for w in words] == ["java", "a\x0bb", "c"]


def test_in_repository_service_link_variable_is_unresolved(tmp_path: Path) -> None:
    from fifthnine.core.engine import ScanOptions, run_scan

    Repo().write(tmp_path)
    (tmp_path / "svc.yaml").write_text("apiVersion: v1\nkind: Service\nmetadata:\n  name: server\n  namespace: shop\n"
                                       "spec:\n  selector: {app: other}\n  ports:\n    - port: 80\n")
    r = run_scan(ScanOptions(path=tmp_path, facts="all"))
    p = _props(r)[PORT]
    assert p.status is FactStatus.UNRESOLVED and "Service server" in (p.unresolved_reason or ""), p
