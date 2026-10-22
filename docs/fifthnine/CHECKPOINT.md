# Checkpoint

Updated 2026-10-22. Read this first when resuming.

- **Current task:** M2-2 (Spring Boot application configuration extraction), in independent review. Not merged. PR #6 (M2-1) merged at 4dcc093; PR #7 (Kubernetes defaults count) awaits CEO approval.
- **Branch:** `feature/m2-2-spring-config`, based on main 4dcc093. See `git log` for the head.
- **Done:** config readers, resolver with Spring precedence, overridden facts, exposure, actuator endpoint derivation, Boot 3.2 to 3.5 defaults table, redaction, independent oracle, tests (see `M2-2.md`).
- **Tests:** 313 pass on Python 3.10, 3.11, 3.12; ruff and mypy clean. Answer key 66 correct, 0 wrong, 0 missing. Oracle 720 items, 0 mismatches on 28 corpus repositories; 70 unresolved or absent endpoint facts are skipped and disclosed. Mutation check 14 of 14 killed.
- **Carried forward:** the 1,778 M2-1 oracle skips are coverage limitations, not verified.
- **Next:** independent review, then CI 7 of 7, then mark ready for CEO merge approval.
- **Follow-ups (separate):** HX2 protocol draft as its own doc PR, not executed; M2-D3 Spring runtime-fixture CI job; Spring anchors in `verify_upstream.py`; secret redaction for M2-1 env facts.
- **Gates:** no merge without CEO approval. HX2 is not executed without CEO approval. No AI implementation is authorized. Phase 1 scope is locked.
