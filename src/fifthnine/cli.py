"""FifthNine command line. Interface frozen at M0 (docs/fifthnine/m0/CLI.md).

Commands and flags that belong to later milestones are present so the interface stays stable, but they
refuse with exit code 2 and say which milestone implements them. Nothing is silently ignored.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any, NoReturn

import click

from fifthnine import __version__
from fifthnine.core.engine import FifthNineError, ScanOptions, exit_code, run_scan
from fifthnine.output.console import render_console
from fifthnine.output.json_report import render_json
from fifthnine.schema.v1 import SCHEMA_ID, Severity

EXIT_ERROR = 2


def _fail(message: str) -> NoReturn:
    click.echo(f"fifthnine: error: {message}", err=True)
    sys.exit(EXIT_ERROR)


def _not_in_m1(feature: str, milestone: str) -> NoReturn:
    _fail(f"{feature} is part of the frozen CLI but is implemented in {milestone}. This build (M1) cannot honor it.")


def _write(text: str, output: str | None) -> None:
    if output is None:
        click.echo(text, nl=False)
        return
    try:
        with Path(output).open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    except OSError as exc:
        _fail(f"cannot write {output}: {exc.strerror}")


def _use_color(no_color: bool) -> bool:
    return not no_color and "NO_COLOR" not in os.environ and sys.stdout.isatty()


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, "--version", prog_name="fifthnine",
                      message=f"%(prog)s %(version)s (schema {SCHEMA_ID}, rules: none in this build)")
def cli() -> None:
    """Local-first production-readiness review. Start with: fifthnine scan ."""


@cli.command()
@click.argument("path", default=".", type=click.Path(file_okay=True, dir_okay=True, path_type=Path))
@click.option("--profile", help="Spring profiles to treat as active (comma-separated).")
@click.option("--base-ref", help="Diff mode against a git ref (M4).")
@click.option("--format", "fmt", type=click.Choice(["console", "json", "sarif"]), default="console", show_default=True)
@click.option("--output", type=click.Path(dir_okay=False), help="Write the report to this file instead of stdout.")
@click.option("--baseline", type=click.Path(), help="Baseline file (M4).")
@click.option("--config", "config_path", type=click.Path(path_type=Path), help="Repository config (default: .fifthnine.yml).")
@click.option("--mode", type=click.Choice(["report", "gate"]), help="report (default) or gate.")
@click.option("--fail-on", type=click.Choice([s.value for s in Severity if s is not Severity.INFO]), help="Gate threshold (default high).")
@click.option("--rule", "rules", multiple=True, help="Only run these rules.")
@click.option("--skip-rule", "skip_rules", multiple=True, help="Skip these rules.")
@click.option("--facts", type=click.Choice(["referenced", "all", "none"]), default="referenced", show_default=True)
@click.option("--max-file-size", type=click.IntRange(min=1), default=1_048_576, show_default=True)
@click.option("--timing", is_flag=True, help="Add durations (makes output non-deterministic).")
@click.option("--no-color", is_flag=True)
@click.option("--quiet", "-q", is_flag=True)
@click.option("--verbose", "-v", is_flag=True)
def scan(path: Path, profile: str | None, base_ref: str | None, fmt: str, output: str | None, baseline: str | None,
         config_path: Path | None, mode: str | None, fail_on: str | None, rules: tuple[str, ...], skip_rules: tuple[str, ...],
         facts: str, max_file_size: int, timing: bool, no_color: bool, quiet: bool, verbose: bool) -> None:
    """Scan a repository (default: current directory)."""
    if base_ref:
        _not_in_m1("--base-ref (diff mode)", "M4")
    if baseline:
        _not_in_m1("--baseline", "M4")
    if fmt == "sarif":
        _not_in_m1("--format sarif", "M4")
    if timing:
        _not_in_m1("--timing", "a later milestone")
    for rule in (*rules, *skip_rules):
        _fail(f"unknown rule id {rule!r}: this build contains no rules (see `fifthnine rules list`)")
    if baseline is None and (path / ".fifthnine-baseline.json").is_file():
        click.echo("fifthnine: note: .fifthnine-baseline.json present but baselines are implemented in M4; it was not applied.", err=True)

    opts = ScanOptions(path=path, profile=profile, config_path=config_path,
                       policy_mode=mode,  # type: ignore[arg-type]
                       fail_on=Severity(fail_on) if fail_on else None,
                       facts=facts,  # type: ignore[arg-type]
                       max_file_size=max_file_size)
    try:
        result = run_scan(opts)
    except FifthNineError as exc:
        _fail(str(exc))
    except Exception as exc:  # noqa: BLE001 - last-resort guard so the exit code stays 2
        if verbose:
            traceback.print_exc()
        _fail(f"internal error ({type(exc).__name__}: {exc}). Re-run with --verbose and report it.")

    color = _use_color(no_color)
    if fmt == "json":
        _write(render_json(result), output)
        if output and not quiet:
            click.echo(render_console(result, color=False), err=True, nl=False)
    else:
        if not quiet or output:
            _write(render_console(result, color=color and output is None), output)
    sys.exit(exit_code(result))


@cli.group()
def rules() -> None:
    """List and explain reliability rules."""


@rules.command("list")
@click.option("--domain", help="Filter by domain.")
@click.option("--format", "fmt", type=click.Choice(["table", "json"]), default="table", show_default=True)
def rules_list(domain: str | None, fmt: str) -> None:
    """List rules in this build."""
    if fmt == "json":
        click.echo(json.dumps({"rules": [], "note": "no rules in this build (M1)"}, indent=2))
    else:
        click.echo("No reliability rules in this build (M1: CLI foundation). The first rules arrive in M3.")


@rules.command("explain")
@click.argument("rule_id")
def rules_explain(rule_id: str) -> None:
    """Explain one rule."""
    _fail(f"unknown rule id {rule_id!r}: this build contains no rules")


@cli.group(invoke_without_command=True)
@click.pass_context
def links(ctx: click.Context) -> None:
    """Show proposed service linkages (M2)."""
    if ctx.invoked_subcommand is None:
        _not_in_m1("`fifthnine links`", "M2")


@links.command("accept")
@click.option("--yes", is_flag=True)
def links_accept(yes: bool) -> None:
    """Declare proposed linkages in .fifthnine.yml (M2)."""
    _not_in_m1("`fifthnine links accept`", "M2")


@cli.group()
def baseline() -> None:
    """Manage baselines (M4)."""


@baseline.command("create")
@click.option("--path", default=".")
@click.option("--output", default=".fifthnine-baseline.json")
@click.option("--profile")
def baseline_create(path: str, output: str, profile: str | None) -> None:
    """Write a baseline of current findings (M4)."""
    _not_in_m1("`fifthnine baseline create`", "M4")


@cli.command()
@click.argument("which", type=click.Choice(["result", "config", "baseline", "support-matrix"]))
def schema(which: str) -> None:
    """Print a frozen JSON Schema."""
    from fifthnine.schema.config_v1 import Baseline, FifthNineConfig
    from fifthnine.schema.support_v1 import SupportMatrix
    from fifthnine.schema.v1 import ScanResult

    model: Any = {"result": ScanResult, "config": FifthNineConfig, "baseline": Baseline, "support-matrix": SupportMatrix}[which]
    data = model.model_json_schema(by_alias=True)
    data["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    click.echo(json.dumps(data, indent=2, sort_keys=True))


def main() -> None:
    cli(prog_name="fifthnine")


if __name__ == "__main__":
    main()
