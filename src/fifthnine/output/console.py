"""Console renderer for people. Never parse it; use --format json."""

from __future__ import annotations

import click

from fifthnine.schema.v1 import ScanResult, SupportLevel

LEVEL_COLOR = {SupportLevel.TESTED: "green", SupportLevel.UNTESTED: "yellow", SupportLevel.UNSUPPORTED: "red"}


def render_console(result: ScanResult, color: bool = False) -> str:
    def style(text: str, **kw: object) -> str:
        return click.style(text, **kw) if color else text  # type: ignore[arg-type]

    s, inv = result.scan, result.inventory
    v = {k.value: n for k, n in result.summary.by_verdict.items()}
    lines = [
        f"FifthNine Scan {result.tool.version}   {s.root}   {s.policy_mode} mode   profile: {s.profile or '(none)'} ({s.profile_source})",
        f"Status: {style(result.status.value, bold=True, fg='yellow')}   {v['VIOLATION']} violation  {v['CONDITIONAL']} conditional  "
        f"{v['UNKNOWN']} unknown  {v['SATISFIED']} satisfied   would block in gate mode (>= {s.fail_on.value}): {result.summary.blocking}",
        f"        {result.status_reason}",
        "",
        f"Inventory: {inv.files_considered} files considered, {inv.files_parsed} parsed, {len(inv.skipped)} skipped",
    ]
    for sk in inv.skipped:
        lines.append(f"  skipped  {sk.reason:<20} {sk.path}" + (f"  ({sk.detail})" if sk.detail else ""))
    lines += ["", "Detected stack"]
    if not inv.detected_stacks:
        lines.append("  (no supported framework, runtime or Kubernetes object detected)")
    for c in inv.detected_stacks:
        where = f"{c.version_source.path}:{c.version_source.line_start}" if c.version_source else ""
        level = style(f"[{c.support.value}]", fg=LEVEL_COLOR[c.support])
        lines.append(f"  {c.component:<16} {c.version or '(version not determined)':<40} {level:<12} {where}")
    lines += ["", "Coverage and limits"]
    for n in result.coverage:
        lines.append(f"  {n.code:<24} {n.scope}")
        lines.append(f"  {'':24} {n.message}")
    lines += ["", result.disclaimer]
    return "\n".join(lines) + "\n"
