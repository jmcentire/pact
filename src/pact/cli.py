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

    # watch (monitoring)
    p_watch = subparsers.add_parser("watch", help="Start the Sentinel production monitor")
    p_watch.add_argument("project_dirs", nargs="+", help="Project directories to monitor")

    # report (manual error report)
    p_report = subparsers.add_parser("report", help="Manually report a production error")
    p_report.add_argument("project_dir", help="Project directory path")
    p_report.add_argument("error_text", help="Error description text")

    # incidents (list)
    p_incidents = subparsers.add_parser("incidents", help="List active/recent incidents")
    p_incidents.add_argument("project_dir", help="Project directory path")
    p_incidents.add_argument("--all", action="store_true", dest="show_all", help="Include resolved/escalated")
    p_incidents.add_argument("--json", action="store_true", dest="json_output", help="Output as JSON")

    # incident (detail)
    p_incident = subparsers.add_parser("incident", help="Show incident details + diagnostic report")
    p_incident.add_argument("project_dir", help="Project directory path")
    p_incident.add_argument("incident_id", help="Incident ID")

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

    # export-tasks
    p_export = subparsers.add_parser("export-tasks", help="Export TASKS.md")
    p_export.add_argument("project_dir", help="Project directory path")

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
    elif args.command == "watch":
        asyncio.run(cmd_watch(args))
    elif args.command == "report":
        asyncio.run(cmd_report(args))
    elif args.command == "incidents":
        cmd_incidents(args)
    elif args.command == "incident":
        cmd_incident(args)
    elif args.command == "tasks":
        cmd_tasks(args)
    elif args.command == "analyze":
        cmd_analyze(args)
    elif args.command == "checklist":
        cmd_checklist(args)
    elif args.command == "export-tasks":
        cmd_export_tasks(args)


def cmd_init(args: argparse.Namespace) -> None:
    """Initialize a new project."""
    project = ProjectManager(args.project_dir)
    project.init(budget=args.budget)
    print(f"Initialized project: {project.project_dir}")
    print(f"  Edit {project.task_path} to describe your task")
    print(f"  Edit {project.sops_path} to set operating procedures")
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
    impl_src = project._impl_dir / component_id / "src"
    if impl_src.exists() and any(impl_src.iterdir()):
        files = list(impl_src.rglob("*"))
        source_files = [f for f in files if f.is_file()]
        print(f"\nImplementation: {len(source_files)} file(s)")
        for f in source_files[:10]:
            print(f"  {f.relative_to(impl_src)}")
        if len(source_files) > 10:
            print(f"  ... and {len(source_files) - 10} more")


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

    daemon = Daemon(
        project, scheduler,
        health_check_interval=args.health_interval,
        max_idle=args.max_idle,
        event_bus=scheduler.event_bus,
        poll_integrations=poll_integrations,
        poll_interval=poll_interval,
        max_poll_attempts=max_poll_attempts,
    )

    print(f"Daemon starting for: {project.project_dir}")
    print(f"  FIFO: {daemon.fifo_path}")
    print(f"  Health check: every {args.health_interval}s")
    print(f"  Max idle: {args.max_idle}s")
    if poll_integrations:
        print(f"  Integration polling: every {poll_interval}s (max {max_poll_attempts} attempts)")
    print(f"  Resume with: pact signal {args.project_dir}")
    print()

    state = await daemon.run()
    print()
    print(format_run_summary(state))


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
        # Accept all assumptions as answers
        for i, q in enumerate(interview.questions):
            if q not in interview.user_answers:
                answer, confidence = match_answer_to_question(
                    q, interview.assumptions, question_index=i,
                )
                interview.user_answers[q] = answer
        interview.approved = True
        project.save_interview(interview)
        print("Interview approved with default assumptions.")

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

    project_dir = Path(args.project_dir).resolve()
    pact_dir = project_dir / ".pact"

    if not pact_dir.exists():
        print("No .pact/ directory found.")
        return

    if args.clean_all:
        # Remove all .pact/ state (keeps task.md, sops.md, pact.yaml)
        removed = []
        for item in pact_dir.iterdir():
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
            removed.append(item.name)
        print(f"Removed all .pact/ contents: {', '.join(removed)}")
        # Recreate empty subdirs
        (pact_dir / "decomposition").mkdir(exist_ok=True)
        (pact_dir / "contracts").mkdir(exist_ok=True)
        (pact_dir / "implementations").mkdir(exist_ok=True)
        (pact_dir / "compositions").mkdir(exist_ok=True)
        (pact_dir / "learnings").mkdir(exist_ok=True)
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
        impl_dir = pact_dir / "implementations"
        removed_count = 0
        if impl_dir.exists():
            for comp_dir in impl_dir.iterdir():
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
    main_src = project._impl_dir / component_id / "src"

    if not attempts and not main_src.exists():
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


