"""CLI for Local Agentic RAG Test Coverage Analyzer conforming to Specification §25."""

import argparse
import sys
import os
import io
import json
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import logging
import warnings

# Suppress Hugging Face Hub unauthenticated requests and warnings
warnings.filterwarnings("ignore", message=".*unauthenticated requests to the HF Hub.*")
warnings.filterwarnings("ignore", module="huggingface_hub.*")
logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
logging.getLogger("transformers").setLevel(logging.ERROR)

from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from src.config import load_config
from src.pipeline import RAGCoveragePipeline
from src.storage.state_db import StateDatabase
from src.watcher.fs_watcher import FeatureRepositoryWatcher

console = Console(force_terminal=True, legacy_windows=False)


def print_banner():
    console.print(
        Panel.fit(
            "[bold cyan]Local Agentic RAG Test Coverage Analyzer[/bold cyan]\n"
            "[dim]Gherkin BDD Test Verification, Grounded Citations & Deterministic Aggregation[/dim]",
            border_style="cyan"
        )
    )


def handle_analyze(args):
    print_banner()
    cfg = load_config(args.config)
    pipeline = RAGCoveragePipeline(config=cfg)

    doc_path = args.document or args.docs or str(cfg.paths.business_docs_dir)
    repo_id = args.repo or "default"

    console.print(f"[bold green]Starting Coverage Analysis[/bold green]")
    console.print(f"[*] Target Repository: [bold cyan]{repo_id}[/bold cyan]")
    console.print(f"[*] Business Document: [cyan]{doc_path}[/cyan]\n")

    report, report_files, session_id = pipeline.analyze(
        document_path=doc_path,
        repo_id=repo_id,
        report_base_name=args.report_name or "coverage_report",
        session_name=args.session_name,
    )

    table = Table(title=f"Coverage Summary (Session: {session_id} | Repo: {repo_id})", border_style="blue")
    table.add_column("Metric", style="bold")
    table.add_column("Count / Value", style="magenta")
    table.add_column("Percentage", style="green")

    table.add_row("Session ID", session_id, "-")
    table.add_row("Target Repo", repo_id, "-")
    table.add_row("Total Requirements", str(report.total_requirements), "100.0%")
    table.add_row("Scenarios in Repo", str(report.total_feature_scenarios), "-")
    table.add_row("Average Match Score", f"{report.average_match_pct:.1f}%", "-")
    table.add_row("[green]FULLY_COVERED[/green]", str(report.covered_count), f"{report.coverage_rate:.1f}%")
    table.add_row("[yellow]PARTIALLY_COVERED[/yellow]", str(report.partial_count), f"{report.partial_rate:.1f}%")
    table.add_row("[red]NOT_COVERED / GAPS[/red]", str(report.uncovered_count), f"{report.uncovered_rate:.1f}%")

    console.print(table)
    console.print(f"\n[bold]Generated Reports & Persisted Session:[/bold]")
    console.print(f"Session ID: [bold cyan]{session_id}[/bold cyan]")
    console.print(f"Markdown: [cyan]{report_files['markdown']}[/cyan]")
    console.print(f"HTML Dashboard: [cyan]{report_files['html']}[/cyan]")
    console.print(f"JSON Output: [cyan]{report_files['json']}[/cyan]")


def handle_eval(args):
    print_banner()
    cfg = load_config(args.config)
    pipeline = RAGCoveragePipeline(config=cfg)

    from eval.benchmarks.run_all_benchmarks import BenchmarkRunner
    runner = BenchmarkRunner(pipeline=pipeline, results_dir=args.output_dir)
    runner.run(
        target=args.target,
        repo_id=args.repo or "default",
        bypass_cache=args.bypass_cache,
    )


