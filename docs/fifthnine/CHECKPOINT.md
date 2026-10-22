# Checkpoint

Updated 2026-10-22. Read this first when resuming.

- **Current task:** M2-1, READY FOR REVIEW once the independent review and CI below are complete. Not merged.
- **Branch:** `feature/m2-1-deployment-facts`, based on 29b7103. See `git log` for the head.
- **Done:** schema revision 3, Dockerfile and Kubernetes extraction, defaults table, environment model, effective command, linkage, scorer, semantic oracle, corpus run (see `M2-1.md`).
- **Tests:** 214 pass on Python 3.10, 3.11, 3.12; ruff and mypy clean. Oracle shows 0 mismatches on both samples and 26 corpus repositories.
- **Benchmark:** `M2-1.md`. Reproduce with `python benchmarks/dev/corpus/m2_report.py CORPUS_DIR OUT.json`.
- **Open:** independent review of the PR (fresh reviewer, code and keys only); 7/7 CI including the `dockerfile` wheel on Windows, macOS and Python 3.10.
- **Decision for the CEO:** whether to tighten the linkage rule for `COPY --from` sources (12 of 24 corpus links rest on an in-image directory name). See `M2-1.md`.
- **Gates:** no merge without CEO approval. M2-2 starts only after M2-1 merges. HX2 protocol is drafted after M2-1 merges and is not executed without CEO approval. No AI implementation is authorized.