async def cmd_watch(args: argparse.Namespace) -> None:
    """Start the Sentinel production monitor."""
    from pathlib import Path
    from pact.schemas_monitoring import MonitoringTarget
    from pact.sentinel import Sentinel

    global_config = load_global_config()

    if not global_config.monitoring_enabled:
        print("Monitoring is disabled. Set monitoring_enabled: true in config.yaml")
        return

    targets: list[MonitoringTarget] = []
    for project_dir in args.project_dirs:
        project_config = load_project_config(project_dir)
        target = MonitoringTarget(
            project_dir=str(Path(project_dir).resolve()),
            label=Path(project_dir).name,
            log_files=project_config.monitoring_log_files,
            process_patterns=project_config.monitoring_process_patterns,
            webhook_port=project_config.monitoring_webhook_port,
            error_patterns=project_config.monitoring_error_patterns,
        )
        targets.append(target)

    if not targets:
        print("No projects to watch.")
        return

    # Use the first project's .pact dir for state
    state_dir = Path(args.project_dirs[0]).resolve() / ".pact"
    state_dir.mkdir(parents=True, exist_ok=True)

    sentinel = Sentinel(
        config=global_config,
        targets=targets,
        state_dir=state_dir,
    )

    print(f"Sentinel starting: watching {len(targets)} project(s)")
    for t in targets:
        print(f"  {t.label or t.project_dir}")
        if t.log_files:
            print(f"    Log files: {', '.join(t.log_files)}")
        if t.process_patterns:
            print(f"    Processes: {', '.join(t.process_patterns)}")
        if t.webhook_port:
            print(f"    Webhook port: {t.webhook_port}")
    print(f"\nAuto-remediate: {global_config.monitoring_auto_remediate}")
    print("Press Ctrl+C to stop.\n")

    # Write PID file
    pid_path = state_dir / "sentinel.pid"
    import os
    pid_path.write_text(str(os.getpid()))

    try:
        await sentinel.run()
    except KeyboardInterrupt:
        sentinel.stop()
    finally:
        if pid_path.exists():
            pid_path.unlink()


async def cmd_report(args: argparse.Namespace) -> None:
    """Manually report a production error."""
    from pathlib import Path
    from pact.incidents import IncidentManager
    from pact.schemas_monitoring import MonitoringBudget, Signal

    project_dir = str(Path(args.project_dir).resolve())
    state_dir = Path(args.project_dir).resolve() / ".pact"
    state_dir.mkdir(parents=True, exist_ok=True)

    from datetime import datetime
    budget = MonitoringBudget()
    mgr = IncidentManager(state_dir, budget)

    signal = Signal(
        source="manual",
        raw_text=args.error_text,
        timestamp=datetime.now().isoformat(),
    )
    incident = mgr.create_incident(signal, project_dir)
    print(f"Incident created: {incident.id}")
    print(f"  Status: {incident.status}")
    print(f"  Project: {project_dir}")
    print(f"  Error: {args.error_text[:100]}")


def cmd_incidents(args: argparse.Namespace) -> None:
    """List active/recent incidents."""
    from pathlib import Path
    from pact.incidents import IncidentManager
    from pact.schemas_monitoring import MonitoringBudget

    state_dir = Path(args.project_dir).resolve() / ".pact"
    if not (state_dir / "monitoring" / "incidents.json").exists():
        print("No incidents found.")
        return

    budget = MonitoringBudget()
    mgr = IncidentManager(state_dir, budget)

    if getattr(args, "show_all", False):
        incidents = mgr.get_recent_incidents(50)
    else:
        incidents = mgr.get_active_incidents()

    if not incidents:
        print("No active incidents.")
        return

    if getattr(args, "json_output", False):
        print(json.dumps([i.model_dump() for i in incidents], indent=2, default=str))
        return

    print(f"{'ID':<14s} {'Status':<14s} {'Component':<20s} {'Spend':<10s} {'Error'}")
    print("-" * 80)
    for inc in incidents:
        error_preview = ""
        if inc.signals:
            error_preview = inc.signals[0].raw_text[:30]
        print(
            f"{inc.id:<14s} {inc.status:<14s} "
            f"{inc.component_id or 'unknown':<20s} "
            f"${inc.spend_usd:<9.2f} {error_preview}"
        )


def cmd_incident(args: argparse.Namespace) -> None:
    """Show incident details and diagnostic report."""
    from pathlib import Path
    from pact.incidents import IncidentManager
    from pact.schemas_monitoring import MonitoringBudget

    state_dir = Path(args.project_dir).resolve() / ".pact"
    if not (state_dir / "monitoring" / "incidents.json").exists():
        print("No incidents found.")
        return

    budget = MonitoringBudget()
    mgr = IncidentManager(state_dir, budget)

    incident = mgr.get_incident(args.incident_id)
    if not incident:
        print(f"Incident not found: {args.incident_id}")
        return

    print(f"Incident: {incident.id}")
    print(f"  Status: {incident.status}")
    print(f"  Component: {incident.component_id or 'unknown'}")
    print(f"  Project: {incident.project_dir}")
    print(f"  Created: {incident.created_at}")
    print(f"  Updated: {incident.updated_at}")
    print(f"  Spend: ${incident.spend_usd:.2f}")
    print(f"  Remediation attempts: {incident.remediation_attempts}")
    print(f"  Resolution: {incident.resolution or 'pending'}")

    if incident.signals:
        print(f"\nSignals ({len(incident.signals)}):")
        for s in incident.signals[:10]:
            print(f"  [{s.source}] {s.raw_text[:100]}")

    if incident.diagnostic_report:
        print(f"\n{'=' * 60}")
        print("DIAGNOSTIC REPORT")
        print(f"{'=' * 60}")
        print(incident.diagnostic_report)

    # Check for report file
    report_path = state_dir / "monitoring" / "reports" / f"{incident.id}.md"
    if report_path.exists() and not incident.diagnostic_report:
        print(f"\n{'=' * 60}")
        print("DIAGNOSTIC REPORT (from file)")
        print(f"{'=' * 60}")
        print(report_path.read_text())


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


if __name__ == "__main__":
    main()