def handle_repos(args):
    print_banner()
    cfg = load_config(args.config)
    pipeline = RAGCoveragePipeline(config=cfg)
    sub = args.repo_action

    if sub == "list":
        repos = pipeline.repo_manager.list_repositories()
        if not repos:
            console.print("[yellow]No repositories registered yet. Use 'coverage-agent repo add <name> <path>'[/yellow]")
            return

        table = Table(title="Registered Automation Repositories (SQLite)", border_style="cyan")
        table.add_column("Repo ID", style="bold cyan")
        table.add_column("Repo Name", style="white")
        table.add_column("Path", style="dim")
        table.add_column("Corpus Ver", style="magenta")
        table.add_column("Scenarios", style="green")
        table.add_column("Last Indexed", style="dim")

        for r in repos:
            table.add_row(
                r["repo_id"],
                r["repo_name"],
                r["repo_path"],
                str(r.get("corpus_version", "v1.0")),
                str(r["scenario_count"]),
                str(r["last_indexed_at"] or "-")[:19],
            )
        console.print(table)

    elif sub == "add":
        repo_info = pipeline.repo_manager.add_repository(
            repo_name=args.name,
            repo_path=args.path,
            repo_id=args.repo_id,
        )
        count = pipeline.index_features(feature_dir=args.path, repo_id=repo_info["repo_id"], repo_name=args.name)
        console.print(f"[bold green]Registered repository '{args.name}' ({repo_info['repo_id']}) with {count} scenarios indexed.[/bold green]")


def handle_chat(args):
    print_banner()
    cfg = load_config(args.config)
    pipeline = RAGCoveragePipeline(config=cfg)
    repo_id = args.repo or "default"

    repo = pipeline.repo_manager.get_repository(repo_id)
    features_path = Path(repo["repo_path"]) if repo else (args.features or cfg.paths.feature_repos_dir)
    pipeline.index_features(feature_dir=features_path, repo_id=repo_id)

    console.print(f"[bold green]Interactive RAG Coverage Chatbot[/bold green] (Repo: [cyan]{repo_id}[/cyan])")
    console.print("[dim]Ask about requirement coverage or test scenarios. Type 'exit' to quit.[/dim]\n")

    chat_id = None
    while True:
        try:
            prompt = console.input("[bold yellow]You > [/bold yellow]")
            if not prompt.strip():
                continue
            if prompt.strip().lower() in ("exit", "quit", "q"):
                break

            res = pipeline.chat(message=prompt, repo_id=repo_id, chat_id=chat_id)
            chat_id = res["chat_id"]

            cache_tag = " [magenta](⚡ Semantic Cache Hit)[/magenta]" if res.get("cached") else ""
            console.print(f"\n[bold cyan]Assistant{cache_tag}:[/bold cyan]\n{res['reply']}\n")
            if res.get("citations"):
                console.print("[dim]Grounded Scenario Citations:[/dim]")
                for c in res["citations"][:3]:
                    console.print(f"  🎯 [green]{c['feature_title']}[/green] ➔ [bold white]{c['scenario_name']}[/bold white] ({Path(c['file_path']).name}:{c['line_number']})")
                console.print("")
        except (KeyboardInterrupt, EOFError):
            break


def handle_serve(args):
    print_banner()
    import uvicorn
    console.print(f"[bold green]Starting Web UI & API server on http://{args.host}:{args.port}[/bold green]")
    uvicorn.run("src.web.app:app", host=args.host, port=args.port, reload=False)


