"""CLI entry points for pact.

Commands:
  pact init <project-dir>       Scaffold a new project
  pact status <project-dir>     Show current state
  pact run <project-dir>        Run the pipeline (single burst or poll loop)
  pact daemon <project-dir>     Run event-driven daemon (FIFO-based, zero-delay)
  pact signal <project-dir>     Send signal to daemon (resume, approve, etc.)
  pact interview <project-dir>  Run interview phase only
  pact answer <project-dir>     Answer interview questions
  pact approve <project-dir>    Approve interview + signal daemon to continue
  pact validate <project-dir>   Re-run contract validation gate
  pact design <project-dir>     Regenerate design.md
  pact components <project-dir> List all components with status
  pact build <project-dir> <id> Rebuild a specific component
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
    p_status = subparsers.add_parser("status", help="Show project status")
    p_status.add_argument("project_dir", help="Project directory path")

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


def cmd_init(args: argparse.Namespace) -> None:
    """Initialize a new project."""
    project = ProjectManager(args.project_dir)
    project.init(budget=args.budget)
    print(f"Initialized project: {project.project_dir}")
    print(f"  Edit {project.task_path} to describe your task")
    print(f"  Edit {project.sops_path} to set operating procedures")
    print(f"  Then run: pact daemon {args.project_dir}")


def cmd_status(args: argparse.Namespace) -> None:
    """Show project status."""
    from pact.daemon import check_daemon_health

    project = ProjectManager(args.project_dir)

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

    scheduler = Scheduler(project, global_config, project_config, budget)
    daemon = Daemon(
        project, scheduler,
        health_check_interval=args.health_interval,
        max_idle=args.max_idle,
    )

    print(f"Daemon starting for: {project.project_dir}")
    print(f"  FIFO: {daemon.fifo_path}")
    print(f"  Health check: every {args.health_interval}s")
    print(f"  Max idle: {args.max_idle}s")
    print(f"  Resume with: pact signal {args.project_dir}")
    print()

    state = await daemon.run()
    print()
    print(format_run_summary(state))


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
        for q in interview.questions:
            if q not in interview.user_answers:
                matching = next(
                    (a for a in interview.assumptions
                     if any(word in a.lower() for word in q.lower().split()[:3])),
                    interview.assumptions[0] if interview.assumptions else "Accepted",
                )
                interview.user_answers[q] = matching
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


if __name__ == "__main__":
    main()
