"""
Triage Orchestrator — connects the live IMAP fetcher, RAG engine,
Ollama LLM, and Draft Governor into a single runnable pipeline.

Usage:
    python triage_orchestrator.py --profile university --dry-run
    python triage_orchestrator.py --profile personal
    python triage_orchestrator.py --all
"""
import argparse
import json
import logging
import sys
from pathlib import Path
from datetime import datetime, timezone

# Local imports
sys.path.insert(0, str(Path(__file__).resolve().parent))
from imap_fetcher import IMAPFetcher
from academic_triage_loop import AcademicTriageSubsystem
from email_summarizer import EmailSummarizer
from local_rag_engine import PIIRedactor
from triage_core_lib import get_triage_root, ensure_directories
from concurrency_guard import append_to_ledger, safe_json_read, safe_json_write

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


def run_academic_triage(dry_run: bool = False) -> dict:
    """
    Fetch unread university emails, classify them, generate drafts
    via local Ollama, and save to [Gmail]/Drafts.
    """
    results = {"profile": "university", "fetched": 0, "drafted": 0, "errors": []}
    root = get_triage_root()
    redactor = PIIRedactor()
    subsystem = AcademicTriageSubsystem()

    # Load roster DB if it exists
    roster_path = root / ".config" / "roster.json"
    roster_db = {}
    if roster_path.exists():
        roster_db = safe_json_read(str(roster_path))
        logger.info("Loaded roster with %d entries.", len(roster_db))

    try:
        with IMAPFetcher("university") as fetcher:
            messages = fetcher.fetch_unread(limit=15)
            results["fetched"] = len(messages)
            logger.info("Fetched %d unread university emails.", len(messages))

            for msg in messages:
                try:
                    # Classify
                    folder = subsystem.classify_email(
                        msg["subject"], msg["from_email"], roster_db
                    )
                    logger.info(
                        "Classified '%s' from %s → %s",
                        msg["subject"], msg["from_email"], folder,
                    )

                    # Redact PII before processing
                    safe_body = redactor.redact(msg["body_plain"])

                    # Generate draft via local Ollama + RAG
                    draft_result = subsystem.process_email(safe_body)

                    if dry_run:
                        logger.info(
                            "[DRY-RUN] Would save draft for: %s",
                            msg["subject"],
                        )
                    else:
                        fetcher.save_draft(
                            subject=msg["subject"],
                            body=draft_result["draft_body"],
                            in_reply_to=msg.get("uid", ""),
                        )
                        results["drafted"] += 1

                    # Audit log
                    append_to_ledger(
                        str(root / ".logs" / "triage_audit_ledger.jsonl"),
                        "ACADEMIC_TRIAGE",
                        {
                            "subject": msg["subject"],
                            "from": msg["from_email"],
                            "folder": folder,
                            "uncertain": draft_result.get("uncertain", False),
                            "dry_run": dry_run,
                        },
                    )

                except Exception as e:
                    logger.error("Failed to process '%s': %s", msg["subject"], e)
                    results["errors"].append(
                        {"subject": msg["subject"], "error": str(e)}
                    )

    except Exception as e:
        logger.error("University IMAP connection failed: %s", e)
        results["errors"].append({"connection": str(e)})

    return results


def run_personal_triage(dry_run: bool = False) -> dict:
    """
    Fetch unread personal emails, route sensitive ones to local Ollama,
    summarize, and save drafts.
    """
    results = {"profile": "personal", "fetched": 0, "drafted": 0, "errors": []}
    root = get_triage_root()
    summarizer = EmailSummarizer()

    try:
        with IMAPFetcher("personal") as fetcher:
            messages = fetcher.fetch_unread(limit=15)
            results["fetched"] = len(messages)
            logger.info("Fetched %d unread personal emails.", len(messages))

            for msg in messages:
                try:
                    summary = summarizer.summarize(msg["subject"], msg["body_plain"])
                    route = summary["route"]
                    logger.info(
                        "Routed '%s' → %s", msg["subject"], route
                    )

                    draft_body = (
                        f"--- AI SUMMARY ---\n{summary['summary']}\n\n"
                        f"--- ACTION ITEMS ---\n"
                        + "\n".join(summary["action_items"])
                        + "\n\n[AI ASSISTED DRAFT - REVIEW REQUIRED BEFORE SENDING]"
                    )

                    if dry_run:
                        logger.info(
                            "[DRY-RUN] Would save draft for: %s", msg["subject"]
                        )
                    else:
                        fetcher.save_draft(
                            subject=msg["subject"], body=draft_body
                        )
                        results["drafted"] += 1

                    append_to_ledger(
                        str(root / ".logs" / "triage_audit_ledger.jsonl"),
                        "PERSONAL_TRIAGE",
                        {
                            "subject": msg["subject"],
                            "from": msg["from_email"],
                            "route": route,
                            "dry_run": dry_run,
                        },
                    )

                except Exception as e:
                    logger.error("Failed to process '%s': %s", msg["subject"], e)
                    results["errors"].append(
                        {"subject": msg["subject"], "error": str(e)}
                    )

    except Exception as e:
        logger.error("Personal IMAP connection failed: %s", e)
        results["errors"].append({"connection": str(e)})

    return results


def main():
    parser = argparse.ArgumentParser(description="CoChem Email Triage Orchestrator")
    parser.add_argument(
        "--profile",
        choices=["university", "personal"],
        help="Run a single profile.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run both university and personal profiles.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and classify but do NOT save drafts.",
    )
    args = parser.parse_args()

    ensure_directories()

    if not args.profile and not args.all:
        parser.error("Specify --profile university|personal or --all.")

    all_results = []

    if args.all or args.profile == "university":
        r = run_academic_triage(dry_run=args.dry_run)
        all_results.append(r)
        print(json.dumps(r, indent=2))

    if args.all or args.profile == "personal":
        r = run_personal_triage(dry_run=args.dry_run)
        all_results.append(r)
        print(json.dumps(r, indent=2))

    # Summary
    total_fetched = sum(r["fetched"] for r in all_results)
    total_drafted = sum(r["drafted"] for r in all_results)
    total_errors = sum(len(r["errors"]) for r in all_results)
    print(f"\n{'='*50}")
    print(f"TRIAGE COMPLETE: {total_fetched} fetched, {total_drafted} drafted, {total_errors} errors")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()