def handle_sessions(args):
    print_banner()
    cfg = load_config(args.config)
    db_path = cfg.paths.cache_dir / "rag_state.db"
    state_db = StateDatabase(db_path=db_path)

    sub = args.session_action

    if sub == "list":
        sessions = state_db.list_sessions(limit=args.limit)
        if not sessions:
            console.print("[yellow]No evaluation sessions found in SQLite.[/yellow]")
            return

        table = Table(title="Recent Evaluation Sessions (SQLite)", border_style="cyan")
        table.add_column("Session ID", style="bold cyan")
        table.add_column("Repo", style="magenta")
        table.add_column("Session Name", style="white")
        table.add_column("Status", style="magenta")
        table.add_column("Total", style="dim")
        table.add_column("Covered", style="green")
        table.add_column("Partial", style="yellow")
        table.add_column("Uncovered", style="red")
        table.add_column("Avg Match", style="blue")
        table.add_column("Started At", style="dim")

        for s in sessions:
            status_style = "green" if s["status"] == "COMPLETED" else "yellow"
            table.add_row(
                s["session_id"],
                str(s.get("repo_id", "default")),
                s["session_name"][:25],
                f"[{status_style}]{s['status']}[/{status_style}]",
                str(s["total_requirements"]),
                str(s["covered_count"]),
                str(s["partial_count"]),
                str(s["uncovered_count"]),
                f"{s['average_match_pct']:.1f}%",
                str(s["started_at"])[:19],
            )
        console.print(table)

    elif sub == "show":
        session = state_db.get_session(args.session_id)
        if not session:
            console.print(f"[red]Session '{args.session_id}' not found.[/red]")
            return

        console.print(Panel.fit(
            f"[bold]Session ID:[/bold] [cyan]{session['session_id']}[/cyan] | [bold]Repo:[/bold] [magenta]{session.get('repo_id', 'default')}[/magenta]\n"
            f"[bold]Name:[/bold] {session['session_name']}\n"
            f"[bold]Status:[/bold] {session['status']}\n"
            f"[bold]Docs Path:[/bold] {session['docs_path']}\n"
            f"[bold]Features Path:[/bold] {session['features_path']}\n"
            f"[bold]Total Reqs:[/bold] {session['total_requirements']} | "
            f"[green]Covered:[/green] {session['covered_count']} | "
            f"[yellow]Partial:[/yellow] {session['partial_count']} | "
            f"[red]Uncovered:[/red] {session['uncovered_count']} | "
            f"[blue]Avg Match:[/blue] {session['average_match_pct']:.1f}%\n"
            f"[bold]Started At:[/bold] {session['started_at']} | [bold]Completed At:[/bold] {session['completed_at']}",
            title="Session Details",
            border_style="cyan"
        ))


def handle_index(args):
    print_banner()
    cfg = load_config(args.config)
    pipeline = RAGCoveragePipeline(config=cfg)
    repo_id = args.repo or "default"
    repo = pipeline.repo_manager.get_repository(repo_id)
    features_path = Path(repo["repo_path"]) if repo else (args.features or cfg.paths.feature_repos_dir)
    count = pipeline.index_features(feature_dir=features_path, repo_id=repo_id)
    console.print(f"[bold green]Successfully indexed {count} scenarios into repository '{repo_id}'.[/bold green]")


def handle_watch(args):
    print_banner()
    cfg = load_config(args.config)
    pipeline = RAGCoveragePipeline(config=cfg)
    repo_id = args.repo or "default"
    repo = pipeline.repo_manager.get_repository(repo_id)
    features_path = Path(repo["repo_path"]) if repo else (args.features or cfg.paths.feature_repos_dir)

    debounce = args.debounce if getattr(args, "debounce", None) is not None else cfg.watcher.debounce_seconds

    watcher = FeatureRepositoryWatcher(
        watch_dir=features_path,
        bm25_index=pipeline.bm25_index,
        milvus_store=pipeline.milvus_store,
        embedding_model=pipeline.embedding_model,
        state_db=pipeline.state_db,
        repo_id=repo_id,
        debounce_seconds=debounce,
    )
    console.print(f"[bold cyan]Watching directory:[/bold cyan] {features_path.resolve()} (Repo: {repo_id} | Debounce: {debounce}s)")
    console.print("[dim]Edit, add, or delete any .feature file to trigger real-time re-indexing. Press Ctrl+C to stop.[/dim]\n")
    watcher.start(blocking=True)


