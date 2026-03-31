"""CLI entry points for pact.

Commands:
  pact init <project-dir>              Scaffold a new project
  pact status <project-dir> [comp]     Show project or component status
  pact run <project-dir>               Run the pipeline (single burst or poll loop)
  pact daemon <project-dir>            Run event-driven daemon (FIFO-based, zero-delay)
  pact stop <project-dir>              Gracefully stop a running daemon
  pact signal <project-dir>            Send signal to daemon (resume, approve, etc.)
  pact log <project-dir>               Show audit trail
  pact ping                            Test API connection and show pricing
  pact interview <project-dir>         Run interview phase only
  pact answer <project-dir>            Answer interview questions
  pact approve <project-dir>           Approve interview + signal daemon to continue
  pact validate <project-dir>          Re-run contract validation gate
  pact design <project-dir>            Regenerate design.md
  pact components <project-dir>        List all components with status
  pact build <project-dir> <id>        Rebuild a specific component
  pact tree <project-dir>              ASCII tree visualization of decomposition
  pact cost <project-dir>              Estimate remaining cost
  pact doctor                          Diagnose common issues
  pact clean <project-dir>             Clean up artifacts
  pact resume <project-dir>            Resume a failed or paused run
  pact diff <project-dir> <id>         Diff between competitive implementations
  pact watch <project-dir>...          Start the Sentinel monitor
  pact report <project-dir> <error>    Manually report a production error
  pact incidents <project-dir>         List active/recent incidents
  pact incident <project-dir> <id>     Show incident details + diagnostic report
  pact ci <project-dir>               Generate GitHub Actions CI workflow
  pact deploy <project-dir>           Generate baton.yaml topology config
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

from pact.config import load_global_config, load_project_config
from pact.lifecycle import format_run_summary
from pact.project import ProjectManager

logger = logging.getLogger(__name__)


def main() -> None:
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="pact",
        description="Contract-first multi-agent architecture",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Enable verbose logging",
    )
    subparsers = parser.add_subparsers(dest="command")

    # init
    p_init = subparsers.add_parser("init", help="Initialize a new project")
    p_init.add_argument("project_dir", help="Project directory path")
    p_init.add_argument("--budget", type=float, default=10.00, help="Budget cap in dollars")

    # status
    p_status = subparsers.add_parser("status", help="Show project or component status")
    p_status.add_argument("project_dir", help="Project directory path")
    p_status.add_argument("component_id", nargs="?", default=None, help="Optional component ID for detailed view")

    # run (legacy poll-based)
    p_run = subparsers.add_parser("run", help="Run the pipeline (poll-based)")
    p_run.add_argument("project_dir", help="Project directory path")
    p_run.add_argument("--once", action="store_true", help="Run one burst only")
    p_run.add_argument("--force-new", action="store_true", help="Clear state and start fresh")
    p_run.add_argument("--constrain-dir", default="", help="Constrain output directory")
    p_run.add_argument("--ledger-dir", default="", help="Ledger assertion exports directory")
    p_run.add_argument("--skip-arbiter", action="store_true", help="Skip Arbiter gate phase")

    # daemon (FIFO-based, event-driven)
    p_daemon = subparsers.add_parser("daemon", help="Run event-driven daemon (recommended)")
    p_daemon.add_argument("project_dir", help="Project directory path")
    p_daemon.add_argument("--force-new", action="store_true", help="Clear state and start fresh")
    p_daemon.add_argument(
        "--health-interval", type=int, default=30,
        help="Seconds between health checks when waiting (default: 30)",
    )
    p_daemon.add_argument(
        "--max-idle", type=int, default=600,
        help="Max seconds to wait for human input before exiting (default: 600)",
    )

    # stop
    p_stop = subparsers.add_parser("stop", help="Gracefully stop a running daemon")
    p_stop.add_argument("project_dir", help="Project directory path")

    # log
    p_log = subparsers.add_parser("log", help="Show audit trail")
    p_log.add_argument("project_dir", help="Project directory path")
    p_log.add_argument("--tail", type=int, default=0, help="Show last N entries (default: all)")
    p_log.add_argument("--json", action="store_true", dest="json_output", help="Output as JSON")

    # ping
    p_ping = subparsers.add_parser("ping", help="Test API connection and show pricing")

    # signal
    p_signal = subparsers.add_parser("signal", help="Send signal to running daemon")
    p_signal.add_argument("project_dir", help="Project directory path")
    p_signal.add_argument("--msg", default="resume", help="Signal message (default: resume)")

    # interview
    p_interview = subparsers.add_parser("interview", help="Run interview phase")
    p_interview.add_argument("project_dir", help="Project directory path")

    # answer (interactive)
    p_answer = subparsers.add_parser("answer", help="Answer interview questions interactively")
    p_answer.add_argument("project_dir", help="Project directory path")

    # approve (answer + signal)
    p_approve = subparsers.add_parser("approve", help="Approve interview with defaults + signal daemon")
    p_approve.add_argument("project_dir", help="Project directory path")
    p_approve.add_argument("-i", "--interactive", action="store_true", help="Prompt for each question before auto-filling")

    # validate
    p_validate = subparsers.add_parser("validate", help="Re-run contract validation")
    p_validate.add_argument("project_dir", help="Project directory path")

    # design
    p_design = subparsers.add_parser("design", help="Regenerate design.md")
    p_design.add_argument("project_dir", help="Project directory path")

    # components
    p_components = subparsers.add_parser("components", help="List all components with status")
    p_components.add_argument("project_dir", help="Project directory path")
    p_components.add_argument("--json", action="store_true", dest="json_output", help="Output as JSON")

    # build
    p_build = subparsers.add_parser("build", help="Rebuild a specific component")
    p_build.add_argument("project_dir", help="Project directory path")
    p_build.add_argument("component_id", help="Component ID to build")
    p_build.add_argument("--competitive", action="store_true", help="Use competitive mode")
    p_build.add_argument("--agents", type=int, default=2, help="Number of competing agents (default: 2)")
    p_build.add_argument("--plan-only", action="store_true", help="Show what would be built without building")

    # tree
    p_tree = subparsers.add_parser("tree", help="ASCII tree visualization of decomposition")
    p_tree.add_argument("project_dir", help="Project directory path")
    p_tree.add_argument("--json", action="store_true", dest="json_output", help="Output as JSON")
    p_tree.add_argument("--no-cost", action="store_true", help="Hide cost information")

    # cost
    p_cost = subparsers.add_parser("cost", help="Estimate remaining cost")
    p_cost.add_argument("project_dir", help="Project directory path")
    p_cost.add_argument("--detailed", action="store_true", help="Per-component estimates")

    # doctor
    p_doctor = subparsers.add_parser("doctor", help="Diagnose common issues")
    p_doctor.add_argument("project_dir", nargs="?", default=None, help="Optional project directory")

    # clean
    p_clean = subparsers.add_parser("clean", help="Clean up project artifacts")
    p_clean.add_argument("project_dir", help="Project directory path")
    p_clean.add_argument("--attempts", action="store_true", help="Remove all attempt artifacts")
    p_clean.add_argument("--stale", action="store_true", help="Remove stale FIFO, PID, shutdown sentinels")
    p_clean.add_argument("--all", action="store_true", dest="clean_all", help="Remove all .pact/ state")

    # resume
    p_resume = subparsers.add_parser("resume", help="Resume a failed or paused run")
    p_resume.add_argument("project_dir", help="Project directory path")
    p_resume.add_argument("--from-phase", default=None, help="Override resume phase")

    # diff
    p_diff = subparsers.add_parser("diff", help="Diff between implementations or attempts")
    p_diff.add_argument("project_dir", help="Project directory path")
    p_diff.add_argument("component_id", help="Component ID to diff")



    # tasks
    p_tasks = subparsers.add_parser("tasks", help="Generate/display task list")
    p_tasks.add_argument("project_dir", help="Project directory path")
    p_tasks.add_argument("--regenerate", action="store_true", help="Force regeneration")
    p_tasks.add_argument("--json", action="store_true", dest="json_output", help="Output as JSON")
    p_tasks.add_argument("--phase", default=None, help="Filter by phase")
    p_tasks.add_argument("--component", default=None, help="Filter by component")
    p_tasks.add_argument("--complete", default=None, metavar="TASK_ID", help="Mark a task as completed")

    # analyze
    p_analyze = subparsers.add_parser("analyze", help="Run cross-artifact analysis")
    p_analyze.add_argument("project_dir", help="Project directory path")
    p_analyze.add_argument("--json", action="store_true", dest="json_output", help="Output as JSON")

    # checklist
    p_checklist = subparsers.add_parser("checklist", help="Generate requirements checklist")
    p_checklist.add_argument("project_dir", help="Project directory path")
    p_checklist.add_argument("--json", action="store_true", dest="json_output", help="Output as JSON")

    # directive
    p_directive = subparsers.add_parser("directive", help="Send structured directive to daemon")
    p_directive.add_argument("project_dir", help="Project directory path")
    p_directive.add_argument("json_or_command", help="JSON directive or simple command string")

    # export-tasks
    p_export = subparsers.add_parser("export-tasks", help="Export TASKS.md")
    p_export.add_argument("project_dir", help="Project directory path")

    p_audit = subparsers.add_parser("audit", help="Spec-compliance audit")
    p_audit.add_argument("project_dir", help="Project directory path")
    p_audit.add_argument("--json", action="store_true", dest="json_output", help="Output as JSON")

    # test-gen
    p_testgen = subparsers.add_parser("test-gen", help="Generate tests + security audit for any codebase")
    p_testgen.add_argument("project_dir", help="Root directory of the codebase to analyze")
    p_testgen.add_argument("--language", default="python", choices=["python", "typescript", "rust"], help="Language (default: python)")
    p_testgen.add_argument("--budget", type=float, default=10.0, help="Max LLM spend in dollars (default: 10.0)")
    p_testgen.add_argument("--model", default="claude-sonnet-4-5-20250929", help="LLM model for generation")
    p_testgen.add_argument("--backend", default="anthropic", help="LLM backend (default: anthropic)")
    p_testgen.add_argument("--complexity-threshold", type=int, default=5, help="Min complexity for priority (default: 5)")
    p_testgen.add_argument("--dry-run", action="store_true", help="Run analysis only, no LLM calls")
    p_testgen.add_argument("--json", action="store_true", dest="json_output", help="Output result as JSON")
    p_testgen.add_argument("--include-covered", action="store_true", help="Include already-covered functions")

    # adopt
    p_adopt = subparsers.add_parser("adopt", help="Adopt existing codebase under pact governance")
    p_adopt.add_argument("project_dir", help="Root directory of the codebase to adopt")
    p_adopt.add_argument("--language", default="python", choices=["python", "typescript", "rust"], help="Language (default: python)")
    p_adopt.add_argument("--budget", type=float, default=10.0, help="Max LLM spend in dollars (default: 10.0)")
    p_adopt.add_argument("--model", default="claude-sonnet-4-5-20250929", help="LLM model for generation")
    p_adopt.add_argument("--backend", default="anthropic", help="LLM backend (default: anthropic)")
    p_adopt.add_argument("--complexity-threshold", type=int, default=5, help="Min complexity for priority (default: 5)")
    p_adopt.add_argument("--dry-run", action="store_true", help="Analyze only, no LLM calls or state changes")
    p_adopt.add_argument("--include", action="append", default=None,
                         help="Scope to directory, file list, or - for stdin (repeatable)")
    p_adopt.add_argument("--exclude", action="append", default=None,
                         help="Additional directories to skip (repeatable)")

    # health
    p_health = subparsers.add_parser("health", help="Check pipeline health (dysmemic pressure detection)")
    p_health.add_argument("project_dir", help="Project directory path")

    # pricing
    p_pricing = subparsers.add_parser("pricing", help="Show or export model pricing table")
    p_pricing.add_argument("--export", action="store_true", help="Export pricing to ~/.config/pact/model_pricing.json")
    p_pricing.add_argument("--json", action="store_true", dest="json_output", help="Output as JSON")

    # handoff
    p_handoff = subparsers.add_parser("handoff", help="Render/validate handoff brief for a component")
    p_handoff.add_argument("project_dir", help="Project directory path")
    p_handoff.add_argument("component_id", help="Component ID to render handoff for")
    p_handoff.add_argument("--validate", action="store_true", help="Run structural validation on the handoff")
    p_handoff.add_argument("--max-tokens", type=int, default=0, help="Apply tiered compression (0=no limit)")
    p_handoff.add_argument("--json", action="store_true", dest="json_output", help="Output validation results as JSON")

    # wizard
    p_wizard = subparsers.add_parser("wizard", help="Guided project setup wizard")
    p_wizard.add_argument("project_dir", help="Project directory path")
    p_wizard.add_argument("--config", default=None, metavar="FILE", help="JSON/YAML config file for non-interactive mode")
    p_wizard.add_argument("--budget", type=float, default=None, help="Override budget (skips budget question)")

    # ci
    p_ci = subparsers.add_parser("ci", help="Generate GitHub Actions CI workflow for a pact-managed project")
    p_ci.add_argument("project_dir", help="Project directory path")
    p_ci.add_argument("--output", default=None, help="Override output path (default: .github/workflows/pact-verify.yml)")

    # deploy
    p_deploy = subparsers.add_parser("deploy", help="Generate baton.yaml topology config for a pact-managed project")

    # mcp-server
    p_mcp = subparsers.add_parser("mcp-server", help="Run MCP server (stdio transport)")
    p_mcp.add_argument("--project-dir", default=None, help="Project directory (default: auto-detect)")

    # Audit separation (two-repo model)
    p_audit_init = subparsers.add_parser("audit-init", help="Initialize audit repo for separation of privilege")
    p_audit_init.add_argument("project_dir", help="Project directory path (code repo)")
    p_audit_init.add_argument("--audit-dir", required=True, help="Path to audit repo directory")
    p_audit_init.add_argument("--audit-repo", default="", help="Git URL of audit repo (optional)")

    p_sync = subparsers.add_parser("sync", help="Sync visible tests from audit repo to code repo")
    p_sync.add_argument("project_dir", help="Project directory path (code repo)")

    p_certify = subparsers.add_parser("certify", help="Run certification (all tests against code repo)")
    p_certify.add_argument("project_dir", help="Project directory path")
    p_certify.add_argument("--json", action="store_true", dest="json_output", help="Output as JSON")
    p_certify.add_argument("--verify-only", action="store_true", help="Only verify existing certification")
    p_deploy.add_argument("project_dir", help="Project directory path")
    p_deploy.add_argument("--output", default=None, help="Override output path (default: baton.yaml in project root)")
    p_deploy.add_argument("--sink", default="jsonl", choices=["jsonl", "otel"], help="Observability sink (default: jsonl)")
    p_deploy.add_argument("--error-rate", type=float, default=5.0, help="Canary error rate threshold percent (default: 5.0)")
    p_deploy.add_argument("--p95-ms", type=float, default=500.0, help="Canary p95 latency threshold in ms (default: 500)")

    # Sentinel integration subcommands
    p_sentinel = subparsers.add_parser("sentinel", help="Sentinel integration commands")
    sentinel_sub = p_sentinel.add_subparsers(dest="sentinel_command")

    p_sentinel_status = sentinel_sub.add_parser("status", help="Show Sentinel connection config")
    p_sentinel_status.add_argument("project_dir", help="Project directory path")

    p_sentinel_push = sentinel_sub.add_parser("push-contract", help="Accept tightened contract from Sentinel")
    p_sentinel_push.add_argument("project_dir", help="Project directory path")
    p_sentinel_push.add_argument("component_id", help="Component ID")
    p_sentinel_push.add_argument("contract_file", help="Path to tightened contract JSON")

    p_sentinel_keys = sentinel_sub.add_parser("list-keys", help="List all PACT keys in project")
    p_sentinel_keys.add_argument("project_dir", help="Project directory path")

    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(name)s: %(message)s")
    else:
        logging.basicConfig(level=logging.INFO, format="%(message)s")

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "init":
        cmd_init(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "run":
        asyncio.run(cmd_run(args))
    elif args.command == "daemon":
        asyncio.run(cmd_daemon(args))
    elif args.command == "stop":
        cmd_stop(args)
    elif args.command == "log":
        cmd_log(args)
    elif args.command == "ping":
        asyncio.run(cmd_ping(args))
    elif args.command == "signal":
        cmd_signal(args)
    elif args.command == "interview":
        asyncio.run(cmd_interview(args))
    elif args.command == "answer":
        cmd_answer(args)
    elif args.command == "approve":
        cmd_approve(args)
    elif args.command == "validate":
        cmd_validate(args)
    elif args.command == "design":
        cmd_design(args)
    elif args.command == "components":
        cmd_components(args)
    elif args.command == "build":
        asyncio.run(cmd_build(args))
    elif args.command == "tree":
        cmd_tree(args)
    elif args.command == "cost":
        cmd_cost(args)
    elif args.command == "doctor":
        cmd_doctor(args)
    elif args.command == "clean":
        cmd_clean(args)
    elif args.command == "resume":
        cmd_resume(args)
    elif args.command == "diff":
        cmd_diff(args)
    elif args.command == "tasks":
        cmd_tasks(args)
    elif args.command == "analyze":
        cmd_analyze(args)
    elif args.command == "checklist":
        cmd_checklist(args)
    elif args.command == "directive":
        cmd_directive(args)
    elif args.command == "export-tasks":
        cmd_export_tasks(args)
    elif args.command == "audit":
        asyncio.run(cmd_audit(args))
    elif args.command == "test-gen":
        asyncio.run(cmd_test_gen(args))
    elif args.command == "adopt":
        asyncio.run(cmd_adopt(args))
    elif args.command == "health":
        cmd_health(args)
    elif args.command == "pricing":
        cmd_pricing(args)
    elif args.command == "handoff":
        cmd_handoff(args)
    elif args.command == "wizard":
        cmd_wizard(args)
    elif args.command == "ci":
        cmd_ci(args)
    elif args.command == "deploy":
        cmd_deploy(args)
    elif args.command == "mcp-server":
        cmd_mcp_server(args)
    elif args.command == "audit-init":
        cmd_audit_init(args)
    elif args.command == "sync":
        asyncio.run(cmd_sync(args))
    elif args.command == "certify":
        asyncio.run(cmd_certify(args))
    elif args.command == "sentinel":
        cmd_sentinel(args)


def cmd_mcp_server(args: argparse.Namespace) -> None:
    """Run the Pact MCP server (stdio transport)."""
    import os
    if args.project_dir:
        os.environ["PACT_PROJECT_DIR"] = str(args.project_dir)
    from pact.mcp_server import main as mcp_main
    mcp_main()


def cmd_wizard(args: argparse.Namespace) -> None:
    """Guided project setup wizard."""
    from pathlib import Path

    import yaml

    from pact.project import ProjectManager
    from pact.wizard import (
        build_wizard_questions,
        generate_pact_yaml,
        generate_sops_md,
        generate_task_md,
        load_wizard_config_from_file,
        run_wizard_interactive,
    )

    project_dir = args.project_dir

    if args.config:
        config = load_wizard_config_from_file(Path(args.config))
    else:
        questions = build_wizard_questions()
        print("Pact Project Wizard")
        print("=" * 40)
        print("Answer each question to configure your project.")
        print("Press Enter to accept defaults shown in [brackets].\n")
        config = run_wizard_interactive(questions)

    if args.budget is not None:
        config.budget = args.budget

    project = ProjectManager(project_dir)
    project.init(budget=config.budget)

    project.task_path.write_text(generate_task_md(config))
    project.sops_path.write_text(generate_sops_md(config))

    pact_yaml = generate_pact_yaml(config)
    with open(project.config_path, "w") as f:
        yaml.dump(pact_yaml, f, default_flow_style=False, sort_keys=False)

    print(f"\nProject initialized: {project.project_dir}")
    print(f"  task.md   - Pre-filled from your description")
    print(f"  sops.md   - Tailored for {config.language}")
    print(f"  pact.yaml - Configured with your preferences")

    if config.run_interview:
        print("\nStarting interview phase...")
        asyncio.run(cmd_interview(argparse.Namespace(
            project_dir=project_dir, verbose=False,
        )))
    else:
        print(f"\nNext steps:")
        print(f"  1. Review and refine task.md")
        print(f"  2. pact interview {project_dir}")
        print(f"  3. pact approve {project_dir}")
        print(f"  4. pact run {project_dir}")


def cmd_pricing(args: argparse.Namespace) -> None:
    """Show or export model pricing table."""
    from pact.budget import get_model_pricing_table, save_pricing_file, DEFAULT_PRICING_PATH

    pricing = get_model_pricing_table()

    if args.export:
        path = save_pricing_file()
        print(f"Pricing exported to {path}")
        print("Edit this file to override default pricing.")
        return

    if args.json_output:
        import json
        data = {model: list(costs) for model, costs in sorted(pricing.items())}
        print(json.dumps(data, indent=2))
        return

    print("Model Pricing (per million tokens)")
    print(f"{'Model':<40} {'Input':>10} {'Output':>10}")
    print("-" * 62)
    for model, (inp, out) in sorted(pricing.items()):
        print(f"{model:<40} ${inp:>8.2f} ${out:>8.2f}")
    print()
    print(f"Override file: {DEFAULT_PRICING_PATH}")


def _kindex_prompt_and_index(project_dir: str) -> None:
    """Check kindex availability and offer code indexing on first run."""
    from pact import kindex_integration as kindex
    from pathlib import Path

    if not kindex.is_available():
        return

    directory = Path(project_dir).resolve()
    auto = kindex.should_auto_index(directory)
    if auto is True:
        print("Kindex: auto-indexing codebase...")
        kindex.index_codebase(directory)
    elif auto is None:
        # Not configured — prompt
        print("Kindex detected. Index this codebase for cross-session context?")
        print("  [y] Yes  [n] No  [a] Always (save)  [v] Never (save)")
        choice = input("  Choice [y]: ").strip().lower() or "y"
        if choice in ("y", "a"):
            print("Indexing codebase...")
            kindex.index_codebase(directory)
        if choice == "a":
            kindex.write_kin_config(directory, {"auto_index": True})
            print("  Saved to .kin/config (auto_index: true)")
        elif choice == "v":
            kindex.write_kin_config(directory, {"auto_index": False})
            print("  Saved to .kin/config (auto_index: false)")


def _kindex_fetch_context(project_dir: str) -> str | None:
    """Fetch kindex context for the project topic. Returns context string or None."""
    from pact import kindex_integration as kindex
    from pathlib import Path

    if not kindex.is_available():
        return None

    directory = Path(project_dir).resolve()
    kin_config = kindex.read_kin_config(directory)
    topic = kin_config.get("name", directory.name)
    context = kindex.fetch_context(f"{topic} architecture components")
    if context.strip():
        print(f"Loaded kindex context for '{topic}'.")
        return context
    return None


def _kindex_publish_task(project_dir: str) -> None:
    """Publish task.md and sops.md to kindex after init."""
    from pact import kindex_integration as kindex
    from pathlib import Path

    if not kindex.is_available():
        return

    directory = Path(project_dir).resolve()
    task_path = directory / "task.md"
    if task_path.exists():
        content = task_path.read_text(encoding="utf-8")
        # Skip if still a template
        if "Describe your task here" not in content:
            kindex.publish_task(
                title=f"Pact Task: {directory.name}",
                content=content,
                tags=["pact", directory.name],
            )


def cmd_init(args: argparse.Namespace) -> None:
    """Initialize a new project."""
    from pact.archive import list_archived_sessions

    project = ProjectManager(args.project_dir)
    project.init(budget=args.budget)

    # Show archive info if artifacts were just archived
    sessions = list_archived_sessions(project.archive_dir)
    if sessions:
        latest = sessions[0]
        print(f"Archived previous artifacts to .pact/archive/{latest['slug']}/")

    # Kindex: offer code indexing + fetch context
    _kindex_prompt_and_index(args.project_dir)
    kindex_context = _kindex_fetch_context(args.project_dir)

    print(f"Initialized project: {project.project_dir}")
    print(f"  Edit {project.task_path} to describe your task")
    print(f"  Edit {project.sops_path} to set operating procedures")

    if kindex_context:
        print(f"  Kindex context loaded — will be available during interview phase")

    if sessions:
        print(f"  Previous sessions available: {', '.join(s['slug'] for s in sessions)}")

    print(f"  Then run: pact daemon {args.project_dir}")


def cmd_status(args: argparse.Namespace) -> None:
    """Show project status, or detailed component status if component_id given."""
    from pact.daemon import check_daemon_health

    project = ProjectManager(args.project_dir)

    # If component_id given, show detailed component view
    if args.component_id:
        _show_component_detail(project, args.component_id)
        return

    # Daemon status
    health = check_daemon_health(args.project_dir)
    if health["alive"]:
        print(f"Daemon: running (PID {health['pid']})")
    elif health["fifo_exists"]:
        print("Daemon: FIFO exists but process not found (stale?)")
    else:
        print("Daemon: not running")

    if not project.has_state():
        print("No active run. Use 'pact daemon' to start.")
        return

    state = project.load_state()
    print(format_run_summary(state))

    # Show interview summary if available
    interview = project.load_interview()
    if interview:
        total_q = len(interview.questions)
        answered = len(interview.user_answers)
        pending = total_q - answered
        approved_str = "approved" if interview.approved else "not approved"
        print(f"\nInterview: {total_q} questions, {answered} answered ({pending} pending) — {approved_str}")
        for q in interview.questions:
            truncated_q = q[:60] + "..." if len(q) > 60 else q
            if q in interview.user_answers:
                truncated_a = interview.user_answers[q]
                truncated_a = truncated_a[:50] + "..." if len(truncated_a) > 50 else truncated_a
                print(f'  [answered] "{truncated_q}" -> "{truncated_a}"')
            else:
                print(f'  [pending]  "{truncated_q}"')

    # Show tree status if available
    tree = project.load_tree()
    if tree:
        print(f"\nDecomposition: {len(tree.nodes)} components")
        for node in tree.nodes.values():
            status_icon = {
                "pending": "[ ]",
                "contracted": "[C]",
                "implemented": "[I]",
                "tested": "[+]",
                "failed": "[X]",
            }.get(node.implementation_status, "[ ]")
            indent = "  " * node.depth
            print(f"  {indent}{status_icon} {node.name} ({node.component_id})")

    # Show audit trail summary
    audit = project.load_audit()
    if audit:
        print(f"\nAudit trail: {len(audit)} entries")
        for entry in audit[-5:]:
            print(f"  {entry.get('timestamp', '')[:19]} {entry.get('action', '')} — {entry.get('detail', '')}")


def _show_component_detail(project: ProjectManager, component_id: str) -> None:
    """Show detailed status for a single component."""
    tree = project.load_tree()
    if not tree:
        print("No decomposition tree found.")
        return

    node = tree.nodes.get(component_id)
    if not node:
        print(f"Component not found: {component_id}")
        print("Available components:")
        for n in tree.nodes.values():
            print(f"  {n.component_id}: {n.name}")
        return

    # Basic info
    node_type = "parent" if node.children else "leaf"
    print(f"Component: {node.name}")
    print(f"  ID: {node.component_id}")
    print(f"  Type: {node_type} (depth {node.depth})")
    print(f"  Status: {node.implementation_status}")
    if node.parent_id:
        parent = tree.nodes.get(node.parent_id)
        print(f"  Parent: {parent.name if parent else node.parent_id}")
    if node.children:
        print(f"  Children: {', '.join(node.children)}")

    # Contract
    contract = project.load_contract(component_id)
    if contract:
        print(f"\nContract (v{contract.version}):")
        print(f"  Functions: {len(contract.functions)}")
        for fn in contract.functions:
            inputs = ", ".join(f"{i.name}: {i.type_ref}" for i in fn.inputs)
            print(f"    {fn.name}({inputs}) -> {fn.output_type}")
        if contract.types:
            print(f"  Types: {', '.join(t.name for t in contract.types)}")
        if contract.dependencies:
            print(f"  Dependencies: {', '.join(contract.dependencies)}")
        if contract.invariants:
            print(f"  Invariants:")
            for inv in contract.invariants:
                print(f"    - {inv}")
    else:
        print("\nContract: not yet generated")

    # Tests
    suite = project.load_test_suite(component_id)
    if suite:
        print(f"\nTest Suite:")
        print(f"  Cases: {len(suite.test_cases)}")
        for tc in suite.test_cases:
            print(f"    [{tc.category}] {tc.id}: {tc.description[:60]}")
        if suite.generated_code:
            lines = suite.generated_code.count("\n") + 1
            print(f"  Generated code: {lines} lines")
    else:
        print("\nTest Suite: not yet generated")

    # Test results
    if node.test_results:
        tr = node.test_results
        status = "PASS" if tr.all_passed else "FAIL"
        print(f"\nTest Results: {status} ({tr.passed}/{tr.total} passed, {tr.failed} failed, {tr.errors} errors)")
        if tr.failure_details:
            print(f"  Failures:")
            for fd in tr.failure_details:
                print(f"    {fd.test_id}: {fd.error_message[:80]}")

    # Attempts
    attempts = project.list_attempts(component_id)
    if attempts:
        print(f"\nAttempts: {len(attempts)}")
        for a in attempts:
            attempt_type = a.get("type", "competitive")
            print(f"  {a['attempt_id']} ({attempt_type})")

    # Implementation files
    impl_src = project.impl_src_dir(component_id)
    if impl_src.exists() and any(impl_src.iterdir()):
        files = list(impl_src.rglob("*"))
        source_files = [f for f in files if f.is_file()]
        print(f"\nImplementation: {len(source_files)} file(s)")
        for f in source_files[:10]:
            print(f"  {f.relative_to(impl_src)}")
        if len(source_files) > 10:
            print(f"  ... and {len(source_files) - 10} more")


def _kindex_publish_project(project_dir: str) -> None:
    """Publish project artifacts to kindex after a run completes."""
    from pact import kindex_integration as kindex
    from pathlib import Path
    import json

    if not kindex.is_available():
        return

    directory = Path(project_dir).resolve()
    published = 0

    # Publish task
    task_path = directory / "task.md"
    if task_path.exists():
        content = task_path.read_text(encoding="utf-8")
        if "Describe your task here" not in content:
            kindex.publish_task(
                title=f"Pact Task: {directory.name}",
                content=content,
                tags=["pact", directory.name],
            )
            published += 1

    # Publish decomposition decisions
    decisions_path = directory / "decomposition" / "decisions.json"
    if decisions_path.exists():
        try:
            decisions = json.loads(decisions_path.read_text(encoding="utf-8"))
            for d in (decisions if isinstance(decisions, list) else []):
                if isinstance(d, dict) and d.get("decision"):
                    kindex.publish_decision(
                        title=d.get("decision", "")[:80],
                        rationale=d.get("rationale", d.get("decision", "")),
                        tags=["pact", directory.name],
                    )
                    published += 1
        except Exception:
            pass

    # Publish decomposition tree components
    tree_path = directory / "decomposition" / "tree.json"
    if tree_path.exists():
        try:
            published += kindex.publish_decomposition(
                tree_path.read_text(encoding="utf-8"),
                tags=["pact", directory.name],
            )
        except Exception:
            pass

    # Publish contracts (visible, not goodhart)
    contracts_dir = directory / "contracts"
    if contracts_dir.exists():
        for iface in contracts_dir.rglob("interface.json"):
            try:
                component_id = iface.parent.name
                kindex.publish_contract(
                    component_id,
                    iface.read_text(encoding="utf-8"),
                    tags=["pact", directory.name],
                )
                published += 1
            except Exception:
                pass

    if published:
        print(f"Published {published} item(s) to kindex.")

    kindex.close()


async def cmd_run(args: argparse.Namespace) -> None:
    """Run the pipeline (poll-based, legacy)."""
    from pact.budget import BudgetTracker
    from pact.scheduler import Scheduler

    project = ProjectManager(args.project_dir)
    global_config = load_global_config()
    project_config = load_project_config(args.project_dir)

    if args.force_new:
        project.clear_state()

    if not project.has_state():
        state = project.create_run()
        project.save_state(state)

    budget = BudgetTracker(
        per_project_cap=project_config.budget or global_config.default_budget,
    )

    scheduler = Scheduler(project, global_config, project_config, budget)

    if args.once:
        state = await scheduler.run_once()
    else:
        state = await scheduler.run_forever()

    print(format_run_summary(state))
    _kindex_publish_project(args.project_dir)


async def cmd_daemon(args: argparse.Namespace) -> None:
    """Run the event-driven daemon."""
    from pact.budget import BudgetTracker
    from pact.daemon import Daemon
    from pact.scheduler import Scheduler

    project = ProjectManager(args.project_dir)
    global_config = load_global_config()
    project_config = load_project_config(args.project_dir)

    if args.force_new:
        project.clear_state()

    if not project.has_state():
        state = project.create_run()
        project.save_state(state)

    budget = BudgetTracker(
        per_project_cap=project_config.budget or global_config.default_budget,
    )

    from pact.events import EventBus

    scheduler = Scheduler(project, global_config, project_config, budget)

    # Resolve polling config: project > global
    poll_integrations = (
        project_config.poll_integrations
        if project_config.poll_integrations is not None
        else global_config.poll_integrations
    )
    poll_interval = (
        project_config.poll_interval
        if project_config.poll_interval is not None
        else global_config.poll_interval
    )
    max_poll_attempts = (
        project_config.max_poll_attempts
        if project_config.max_poll_attempts is not None
        else global_config.max_poll_attempts
    )

    # Phase timeout: 0 = no hard timeout (phases run to completion).
    # Complexity determines duration, not a fixed clock.
    # A stall-detection approach (no API progress for N seconds) is
    # the right way to detect stuck phases, not wall-clock caps.
    phase_timeout = global_config.autonomous_timeout  # 0 by default

    daemon = Daemon(
        project, scheduler,
        health_check_interval=args.health_interval,
        max_idle=args.max_idle,
        phase_timeout=phase_timeout,
        event_bus=scheduler.event_bus,
        poll_integrations=poll_integrations,
        poll_interval=poll_interval,
        max_poll_attempts=max_poll_attempts,
    )

    print(f"Daemon starting for: {project.project_dir}")
    print(f"  FIFO: {daemon.fifo_path}")
    print(f"  Health check: every {args.health_interval}s")
    print(f"  Max idle: {args.max_idle}s")
    if phase_timeout > 0:
        print(f"  Phase timeout: {phase_timeout}s (hard wall-clock)")
    else:
        print(f"  Phase timeout: none (stall-based detection)")

    if poll_integrations:
        print(f"  Integration polling: every {poll_interval}s (max {max_poll_attempts} attempts)")
    print(f"  Resume with: pact signal {args.project_dir}")
    print()

    state = await daemon.run()
    print()
    print(format_run_summary(state))
    _kindex_publish_project(args.project_dir)


def cmd_log(args: argparse.Namespace) -> None:
    """Show audit trail."""
    project = ProjectManager(args.project_dir)
    audit = project.load_audit()

    if not audit:
        print("No audit entries.")
        return

    if args.tail > 0:
        audit = audit[-args.tail:]

    if getattr(args, "json_output", False):
        print(json.dumps(audit, indent=2))
        return

    for entry in audit:
        ts = entry.get("timestamp", "")[:19]
        action = entry.get("action", "")
        detail = entry.get("detail", "")
        print(f"{ts}  {action:<20s}  {detail}")

    if not args.tail:
        print(f"\n{len(audit)} entries total")


async def cmd_ping(args: argparse.Namespace) -> None:
    """Test API connection and show pricing configuration."""
    import os
    from pact.budget import get_model_pricing_table

    global_config = load_global_config()

    # Show configured pricing
    pricing = get_model_pricing_table()
    print("Model Pricing (per million tokens):")
    print(f"  {'Model':<35s} {'Input':>8s}  {'Output':>8s}")
    print(f"  {'-'*35} {'-'*8}  {'-'*8}")
    for model, (inp, out) in sorted(pricing.items()):
        print(f"  {model:<35s} ${inp:>7.2f}  ${out:>7.2f}")

    if global_config.model_pricing:
        print("\n  (includes overrides from config.yaml)")

    # Test API connection
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("\nAPI Connection: ANTHROPIC_API_KEY not set")
        print("  Set it with: export ANTHROPIC_API_KEY=sk-...")
        return

    print(f"\nAPI Key: ...{api_key[-8:]}")
    print(f"Default model: {global_config.model}")

    try:
        import anthropic
    except ImportError:
        print("API Connection: anthropic package not installed")
        print("  Install with: pip install -e '.[llm]'")
        return

    print("Testing connection...", end=" ", flush=True)
    try:
        client = anthropic.AsyncAnthropic(api_key=api_key, timeout=15.0)
        try:
            message = await client.messages.create(
                model=global_config.model,
                max_tokens=16,
                messages=[{"role": "user", "content": "Reply with only the word: pong"}],
            )
            reply = message.content[0].text.strip() if message.content else ""
            in_tok = message.usage.input_tokens
            out_tok = message.usage.output_tokens
            print(f"OK ({reply})")
            print(f"  Tokens: {in_tok} in / {out_tok} out")
            cost = (
                in_tok * pricing.get(global_config.model, (0, 0))[0] / 1_000_000
                + out_tok * pricing.get(global_config.model, (0, 0))[1] / 1_000_000
            )
            print(f"  Cost: ${cost:.6f}")
        finally:
            await client.close()
    except Exception as e:
        print(f"FAILED")
        print(f"  Error: {e}")


def cmd_stop(args: argparse.Namespace) -> None:
    """Gracefully stop a running daemon.

    Uses two mechanisms:
    1. FIFO "shutdown" signal (if daemon is paused and waiting on FIFO)
    2. Sentinel file .pact/shutdown (if daemon is mid-phase)
    The daemon checks for both between phases and during FIFO waits.
    """
    from pathlib import Path
    from pact.daemon import check_daemon_health, send_signal

    health = check_daemon_health(args.project_dir)
    if not health["alive"]:
        print("No daemon running.")
        return

    # Write sentinel file for mid-phase shutdown
    pact_dir = Path(args.project_dir).resolve() / ".pact"
    shutdown_path = pact_dir / "shutdown"
    shutdown_path.write_text("shutdown")

    # Also try FIFO signal in case daemon is paused
    send_signal(args.project_dir, "shutdown")

    print(f"Shutdown signal sent to daemon (PID {health['pid']}).")
    print("Daemon will stop cleanly after the current phase completes.")
    print(f"State is preserved — restart with: pact daemon {args.project_dir}")


def cmd_signal(args: argparse.Namespace) -> None:
    """Send a signal to the running daemon."""
    from pact.daemon import check_daemon_health, send_signal

    health = check_daemon_health(args.project_dir)
    if not health["alive"]:
        print("No daemon running. Start with: pact daemon <project-dir>")
        return

    sent = send_signal(args.project_dir, args.msg)
    if sent:
        print(f"Signal sent: {args.msg}")
    else:
        print("Failed to send signal (FIFO not found or daemon not listening)")


async def cmd_interview(args: argparse.Namespace) -> None:
    """Run the interview phase only."""
    from pact.agents.base import AgentBase
    from pact.budget import BudgetTracker
    from pact.config import resolve_backend, resolve_model
    from pact.decomposer import run_interview

    project = ProjectManager(args.project_dir)
    global_config = load_global_config()
    project_config = load_project_config(args.project_dir)

    budget = BudgetTracker(
        per_project_cap=project_config.budget or global_config.default_budget,
    )

    model = resolve_model("decomposer", project_config, global_config)
    backend = resolve_backend("decomposer", project_config, global_config)
    budget.set_model_pricing(model)

    agent = AgentBase(budget=budget, model=model, backend=backend)
    try:
        task = project.load_task()
        sops = project.load_sops()
        result = await run_interview(agent, task, sops)
        project.save_interview(result)

        if result.questions:
            print("Interview questions:")
            for i, q in enumerate(result.questions, 1):
                print(f"  {i}. {q}")
            print(f"\nRisks: {', '.join(result.risks)}")
            print(f"Assumptions: {', '.join(result.assumptions)}")
            if result.acceptance_criteria:
                print(f"\nAcceptance criteria (done when):")
                for i, ac in enumerate(result.acceptance_criteria, 1):
                    print(f"  {i}. {ac}")
            print(f"\nAnswer with: pact answer {args.project_dir}")
        else:
            print("No questions — ready to decompose.")
            result.approved = True
            project.save_interview(result)
    finally:
        await agent.close()


def cmd_answer(args: argparse.Namespace) -> None:
    """Answer interview questions interactively."""
    project = ProjectManager(args.project_dir)
    interview = project.load_interview()

    if not interview:
        print("No interview found. Run 'pact interview' first.")
        return

    if interview.approved:
        print("Interview already approved.")
        return

    print("Answer each question (or press Enter to accept assumption):\n")
    for q in interview.questions:
        assumption = next(
            (a for a in interview.assumptions if a.lower() in q.lower()),
            "No default",
        )
        answer = input(f"Q: {q}\n  [Default: {assumption}]\n  A: ").strip()
        interview.user_answers[q] = answer or assumption

    interview.approved = True
    project.save_interview(interview)
    print("\nInterview complete. Run 'pact run' to proceed.")


STOPWORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "shall",
    "should", "may", "might", "can", "could", "must",
    "for", "to", "in", "of", "on", "at", "by", "with", "from", "about",
    "into", "through", "during", "before", "after", "above", "below",
    "between", "out", "off", "over", "under", "again", "further",
    "then", "once", "here", "there", "when", "where", "why", "how",
    "all", "each", "every", "both", "few", "more", "most", "other",
    "some", "such", "no", "nor", "not", "only", "own", "same",
    "so", "than", "too", "very", "just", "because", "as", "until",
    "while", "if", "or", "and", "but", "yet", "what", "which", "who",
    "whom", "this", "that", "these", "those", "i", "me", "my", "we",
    "our", "you", "your", "he", "him", "his", "she", "her", "it", "its",
    "they", "them", "their",
})


def match_answer_to_question(
    question: str,
    assumptions: list[str],
    question_index: int = 0,
) -> tuple[str, float]:
    """Match a question to the best assumption for auto-approval.

    Algorithm (in order):
      1. Keyword overlap (>= 2 significant words shared): use best match
         Confidence: word_overlap / max(len_q_words, len_a_words)
      2. Index-based pairing: if question_index < len(assumptions), use assumptions[question_index]
         Confidence: 0.5
      3. No match: return ("Accepted as stated", 0.0)

    Significant words: exclude STOPWORDS
    """
    def significant_words(text: str) -> set[str]:
        return {w.lower().strip("?.,!:;\"'()") for w in text.split()} - STOPWORDS - {""}

    q_words = significant_words(question)
    if not q_words:
        if question_index < len(assumptions):
            return assumptions[question_index], 0.5
        return "Accepted as stated", 0.0

    # 1. Keyword overlap matching
    best_match = ""
    best_confidence = 0.0
    for assumption in assumptions:
        a_words = significant_words(assumption)
        overlap = q_words & a_words
        if len(overlap) >= 2:
            confidence = len(overlap) / max(len(q_words), len(a_words))
            if confidence > best_confidence:
                best_confidence = confidence
                best_match = assumption

    if best_match and best_confidence > 0.0:
        return best_match, best_confidence

    # 2. Index-based fallback
    if question_index < len(assumptions):
        return assumptions[question_index], 0.5

    # 3. No match
    return "Accepted as stated", 0.0


def cmd_approve(args: argparse.Namespace) -> None:
    """Approve interview with defaults and signal daemon to continue."""
    from pact.daemon import send_signal

    project = ProjectManager(args.project_dir)
    interview = project.load_interview()

    if not interview:
        print("No interview found.")
        return

    if interview.approved:
        print("Already approved.")
    else:
        interactive = getattr(args, "interactive", False)
        answer_sources: list[tuple[str, str, str, float]] = []  # (question, answer, source, confidence)

        for i, q in enumerate(interview.questions):
            if q in interview.user_answers:
                # Already answered by user (via pact answer)
                answer_sources.append((q, interview.user_answers[q], "user", 1.0))
                continue

            if interactive:
                auto_answer, confidence = match_answer_to_question(
                    q, interview.assumptions, question_index=i,
                )
                user_input = input(
                    f"\nQ: {q}\n  [auto: {auto_answer} (confidence: {confidence:.1f})]\n  A: "
                ).strip()
                if user_input:
                    interview.user_answers[q] = user_input
                    answer_sources.append((q, user_input, "user", 1.0))
                else:
                    interview.user_answers[q] = auto_answer
                    answer_sources.append((q, auto_answer, "auto", confidence))
            else:
                answer, confidence = match_answer_to_question(
                    q, interview.assumptions, question_index=i,
                )
                interview.user_answers[q] = answer
                answer_sources.append((q, answer, "auto", confidence))

        interview.approved = True

        # Build audited answers with provenance
        from datetime import datetime, timezone
        from pact.schemas import AuditedAnswer, AnswerSource
        interview.audited_answers = [
            AuditedAnswer(
                question_id=f"q_{i:03d}",
                answer=ans,
                source=AnswerSource.USER_INTERACTIVE if src == "user" else AnswerSource.CLI_APPROVE,
                confidence=conf,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
            for i, (_, ans, src, conf) in enumerate(answer_sources)
        ]

        project.save_interview(interview)

        # Print answer summary
        print("Interview approved. Answer summary:")
        for q, answer, source, confidence in answer_sources:
            truncated_q = q[:60] + "..." if len(q) > 60 else q
            truncated_a = answer[:50] + "..." if len(answer) > 50 else answer
            if source == "user":
                print(f'  Q: "{truncated_q}" -> [user] "{truncated_a}"')
            else:
                print(f'  Q: "{truncated_q}" -> [auto, {confidence:.1f}] "{truncated_a}"')

    # Signal daemon if running
    sent = send_signal(args.project_dir, "approved")
    if sent:
        print("Daemon signaled to continue.")
    else:
        print("No daemon running. Start with: pact daemon <project-dir>")


def cmd_validate(args: argparse.Namespace) -> None:
    """Re-run contract validation gate."""
    from pact.contracts import validate_all_contracts

    project = ProjectManager(args.project_dir)
    tree = project.load_tree()
    if not tree:
        print("No decomposition tree found.")
        return

    contracts = project.load_all_contracts()
    test_suites = project.load_all_test_suites()

    gate = validate_all_contracts(tree, contracts, test_suites)

    if gate.passed:
        print("Validation PASSED")
    else:
        print(f"Validation FAILED: {gate.reason}")
        for detail in gate.details:
            print(f"  - {detail}")


def cmd_design(args: argparse.Namespace) -> None:
    """Regenerate design.md from current state."""
    from pact.design_doc import render_design_doc

    project = ProjectManager(args.project_dir)
    doc = project.load_design_doc()

    if not doc:
        print("No design document found. Run decomposition first.")
        return

    markdown = render_design_doc(doc)
    project.design_path.write_text(markdown)
    print(f"Design document updated: {project.design_path}")


def cmd_components(args: argparse.Namespace) -> None:
    """List all components with status."""
    project = ProjectManager(args.project_dir)
    tree = project.load_tree()

    if not tree:
        print("No decomposition tree found. Run decomposition first.")
        return

    contracts = project.load_all_contracts()
    test_suites = project.load_all_test_suites()

    components = []
    for node in tree.nodes.values():
        node_type = "parent" if node.children else "leaf"
        has_contract = node.component_id in contracts
        has_tests = node.component_id in test_suites
        test_count = 0
        if has_tests:
            suite = test_suites[node.component_id]
            test_count = len(suite.test_cases)

        # Attempts info
        attempts = project.list_attempts(node.component_id)

        components.append({
            "id": node.component_id,
            "name": node.name,
            "status": node.implementation_status,
            "type": node_type,
            "depth": node.depth,
            "has_contract": has_contract,
            "has_tests": has_tests,
            "test_count": test_count,
            "attempts": len(attempts),
            "test_results": {
                "passed": node.test_results.passed if node.test_results else 0,
                "total": node.test_results.total if node.test_results else 0,
            },
        })

    if getattr(args, "json_output", False):
        print(json.dumps(components, indent=2))
        return

    # Table output
    print(f"{'ID':<22s} {'Name':<24s} {'Status':<16s} {'Type':<8s} {'Tests':<10s}")
    print("-" * 82)
    for c in components:
        status_icon = {
            "pending": "[ ]",
            "contracted": "[C]",
            "implemented": "[I]",
            "tested": "[+]",
            "failed": "[X]",
        }.get(c["status"], "[ ]")
        status_str = f"{status_icon} {c['status']}"
        test_str = ""
        if c["test_results"]["total"] > 0:
            test_str = f"{c['test_results']['passed']}/{c['test_results']['total']}"
        elif c["test_count"] > 0:
            test_str = f"{c['test_count']} cases"

        indent = "  " * c["depth"]
        name_display = f"{indent}{c['name']}"
        if len(name_display) > 24:
            name_display = name_display[:21] + "..."

        print(f"{c['id']:<22s} {name_display:<24s} {status_str:<16s} {c['type']:<8s} {test_str:<10s}")

    if any(c["attempts"] > 0 for c in components):
        print()
        for c in components:
            if c["attempts"] > 0:
                print(f"  {c['id']}: {c['attempts']} attempt(s) on record")


async def cmd_build(args: argparse.Namespace) -> None:
    """Rebuild a specific component."""
    from pact.budget import BudgetTracker
    from pact.scheduler import Scheduler

    project = ProjectManager(args.project_dir)
    global_config = load_global_config()
    project_config = load_project_config(args.project_dir)

    if not project.has_state():
        print("No active run. Use 'pact run' or 'pact daemon' first.")
        return

    tree = project.load_tree()
    if not tree:
        print("No decomposition tree found.")
        return

    node = tree.nodes.get(args.component_id)
    if not node:
        print(f"Component not found: {args.component_id}")
        print("Available components:")
        for n in tree.nodes.values():
            print(f"  {n.component_id}: {n.name}")
        return

    if getattr(args, "plan_only", False):
        contracts = project.load_all_contracts()
        test_suites = project.load_all_test_suites()
        print(f"Component: {node.name} ({node.component_id})")
        print(f"  Type: {'parent' if node.children else 'leaf'}")
        print(f"  Status: {node.implementation_status}")
        print(f"  Has contract: {node.component_id in contracts}")
        print(f"  Has tests: {node.component_id in test_suites}")
        if node.component_id in test_suites:
            print(f"  Test cases: {len(test_suites[node.component_id].test_cases)}")
        attempts = project.list_attempts(node.component_id)
        print(f"  Prior attempts: {len(attempts)}")
        if args.competitive:
            print(f"  Mode: competitive ({args.agents} agents)")
        else:
            print(f"  Mode: sequential")
        print("\nRun without --plan-only to build.")
        return

    budget = BudgetTracker(
        per_project_cap=project_config.budget or global_config.default_budget,
    )

    scheduler = Scheduler(project, global_config, project_config, budget)

    print(f"Building component: {node.name} ({args.component_id})")
    if args.competitive:
        print(f"  Mode: competitive ({args.agents} agents)")
    else:
        print(f"  Mode: sequential")

    state = await scheduler.build_component(
        args.component_id,
        competitive=args.competitive,
        num_agents=args.agents,
    )

    # Show result
    updated_tree = project.load_tree()
    if updated_tree:
        updated_node = updated_tree.nodes.get(args.component_id)
        if updated_node and updated_node.test_results:
            tr = updated_node.test_results
            if tr.all_passed:
                print(f"\nSUCCESS: {tr.passed}/{tr.total} tests passed")
            else:
                print(f"\nFAILED: {tr.passed}/{tr.total} tests passed, "
                      f"{tr.failed} failed, {tr.errors} errors")

    print(f"\nSpend: ${budget.project_spend:.4f}")


def cmd_tree(args: argparse.Namespace) -> None:
    """ASCII tree visualization of decomposition with status icons."""
    project = ProjectManager(args.project_dir)
    tree = project.load_tree()

    if not tree:
        print("No decomposition tree found. Run decomposition first.")
        return

    show_cost = not getattr(args, "no_cost", False)

    # Compute per-component cost from state
    cost_map: dict[str, float] = {}
    if show_cost and project.has_state():
        state = project.load_state()
        cost_map["_total"] = state.total_cost_usd

    if getattr(args, "json_output", False):
        nodes_data = []
        for node in tree.nodes.values():
            entry = {
                "id": node.component_id,
                "name": node.name,
                "status": node.implementation_status,
                "depth": node.depth,
                "parent_id": node.parent_id,
                "children": node.children,
            }
            if node.test_results:
                entry["test_results"] = {
                    "passed": node.test_results.passed,
                    "total": node.test_results.total,
                }
            nodes_data.append(entry)
        print(json.dumps({"root_id": tree.root_id, "nodes": nodes_data}, indent=2))
        return

    def render_node(node_id: str, prefix: str, is_last: bool) -> None:
        node = tree.nodes.get(node_id)
        if not node:
            return

        status_icon = {
            "pending": "[ ]",
            "contracted": "[C]",
            "implemented": "[I]",
            "tested": "[+]",
            "failed": "[X]",
        }.get(node.implementation_status, "[ ]")

        connector = "\u2514\u2500\u2500 " if is_last else "\u251c\u2500\u2500 "
        if not prefix:
            connector = ""

        cost_str = ""
        if show_cost and node.component_id in cost_map:
            cost_str = f"  ${cost_map[node.component_id]:.2f}"

        line = f"{prefix}{connector}{status_icon} {node.name} ({node.component_id}){cost_str}"
        print(line)

        # Test results on next line
        if node.test_results and node.test_results.total > 0:
            child_prefix = prefix + ("    " if is_last else "\u2502   ")
            if not prefix:
                child_prefix = "    "
            status_word = "passed" if node.test_results.all_passed else "passed"
            print(f"{child_prefix}{node.test_results.passed}/{node.test_results.total} tests passed")

        # Recurse into children
        children = node.children
        for i, child_id in enumerate(children):
            child_is_last = (i == len(children) - 1)
            child_prefix = prefix + ("    " if is_last else "\u2502   ")
            if not prefix:
                child_prefix = ""
            render_node(child_id, child_prefix, child_is_last)

    render_node(tree.root_id, "", True)


def cmd_cost(args: argparse.Namespace) -> None:
    """Estimate remaining cost based on components left to process."""
    from pact.budget import pricing_for_model

    project = ProjectManager(args.project_dir)
    global_config = load_global_config()
    project_config = load_project_config(args.project_dir)

    tree = project.load_tree()
    if not tree:
        print("No decomposition tree found. Run decomposition first.")
        return

    budget_cap = project_config.budget or global_config.default_budget

    # Current spend
    current_spend = 0.0
    if project.has_state():
        state = project.load_state()
        current_spend = state.total_cost_usd

    # Categorize components
    pending = []
    contracted = []
    implemented = []
    tested = []
    failed = []

    for node in tree.nodes.values():
        if node.implementation_status == "pending":
            pending.append(node)
        elif node.implementation_status == "contracted":
            contracted.append(node)
        elif node.implementation_status == "implemented":
            implemented.append(node)
        elif node.implementation_status == "tested":
            tested.append(node)
        elif node.implementation_status == "failed":
            failed.append(node)

    # Estimate cost per phase (rough averages based on typical usage)
    # These are configurable defaults — real projects can refine from learnings
    model = project_config.model or global_config.model
    inp_cost, out_cost = pricing_for_model(model)

    # Estimate tokens per phase (conservative estimates)
    contract_tokens = (4000, 2000)    # ~4k in, 2k out for contract generation
    test_tokens = (3000, 2000)        # ~3k in, 2k out for test generation
    impl_tokens = (6000, 4000)        # ~6k in, 4k out for implementation
    integration_tokens = (4000, 3000) # ~4k in, 3k out for integration

    def estimate_cost(in_tok: int, out_tok: int) -> float:
        return in_tok * inp_cost / 1_000_000 + out_tok * out_cost / 1_000_000

    contract_cost = estimate_cost(*contract_tokens)
    test_cost = estimate_cost(*test_tokens)
    impl_cost = estimate_cost(*impl_tokens)
    integration_cost = estimate_cost(*integration_tokens)

    # Full pipeline per component
    full_cost = contract_cost + test_cost + impl_cost

    # Estimate remaining
    est_contracted = len(contracted) * impl_cost
    est_implemented = len(implemented) * integration_cost
    est_pending = len(pending) * full_cost
    est_failed = len(failed) * impl_cost  # Re-implementation
    est_remaining = est_contracted + est_implemented + est_pending + est_failed

    print("Component Status Breakdown:")
    print(f"  {len(tree.nodes)} total components")
    if contracted:
        print(f"  {len(contracted)} contracted (need implementation)    ~${est_contracted:.2f}")
    if implemented:
        print(f"  {len(implemented)} implemented (need integration)      ~${est_implemented:.2f}")
    if pending:
        print(f"  {len(pending)} pending (need contract + impl)      ~${est_pending:.2f}")
    if failed:
        print(f"  {len(failed)} failed (need re-implementation)    ~${est_failed:.2f}")
    if tested:
        print(f"  {len(tested)} tested (complete)")
    print()
    print(f"Estimated remaining: ${est_remaining:.2f}")
    print(f"Current spend:       ${current_spend:.2f}")
    print(f"Budget remaining:    ${budget_cap - current_spend:.2f} of ${budget_cap:.2f}")

    if args.detailed and tree.nodes:
        print("\nPer-Component Estimates:")
        for node in tree.nodes.values():
            if node.implementation_status == "tested":
                est = 0.0
            elif node.implementation_status == "contracted":
                est = impl_cost
            elif node.implementation_status == "implemented":
                est = integration_cost
            elif node.implementation_status == "failed":
                est = impl_cost
            else:
                est = full_cost
            node_type = "parent" if node.children else "leaf"
            print(f"  {node.component_id:<22s} {node.implementation_status:<14s} {node_type:<8s} ~${est:.2f}")


def cmd_doctor(args: argparse.Namespace) -> None:
    """Diagnose common issues."""
    import os
    from pact.budget import get_model_pricing_table
    from pact.daemon import check_daemon_health

    global_config = load_global_config()

    checks: list[tuple[str, str, str]] = []  # (level, label, detail)

    # API key
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if api_key:
        checks.append(("OK", "ANTHROPIC_API_KEY is set", f"...{api_key[-8:]}"))
    else:
        checks.append(("FAIL", "ANTHROPIC_API_KEY not set", "export ANTHROPIC_API_KEY=sk-..."))

    # anthropic package
    try:
        import anthropic
        version = getattr(anthropic, "__version__", "unknown")
        checks.append(("OK", "anthropic package installed", f"v{version}"))
    except ImportError:
        checks.append(("WARN", "anthropic package not installed", "pip install anthropic"))

    # Model
    checks.append(("OK", "Model", global_config.model))

    # Project-specific checks
    project_dir = getattr(args, "project_dir", None)
    if project_dir:
        project = ProjectManager(project_dir)

        # Budget
        if project.has_state():
            state = project.load_state()
            project_config = load_project_config(project_dir)
            budget_cap = project_config.budget or global_config.default_budget
            remaining = budget_cap - state.total_cost_usd
            checks.append(("OK", "Budget", f"${remaining:.2f} remaining of ${budget_cap:.2f}"))

            # State
            checks.append(("OK", "State", f"{state.status}, phase: {state.phase}"))
        else:
            checks.append(("INFO", "State", "no active run"))

        # Daemon / FIFO health
        health = check_daemon_health(project_dir)
        if health["alive"]:
            checks.append(("OK", "Daemon", f"running (PID {health['pid']})"))
        elif health["fifo_exists"]:
            checks.append(("WARN", "Stale FIFO exists but daemon not running", "pact clean --stale"))
        else:
            checks.append(("OK", "Daemon", "not running (no stale artifacts)"))

        # Decomposition
        tree = project.load_tree()
        if tree:
            checks.append(("OK", "Decomposition", f"{len(tree.nodes)} components"))

            # Contract validation
            contracts = project.load_all_contracts()
            if contracts:
                from pact.contracts import validate_all_contracts
                test_suites = project.load_all_test_suites()
                gate = validate_all_contracts(tree, contracts, test_suites)
                if gate.passed:
                    checks.append(("OK", "All contracts validated", ""))
                else:
                    checks.append(("WARN", "Contract validation issues", gate.reason))

            # Test results
            for node in tree.nodes.values():
                if node.test_results and not node.test_results.all_passed:
                    checks.append((
                        "WARN",
                        f"Component '{node.component_id}' failed",
                        f"{node.test_results.failed}/{node.test_results.total} tests",
                    ))
        else:
            checks.append(("INFO", "Decomposition", "not yet run"))

    # Integration availability
    slack_url = os.environ.get("CF_SLACK_WEBHOOK", "") or global_config.slack_webhook
    slack_bot = os.environ.get("PACT_SLACK_BOT_TOKEN", "") or global_config.slack_bot_token
    if slack_bot:
        checks.append(("OK", "Slack", "read+write (bot token)"))
    elif slack_url:
        checks.append(("OK", "Slack", "write-only (webhook)"))
    else:
        checks.append(("INFO", "Slack", "not configured (set CF_SLACK_WEBHOOK)"))

    linear_key = os.environ.get("LINEAR_API_KEY", "") or global_config.linear_api_key
    if linear_key:
        checks.append(("OK", "Linear", "configured (read+write)"))
    else:
        checks.append(("INFO", "Linear", "not configured (set LINEAR_API_KEY)"))

    # Polling config
    poll_enabled = global_config.poll_integrations
    if project_dir:
        proj_cfg = load_project_config(project_dir)
        if proj_cfg.poll_integrations is not None:
            poll_enabled = proj_cfg.poll_integrations
    if poll_enabled:
        checks.append(("OK", "Integration polling", f"enabled (every {global_config.poll_interval}s)"))

    # Git
    if project_dir:
        from pact.events import _is_git_repo
        from pathlib import Path
        if _is_git_repo(Path(project_dir)):
            checks.append(("OK", "Git", "repo detected"))
        else:
            checks.append(("INFO", "Git", "no repo detected"))

    # Print results
    for level, label, detail in checks:
        icon = {
            "OK": "[OK] ",
            "WARN": "[WARN]",
            "FAIL": "[FAIL]",
            "INFO": "[INFO]",
        }.get(level, "[??] ")
        detail_str = f" ({detail})" if detail else ""
        print(f"{icon} {label}{detail_str}")


def cmd_clean(args: argparse.Namespace) -> None:
    """Clean up project artifacts."""
    from pathlib import Path
    import shutil
    from pact.project import ProjectManager

    project_dir = Path(args.project_dir).resolve()
    pact_dir = project_dir / ".pact"

    if not pact_dir.exists():
        print("No .pact/ directory found.")
        return

    if args.clean_all:
        project = ProjectManager(project_dir)
        project.clear_state(include_deliverables=True)
        print("Removed all run state and project artifacts.")
        return

    if args.stale:
        removed = []
        for name in ("dispatch", "daemon.pid", "shutdown"):
            path = pact_dir / name
            if path.exists():
                path.unlink()
                removed.append(name)
        if removed:
            print(f"Removed stale artifacts: {', '.join(removed)}")
        else:
            print("No stale artifacts found.")
        return

    if args.attempts:
        internal_impl_dir = pact_dir / "implementations"
        removed_count = 0
        if internal_impl_dir.exists():
            for comp_dir in internal_impl_dir.iterdir():
                if not comp_dir.is_dir():
                    continue
                attempts_dir = comp_dir / "attempts"
                if attempts_dir.exists():
                    shutil.rmtree(attempts_dir)
                    removed_count += 1
        if removed_count:
            print(f"Removed attempt artifacts from {removed_count} component(s).")
        else:
            print("No attempt artifacts found.")
        return

    # Interactive: show what would be deleted
    print("Artifacts in .pact/:")
    total_size = 0
    for item in sorted(pact_dir.rglob("*")):
        if item.is_file():
            size = item.stat().st_size
            total_size += size
            rel = item.relative_to(pact_dir)
            print(f"  {rel} ({size:,} bytes)")

    print(f"\nTotal: {total_size:,} bytes")
    print("\nUse --stale, --attempts, or --all to remove specific artifacts.")


def cmd_resume(args: argparse.Namespace) -> None:
    """Resume a failed or paused run."""
    from pact.lifecycle import compute_resume_strategy, execute_resume
    from pact.daemon import send_signal

    project = ProjectManager(args.project_dir)
    if not project.has_state():
        print("No active run found.")
        return

    state = project.load_state()

    if state.status == "active":
        print("Run is already active.")
        return

    if state.status == "completed":
        print("Run already completed.")
        return

    try:
        strategy = compute_resume_strategy(state)
    except ValueError as e:
        print(f"Cannot resume: {e}")
        return

    if args.from_phase:
        strategy.resume_phase = args.from_phase

    # Log the original failure before clearing
    original_reason = state.pause_reason
    project.append_audit("daemon_resume", f"Resuming from {state.status}: {original_reason}")

    state = execute_resume(state, strategy)
    project.save_state(state)

    print(f"Resumed from {strategy.resume_phase}")
    print(f"  Original failure: {original_reason}")
    print(f"  Completed components: {len(strategy.completed_components)}")

    # Signal daemon if running
    sent = send_signal(args.project_dir, "resumed")
    if sent:
        print("Daemon signaled to continue.")
    else:
        print(f"Start daemon with: pact daemon {args.project_dir}")


def cmd_diff(args: argparse.Namespace) -> None:
    """Show diff between competitive implementations or attempts."""
    import difflib
    from pathlib import Path

    project = ProjectManager(args.project_dir)
    component_id = args.component_id

    attempts = project.list_attempts(component_id)
    main_src = project.impl_src_dir(component_id)

    if not attempts and (not main_src.exists() or not any(main_src.iterdir())):
        print(f"No implementations found for component: {component_id}")
        return

    # Collect all available sources
    sources: list[tuple[str, Path]] = []

    if main_src.exists() and any(main_src.iterdir()):
        sources.append(("current", main_src))

    for attempt in attempts:
        attempt_src = Path(attempt["path"]) / "src"
        if attempt_src.exists() and any(attempt_src.iterdir()):
            label = attempt["attempt_id"]
            attempt_type = attempt.get("type", "competitive")
            sources.append((f"{label} ({attempt_type})", attempt_src))

    if len(sources) < 2:
        if len(sources) == 1:
            print(f"Only one implementation found: {sources[0][0]}")
            src_dir = sources[0][1]
            for f in sorted(src_dir.rglob("*")):
                if f.is_file():
                    print(f"  {f.relative_to(src_dir)}")
        else:
            print("No implementation sources found.")
        return

    print(f"Available implementations for {component_id}:")
    for i, (label, _) in enumerate(sources):
        print(f"  [{i}] {label}")

    # Diff first two by default
    label_a, src_a = sources[0]
    label_b, src_b = sources[1]

    print(f"\nDiff: {label_a} vs {label_b}")
    print("=" * 60)

    # Collect files from both
    files_a = {f.relative_to(src_a): f for f in src_a.rglob("*") if f.is_file()}
    files_b = {f.relative_to(src_b): f for f in src_b.rglob("*") if f.is_file()}
    all_files = sorted(set(files_a.keys()) | set(files_b.keys()))

    for rel_path in all_files:
        fa = files_a.get(rel_path)
        fb = files_b.get(rel_path)

        if fa and not fb:
            print(f"\n--- {rel_path} (only in {label_a})")
            continue
        if fb and not fa:
            print(f"\n+++ {rel_path} (only in {label_b})")
            continue

        try:
            lines_a = fa.read_text().splitlines(keepends=True)
            lines_b = fb.read_text().splitlines(keepends=True)
        except (UnicodeDecodeError, OSError):
            print(f"\n[binary] {rel_path}")
            continue

        diff = list(difflib.unified_diff(
            lines_a, lines_b,
            fromfile=f"{label_a}/{rel_path}",
            tofile=f"{label_b}/{rel_path}",
        ))
        if diff:
            print()
            for line in diff:
                print(line, end="")


def cmd_tasks(args: argparse.Namespace) -> None:
    """Generate/display task list."""
    from pact.schemas_tasks import TaskPhase
    from pact.task_list import generate_task_list, render_task_list_markdown

    project = ProjectManager(args.project_dir)

    # Handle --complete
    if args.complete:
        task_list = project.load_task_list()
        if not task_list:
            print("No task list found. Run 'pact tasks' first to generate one.")
            return
        if task_list.mark_complete(args.complete):
            project.save_task_list(task_list)
            print(f"Marked {args.complete} as completed.")
        else:
            print(f"Task not found: {args.complete}")
        return

    # Load or generate task list
    task_list = project.load_task_list()
    if task_list is None or args.regenerate:
        tree = project.load_tree()
        if not tree:
            print("No decomposition tree found. Run decomposition first.")
            return
        contracts = project.load_all_contracts()
        test_suites = project.load_all_test_suites()
        task_list = generate_task_list(tree, contracts, test_suites, project.project_dir.name)
        project.save_task_list(task_list)
        project.append_audit("tasks_generated", f"{task_list.total} tasks")

    # Filter
    if args.phase:
        try:
            phase = TaskPhase(args.phase)
        except ValueError:
            print(f"Unknown phase: {args.phase}")
            print(f"Valid phases: {', '.join(p.value for p in TaskPhase)}")
            return
        tasks = task_list.tasks_for_phase(phase)
    elif args.component:
        tasks = task_list.tasks_for_component(args.component)
    else:
        tasks = task_list.tasks

    if getattr(args, "json_output", False):
        print(json.dumps([t.model_dump() for t in tasks], indent=2, default=str))
        return

    # Render filtered or full
    if args.phase or args.component:
        for t in tasks:
            checkbox = "[x]" if t.status == "completed" else "[ ]"
            parallel = " [P]" if t.parallel else ""
            comp = f" [{t.component_id}]" if t.component_id else ""
            print(f"  {checkbox} {t.id}{parallel}{comp} {t.description}")
        print(f"\n{len(tasks)} task(s)")
    else:
        print(render_task_list_markdown(task_list))


def cmd_analyze(args: argparse.Namespace) -> None:
    """Run cross-artifact analysis."""
    from pact.analyzer import analyze_project, render_analysis_markdown

    project = ProjectManager(args.project_dir)
    tree = project.load_tree()
    if not tree:
        print("No decomposition tree found. Run decomposition first.")
        return

    contracts = project.load_all_contracts()
    test_suites = project.load_all_test_suites()

    report = analyze_project(tree, contracts, test_suites)
    project.save_analysis(report)
    project.append_audit("analysis", report.summary)

    if getattr(args, "json_output", False):
        print(report.model_dump_json(indent=2))
        return

    print(render_analysis_markdown(report))


def cmd_checklist(args: argparse.Namespace) -> None:
    """Generate requirements checklist."""
    from pact.checklist_gen import generate_checklist, render_checklist_markdown

    project = ProjectManager(args.project_dir)
    tree = project.load_tree()
    if not tree:
        print("No decomposition tree found. Run decomposition first.")
        return

    contracts = project.load_all_contracts()
    test_suites = project.load_all_test_suites()

    checklist = generate_checklist(tree, contracts, test_suites, project.project_dir.name)
    project.save_checklist(checklist)
    project.append_audit("checklist", f"{len(checklist.items)} items")

    if getattr(args, "json_output", False):
        print(checklist.model_dump_json(indent=2))
        return

    print(render_checklist_markdown(checklist))


def cmd_directive(args: argparse.Namespace) -> None:
    """Send a structured directive to the running daemon."""
    from pact.daemon import check_daemon_health, send_signal

    health = check_daemon_health(args.project_dir)
    if not health["alive"]:
        print("No daemon running. Start with: pact daemon <project-dir>")
        return

    raw = args.json_or_command.strip()
    if raw.startswith("{"):
        try:
            directive = json.loads(raw)
            if "type" not in directive:
                print("Error: JSON directive must include a 'type' field.")
                return
            sent = send_signal(args.project_dir, directive=directive)
        except json.JSONDecodeError as e:
            print(f"Invalid JSON: {e}")
            return
    else:
        # Simple string command (backward compatible)
        sent = send_signal(args.project_dir, message=raw)

    if sent:
        print(f"Directive sent: {raw}")
    else:
        print("Failed to send directive (FIFO not found or daemon not listening)")


def cmd_export_tasks(args: argparse.Namespace) -> None:
    """Export TASKS.md."""
    from pact.task_list import render_task_list_markdown

    project = ProjectManager(args.project_dir)
    task_list = project.load_task_list()
    if not task_list:
        print("No task list found. Run 'pact tasks' first to generate one.")
        return

    md = render_task_list_markdown(task_list)
    project.tasks_md_path.write_text(md)
    print(f"Exported: {project.tasks_md_path}")


async def cmd_audit(args: argparse.Namespace) -> None:
    """Run spec-compliance audit comparing task.md against implementations."""
    from pact.agents.base import AgentBase
    from pact.auditor import audit_spec_compliance, render_audit_markdown
    from pact.budget import BudgetTracker

    project = ProjectManager(args.project_dir)

    if not project.task_path.exists():
        print("No task.md found. Nothing to audit.")
        return

    tree = project.load_tree()
    if not tree:
        print("No decomposition tree found. Run decomposition first.")
        return

    config = project.load_config()
    budget = BudgetTracker(max_usd=config.budget)
    agent = AgentBase(budget)

    try:
        result = await audit_spec_compliance(agent, project)
    finally:
        await agent.close()

    if getattr(args, "json_output", False):
        print(result.model_dump_json(indent=2))
        return

    print(render_audit_markdown(result))


async def cmd_test_gen(args: argparse.Namespace) -> None:
    """Generate tests + security audit for any codebase."""
    import sys
    from pathlib import Path

    project_dir = Path(args.project_dir).resolve()
    if not project_dir.is_dir():
        print(f"Error: '{args.project_dir}' is not a directory", file=sys.stderr)
        sys.exit(1)

    from pact.test_gen import render_summary, run_test_gen

    result = await run_test_gen(
        project_path=project_dir,
        language=args.language,
        budget=args.budget,
        model=args.model,
        backend=args.backend,
        complexity_threshold=args.complexity_threshold,
        skip_covered=not args.include_covered,
        dry_run=args.dry_run,
    )

    if args.json_output:
        print(result.model_dump_json(indent=2))
    else:
        print(render_summary(result))


async def cmd_adopt(args: argparse.Namespace) -> None:
    """Adopt an existing codebase under pact governance."""
    import sys
    from pathlib import Path

    project_dir = Path(args.project_dir).resolve()
    if not project_dir.is_dir():
        print(f"Error: '{args.project_dir}' is not a directory", file=sys.stderr)
        sys.exit(1)

    from pact.adopt import adopt_codebase

    result = await adopt_codebase(
        project_path=project_dir,
        language=args.language,
        budget=args.budget,
        model=args.model,
        backend=args.backend,
        complexity_threshold=args.complexity_threshold,
        dry_run=args.dry_run,
        include=args.include,
        exclude=args.exclude,
    )

    print(result.summary())


def cmd_handoff(args: argparse.Namespace) -> None:
    """Render and optionally validate the handoff brief for a component."""
    import json as json_mod

    from pact.interface_stub import context_fence, render_handoff_brief
    from pact.project import ProjectManager

    project = ProjectManager(args.project_dir)
    tree = project.load_tree()
    if not tree:
        print("No decomposition tree found. Run decomposition first.")
        return

    cid = args.component_id
    contracts = project.load_all_contracts()
    if cid not in contracts:
        print(f"No contract found for component '{cid}'.")
        print(f"Available: {', '.join(sorted(contracts.keys()))}")
        return

    contract = contracts[cid]
    test_suites = project.load_all_test_suites()
    test_suite = test_suites.get(cid)

    # Load optional context
    sops = ""
    sops_path = project.project_dir / "sops.md"
    if sops_path.exists():
        sops = sops_path.read_text()

    # Load learnings
    learnings = ""
    learnings_path = project._learnings_dir / "learnings.jsonl"
    if learnings_path.exists():
        entries = []
        for line in learnings_path.read_text().splitlines():
            if line.strip():
                try:
                    entry = json_mod.loads(line)
                    entries.append(entry.get("lesson", str(entry)))
                except json_mod.JSONDecodeError:
                    pass
        if entries:
            learnings = "Previous agents noted: " + "; ".join(entries[:5])

    # Load standards
    standards_brief = ""
    standards_path = project.standards_path
    if standards_path.exists():
        try:
            from pact.standards import render_standards_brief
            standards_data = json_mod.loads(standards_path.read_text())
            from pact.schemas import GlobalStandards
            standards = GlobalStandards(**standards_data)
            standards_brief = render_standards_brief(standards)
        except Exception:
            pass

    brief = render_handoff_brief(
        component_id=cid,
        contract=contract,
        contracts=contracts,
        test_suite=test_suite,
        sops=sops,
        learnings=learnings,
        standards_brief=standards_brief,
        max_context_tokens=args.max_tokens,
    )

    if args.validate:
        # Structural validation of the handoff
        issues: list[dict] = []

        # Check context fence present
        if "starting fresh" not in brief[:200]:
            issues.append({
                "level": "error",
                "check": "context_fence",
                "message": "Missing context fence (reset instruction) in first 200 chars",
            })

        # Check interface stub present
        if "interface contract" not in brief.lower():
            issues.append({
                "level": "error",
                "check": "domain_primer",
                "message": "Missing interface contract (domain primer)",
            })

        # Check stub appears before tests (ordering)
        stub_pos = brief.lower().find("interface contract")
        test_pos = brief.lower().find("tests")
        if stub_pos > 0 and test_pos > 0 and stub_pos > test_pos:
            issues.append({
                "level": "warning",
                "check": "primer_ordering",
                "message": "Interface stub appears after tests — should lead",
            })

        # Check for rigid headers (anti-pattern from Paper XX)
        rigid_headers = ["## YOUR ", "## TASK:", "## REQUIREMENTS:", "## CONSTRAINTS:"]
        for header in rigid_headers:
            if header in brief:
                issues.append({
                    "level": "warning",
                    "check": "natural_format",
                    "message": f"Rigid header '{header}' found — conversational format preferred",
                })

        # Estimate token count
        token_est = len(brief) // 4
        if token_est > 2000:
            issues.append({
                "level": "info",
                "check": "token_budget",
                "message": f"Handoff is ~{token_est} tokens. Consider --max-tokens for compression.",
            })

        # Check dependency coverage
        for dep_id in contract.dependencies:
            if dep_id not in contracts:
                issues.append({
                    "level": "warning",
                    "check": "dependency_coverage",
                    "message": f"Dependency '{dep_id}' has no contract — handoff may be incomplete",
                })

        if args.json_output:
            print(json_mod.dumps({
                "component_id": cid,
                "token_estimate": token_est,
                "issues": issues,
                "valid": not any(i["level"] == "error" for i in issues),
            }, indent=2))
        else:
            print(f"Handoff validation for '{cid}':")
            print(f"  Estimated tokens: ~{token_est}")
            if not issues:
                print("  All checks passed.")
            else:
                for issue in issues:
                    marker = {"error": "ERROR", "warning": "WARN", "info": "INFO"}[issue["level"]]
                    print(f"  [{marker}] {issue['check']}: {issue['message']}")
            print()
            print("--- Rendered handoff brief ---")
            print(brief)
    else:
        print(brief)


def cmd_health(args: argparse.Namespace) -> None:
    """Check pipeline health — dysmemic pressure detection."""
    from pact.health import (
        HealthMetrics,
        check_health,
        render_health_report,
        suggest_remedies,
    )
    from pact.project import ProjectManager

    project = ProjectManager(args.project_dir)

    metrics = HealthMetrics()

    if project.has_state():
        state = project.load_state()

        # Prefer accumulated health_snapshot when available (accurate)
        if state.health_snapshot:
            metrics = HealthMetrics.from_dict(state.health_snapshot)
        else:
            # Fall back to component-task reconstruction for old state files
            metrics.budget_cap = state.total_cost_usd or 10.0
            metrics.total_spend = state.total_cost_usd

            for ct in state.component_tasks:
                if ct.status == "failed":
                    metrics.record_component_failure(ct.component_id)
                    metrics.record_attempt(success=False)
                elif ct.status == "completed":
                    metrics.record_attempt(success=True)
                    metrics.contracts_produced += 1
                    metrics.tests_produced += 1
                    metrics.implementations_produced += 1

            # Parse audit trail for token-level metrics
            audit = project.load_audit()
            for entry in audit:
                action = entry.get("action", "")
                if "research" in action or "plan" in action or "interview" in action:
                    metrics.planning_calls += 1
                elif "contract" in action or "test" in action or "implement" in action or "build" in action:
                    metrics.generation_calls += 1

    report = check_health(metrics)
    print(render_health_report(report))

    # Show token breakdown if data is available
    if metrics.planning_tokens > 0 or metrics.generation_tokens > 0:
        total = metrics.planning_tokens + metrics.generation_tokens
        plan_pct = (metrics.planning_tokens / total * 100) if total else 0
        gen_pct = (metrics.generation_tokens / total * 100) if total else 0
        print(f"\nToken breakdown:")
        print(f"  Planning:   {metrics.planning_tokens:>10,} ({plan_pct:.0f}%)")
        print(f"  Generation: {metrics.generation_tokens:>10,} ({gen_pct:.0f}%)")
        print(f"  Ratio:      {metrics.output_planning_ratio:.2f}x gen/plan")

    # Show per-phase token table
    if metrics.phase_tokens:
        print(f"\nPer-phase tokens:")
        for phase, pt in sorted(metrics.phase_tokens.items()):
            print(f"  {phase:12s}  in={pt.input_tokens:>8,}  out={pt.output_tokens:>8,}  calls={pt.calls}")

    # Show cascade event count
    if metrics.cascade_events > 0:
        print(f"\nCascade events: {metrics.cascade_events}")

    # Show suggested remedies
    remedies = suggest_remedies(report, metrics)
    if remedies:
        auto = [r for r in remedies if r.auto]
        proposed = [r for r in remedies if not r.auto]
        if auto:
            print(f"\nAuto-applied remedies:")
            for r in auto:
                print(f"  [{r.kind}] {r.description}")
        if proposed:
            print(f"\nProposed remedies (apply via FIFO):")
            for r in proposed:
                print(f"  [{r.kind}] {r.description}")
                if r.fifo_hint:
                    print(f"    pact signal {args.project_dir} --directive '{r.fifo_hint}'")

def cmd_ci(args: argparse.Namespace) -> None:
    """Generate a GitHub Actions CI workflow for a pact-managed project."""
    from pact.ci import generate_ci_workflow

    generate_ci_workflow(args.project_dir, output_path=args.output)


def cmd_deploy(args: argparse.Namespace) -> None:
    """Generate a baton.yaml topology config for a pact-managed project."""
    from pact.deploy import generate_baton_yaml

    generate_baton_yaml(
        args.project_dir,
        output_path=args.output,
        sink=args.sink,
        error_rate_threshold=args.error_rate,
        p95_ms_threshold=args.p95_ms,
    )


def cmd_audit_init(args: argparse.Namespace) -> None:
    """Initialize audit repo separation for a project."""
    import shutil

    import yaml

    audit_dir = Path(args.audit_dir).resolve()
    project_dir = Path(args.project_dir).resolve()

    project = ProjectManager(project_dir, audit_dir=audit_dir)
    project.init()

    # Migrate existing contracts/tests/decomposition to audit dir
    for subdir in ("contracts", "tests", "decomposition"):
        src = project_dir / subdir
        dst = audit_dir / subdir
        if src.exists() and src != dst:
            if dst.exists():
                print(f"  {subdir}/ already exists in audit dir, skipping migration")
            else:
                shutil.copytree(src, dst)
                print(f"  Migrated {subdir}/ to audit dir")

    # Migrate analysis/checklist/standards/design.json
    for fname in ("standards.json", "analysis.json", "checklist.json", "design.json"):
        src = project_dir / fname
        dst = audit_dir / fname
        if src.exists() and src != dst and not dst.exists():
            shutil.copy2(src, dst)
            print(f"  Migrated {fname} to audit dir")

    # Update pact.yaml with audit config
    config_path = project_dir / "pact.yaml"
    if config_path.exists():
        with open(config_path) as f:
            raw = yaml.safe_load(f) or {}
    else:
        raw = {}

    raw["audit_dir"] = str(audit_dir)
    if args.audit_repo:
        raw["audit_repo"] = args.audit_repo

    with open(config_path, "w") as f:
        yaml.dump(raw, f, default_flow_style=False, sort_keys=False)

    # Run initial sync
    from pact.sync import sync_visible_tests

    results = sync_visible_tests(project)

    print(f"\nAudit repo initialized at: {audit_dir}")
    print(f"Synced {len(results)} component test suites to code repo")
    print("\nNext steps:")
    print("  1. Initialize git in the audit dir (if not already a repo)")
    print("  2. Set audit_mode in pact.yaml:")
    print('     - "audit" for the auditing agent')
    print('     - "code" for the coding agent')


async def cmd_sync(args: argparse.Namespace) -> None:
    """Sync visible tests from audit repo to code repo."""
    from pact.config import load_project_config
    from pact.sync import clone_or_pull_audit_repo, sync_visible_tests

    project_dir = Path(args.project_dir).resolve()
    config = load_project_config(project_dir)

    audit_dir = None
    if config.audit_dir:
        audit_dir = Path(config.audit_dir).resolve()
    elif config.audit_repo:
        audit_dir = await clone_or_pull_audit_repo(project_dir, config.audit_repo)
    else:
        print("No audit_dir or audit_repo configured in pact.yaml")
        return

    project = ProjectManager(project_dir, audit_dir=audit_dir)
    results = sync_visible_tests(project)

    if not results:
        print("No tests to sync")
        return

    for cid, status in sorted(results.items()):
        print(f"  {cid}: {status}")
    print(f"\nSynced {sum(1 for s in results.values() if s == 'synced')} component(s)")


async def cmd_certify(args: argparse.Namespace) -> None:
    """Run certification or verify existing."""
    from pact.config import load_project_config

    project_dir = Path(args.project_dir).resolve()
    config = load_project_config(project_dir)

    audit_dir = None
    if config.audit_dir:
        audit_dir = Path(config.audit_dir).resolve()
    elif config.audit_repo:
        from pact.sync import clone_or_pull_audit_repo
        audit_dir = await clone_or_pull_audit_repo(project_dir, config.audit_repo)

    project = ProjectManager(project_dir, audit_dir=audit_dir)

    if getattr(args, "verify_only", False):
        from pact.certification import verify_artifact_hashes, verify_certification

        cert = project.load_certification()
        if cert is None:
            print("No certification found")
            return

        valid, issues = verify_certification(cert)
        if not valid:
            print("Certification INVALID:")
            for issue in issues:
                print(f"  - {issue}")
            return

        mismatches = verify_artifact_hashes(cert, project)
        if mismatches:
            print("Artifact hash mismatches:")
            for m in mismatches:
                print(f"  - {m}")
            return

        print(f"Certification VALID (verdict: {cert.verdict})")
        print(f"  Timestamp: {cert.timestamp}")
        print(f"  Components: {len(cert.components)}")
        print(f"  Summary: {cert.summary}")
        return

    # Full certification run
    from pact.certification import certify

    print("Running certification...")
    cert = await certify(project)

    # Save
    path = project.save_certification(cert)

    if getattr(args, "json_output", False):
        print(cert.model_dump_json(indent=2))
    else:
        icon = "PASS" if cert.verdict == "pass" else "PARTIAL" if cert.verdict == "partial" else "FAIL"
        print(f"\nCertification: {icon}")
        print(f"  Verdict: {cert.verdict}")
        print(f"  Components: {len(cert.components)}")
        print(f"  Summary: {cert.summary}")

        if cert.visible_results:
            total_v = sum(r.get("passed", 0) for r in cert.visible_results.values())
            total_vt = sum(r.get("total", 0) for r in cert.visible_results.values())
            print(f"  Visible tests: {total_v}/{total_vt} passed")

        if cert.goodhart_results:
            total_g = sum(r.get("passed", 0) for r in cert.goodhart_results.values())
            total_gt = sum(r.get("total", 0) for r in cert.goodhart_results.values())
            print(f"  Goodhart tests: {total_g}/{total_gt} passed")

        print(f"  Saved to: {path}")


def cmd_sentinel(args: argparse.Namespace) -> None:
    """Dispatch sentinel subcommands."""
    sub = getattr(args, "sentinel_command", None)
    if not sub:
        print("Usage: pact sentinel {status|push-contract|list-keys}")
        return

    if sub == "status":
        cmd_sentinel_status(args)
    elif sub == "push-contract":
        cmd_sentinel_push_contract(args)
    elif sub == "list-keys":
        cmd_sentinel_list_keys(args)


def cmd_sentinel_status(args: argparse.Namespace) -> None:
    """Show Sentinel/Arbiter connection configuration."""
    config = load_project_config(args.project_dir)
    from pact.arbiter import resolve_arbiter_endpoint

    endpoint = resolve_arbiter_endpoint(config.arbiter_endpoint)
    print(f"Arbiter endpoint: {endpoint or '(not configured)'}")
    print(f"Skip arbiter: {config.skip_arbiter}")
    print(f"Constrain dir: {config.constrain_dir or '(not configured)'}")
    print(f"Ledger dir: {config.ledger_dir or '(not configured)'}")


def cmd_sentinel_push_contract(args: argparse.Namespace) -> None:
    """Accept a tightened contract from Sentinel and trigger rebuild."""
    from pact.schemas import ComponentContract

    contract_path = Path(args.contract_file)
    if not contract_path.exists():
        print(f"Contract file not found: {contract_path}")
        return

    contract = ComponentContract.model_validate_json(contract_path.read_text())
    project = ProjectManager(args.project_dir)

    # Save the tightened contract
    project.save_contract(contract)
    print(f"Contract updated for {args.component_id}")
    print(f"  Version: {contract.version}")
    print(f"  Functions: {len(contract.functions)}")
    print(f"\nTo rebuild this component: pact build {args.project_dir} {args.component_id}")


def cmd_sentinel_list_keys(args: argparse.Namespace) -> None:
    """List all PACT keys in a project."""
    from pact.interface_stub import project_id_hash

    project = ProjectManager(args.project_dir)
    contracts = project.load_all_contracts()

    if not contracts:
        print("No contracts found.")
        return

    for cid, contract in sorted(contracts.items()):
        pid = project_id_hash(cid)
        print(f"  PACT:{pid}:{cid}")
        for func in contract.functions:
            print(f"    PACT:{cid}:{func.name}")


if __name__ == "__main__":
    main()
