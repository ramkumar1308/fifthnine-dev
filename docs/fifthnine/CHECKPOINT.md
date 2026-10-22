# Checkpoint

Updated 2026-10-22. Read this first when resuming.

- **Current task:** M2-1, READY FOR REVIEW once the independent review and CI below are complete. Not merged.
- **Branch:** `feature/m2-1-deployment-facts`, based on 29b7103. See `git log` for the head.
- **Done:** schema revision 3, Dockerfile and Kubernetes extraction, defaults table, environment model, effective command, linkage, scorer, semantic oracle, corpus run (see `M2-1.md`).
- **Tests:** 235 pass on Python 3.10, 3.11, 3.12; ruff and mypy clean. Oracle shows 0 mismatches on both samples and 26 corpus repositories.
- **Benchmark:** `M2-1.md`. Reproduce with `python benchmarks/dev/corpus/m2_report.py CORPUS_DIR OUT.json`.
- **Review:** first independent review done (2 blockers, 6 majors fixed; dispositions in `M2-1.md`). Second and third reviews: all findings fixed (dispositions in `M2-1.md`). Final re-review result is recorded in the PR.
- **Open:** none for CI (7 of 7 green at a0a0726; the temporary diagnostics step is removed). Windows crashed on 5,000-level YAML nesting, a C-stack crash fixed with a depth pre-check.
- **For the CEO:** the linkage implementation was tightened after review (no label changed to fit): 24 links became 12, because 12 rested on an in-image directory name. Agreement with M2-0 labels stays 25 of 26. Answer key SP-K08 changed from `declared` to `inferred`.
- **Gates:** no merge without CEO approval. M2-2 starts only after M2-1 merges. HX2 protocol is drafted after M2-1 merges and is not executed without CEO approval. No AI implementation is authorized.