def handle_query(args):
    print_banner()
    cfg = load_config(args.config)
    pipeline = RAGCoveragePipeline(config=cfg)
    repo_id = args.repo or "default"
    repo = pipeline.repo_manager.get_repository(repo_id)
    features_path = Path(repo["repo_path"]) if repo else (args.features or cfg.paths.feature_repos_dir)
    pipeline.index_features(feature_dir=features_path, repo_id=repo_id)

    query_text = args.query
    console.print(f"[bold]Querying Repo [cyan]{repo_id}[/cyan]:[/bold] [yellow]{query_text}[/yellow]\n")

    top10 = pipeline.retriever.retrieve(query_text, repo_id=repo_id)

    table = Table(title=f"Top Retrieved Scenarios in '{repo_id}' (Cross-Encoder Ranked)", border_style="cyan")
    table.add_column("Rank", style="bold")
    table.add_column("Score", style="magenta")
    table.add_column("Feature", style="green")
    table.add_column("Scenario Name", style="bold white")
    table.add_column("Location", style="dim")

    for rank, (scenario, score, meta) in enumerate(top10, start=1):
        table.add_row(
            str(rank),
            f"{score:.3f}",
                str(rank),
                f"{score:.3f}",
                scenario.feature_name,
                scenario.scenario_name,
                f"{Path(scenario.file_path).name}:{scenario.line_number}"
            )

    console.print(table)


def handle_clean(args):
    print_banner()
    cfg = load_config(args.config)
    db = StateDatabase(db_path=cfg.paths.db_path)
    if args.chat_only:
        db.clear_chat_sessions(repo_id=args.repo)
        console.print("[bold green]Successfully dropped chat sessions data from SQLite.[/bold green]")
    else:
        db.clear_all_sessions()
        console.print("[bold green]Successfully dropped all evaluation runs, requirements, chat sessions, and semantic cache from SQLite.[/bold green]")


def main():
    parser = argparse.ArgumentParser(description="coverage-agent: Local Agentic RAG Test Coverage Analyzer")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # analyze (Specification §25)
    analyze_parser = subparsers.add_parser("analyze", help="Analyze business requirement document against a repository")
    analyze_parser.add_argument("--document", "--docs", type=str, required=True, help="Path to business requirement document (.md, .txt, .pdf, .docx)")
    analyze_parser.add_argument("--repo", type=str, default="default", help="Target repository ID (configured in config.yaml)")
    analyze_parser.add_argument("--config", type=str, help="Path to config.yaml")
    analyze_parser.add_argument("--session-name", type=str, help="Optional session name")
    analyze_parser.add_argument("--report-name", type=str, default="coverage_report", help="Base report filename")

    # eval / benchmark (Decoupled Evaluation Framework)
    eval_bench_parser = subparsers.add_parser("eval", help="Run decoupled retrieval, judge, or end-to-end benchmarks")
    eval_bench_parser.add_argument("--target", choices=["retrieval", "judge", "all", "e2e"], default="all", help="Target benchmark component")
    eval_bench_parser.add_argument("--repo", type=str, default="default", help="Target repository ID to benchmark")
    eval_bench_parser.add_argument("--bypass-cache", action="store_true", default=True, help="Enforce cache bypass for live model benchmarks")
    eval_bench_parser.add_argument("--output-dir", type=str, default=None, help="Custom results output directory")
    eval_bench_parser.add_argument("--config", type=str, help="Path to config.yaml")

    # evaluate alias
    eval_parser = subparsers.add_parser("evaluate", help="Alias for 'analyze'")
    eval_parser.add_argument("--document", "--docs", type=str, required=True, help="Path to business requirement document (.md, .txt, .pdf, .docx)")
    eval_parser.add_argument("--repo", type=str, default="default", help="Target repository ID (configured in config.yaml)")
    eval_parser.add_argument("--config", type=str, help="Path to config.yaml")
    eval_parser.add_argument("--session-name", type=str, help="Optional session name")
    eval_parser.add_argument("--report-name", type=str, default="coverage_report", help="Base report filename")

    # chat
    chat_parser = subparsers.add_parser("chat", help="Start interactive terminal RAG chat scoped to a repository")
    chat_parser.add_argument("--repo", type=str, default="default", help="Target repository ID")
    chat_parser.add_argument("--chat-id", type=str, help="Resume existing SQLite chat conversation ID")
    chat_parser.add_argument("--config", type=str, help="Path to config.yaml")

    # serve (Web Dashboard UI)
    serve_parser = subparsers.add_parser("serve", help="Launch FastAPI Web Dashboard with interactive chat and scenario inspector")
    serve_parser.add_argument("--host", type=str, default="127.0.0.1", help="Host address to bind to")
    serve_parser.add_argument("--port", type=int, default=8000, help="Port to bind to")
    serve_parser.add_argument("--config", type=str, help="Path to config.yaml")

    # repos
    repo_parser = subparsers.add_parser("repo", help="Repository management commands")
    repo_sub = repo_parser.add_subparsers(dest="repo_action")
    repo_sub.add_parser("list", help="List all registered test repositories")

    # sessions
    sess_parser = subparsers.add_parser("sessions", help="List past analysis and verification sessions")
    sess_parser.add_argument("--repo", type=str, help="Filter sessions by repository ID")
    sess_parser.add_argument("--config", type=str, help="Path to config.yaml")

    # index
    index_parser = subparsers.add_parser("index", help="Index or re-index Gherkin .feature files into Milvus & BM25")
    index_parser.add_argument("--repo", type=str, default="default", help="Target repository ID")
    index_parser.add_argument("--features", type=str, help="Directory containing Gherkin .feature files")
    index_parser.add_argument("--force", action="store_true", help="Force complete rebuild of Milvus and BM25 index")
    index_parser.add_argument("--config", type=str, help="Path to config.yaml")

    # watch
    watch_parser = subparsers.add_parser("watch", help="Real-time watchdog monitor for .feature file modifications")
    watch_parser.add_argument("--repo", type=str, default="default", help="Target repository ID to watch")
    watch_parser.add_argument("--features", type=str, help="Directory containing Gherkin .feature files")
    watch_parser.add_argument("--debounce", type=float, help="Debounce delay in seconds (e.g. 300 for 5 minutes)")
    watch_parser.add_argument("--config", type=str, help="Path to config.yaml")

    # query
    query_parser = subparsers.add_parser("query", help="Query repository for top matching Gherkin scenarios")
    query_parser.add_argument("-q", "--query", type=str, required=True, help="Query text or requirement")
    query_parser.add_argument("--repo", type=str, default="default", help="Target repository ID")
    query_parser.add_argument("--features", type=str, help="Directory containing Gherkin .feature files")
    query_parser.add_argument("--config", type=str, help="Path to config.yaml")

    # clean
    clean_parser = subparsers.add_parser("clean", help="Drop sessions, chat history, and semantic cache from SQLite")
    clean_parser.add_argument("--chat-only", action="store_true", help="Only drop chat sessions and messages")
    clean_parser.add_argument("--repo", type=str, help="Scope chat deletion to a specific repo ID")
    clean_parser.add_argument("--config", type=str, help="Path to config.yaml")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command in ("analyze", "evaluate"):
        handle_analyze(args)
    elif args.command == "eval":
        handle_eval(args)
    elif args.command == "chat":
        handle_chat(args)
    elif args.command == "serve":
        handle_serve(args)
    elif args.command == "repo":
        handle_repos(args)
    elif args.command == "sessions":
        handle_sessions(args)
    elif args.command == "index":
        handle_index(args)
    elif args.command == "watch":
        handle_watch(args)
    elif args.command == "query":
        handle_query(args)
    elif args.command == "clean":
        handle_clean(args)


if __name__ == "__main__":
    main()
