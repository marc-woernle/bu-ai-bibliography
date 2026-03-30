#!/usr/bin/env python3
"""
Set up Claude Code scheduled triggers for the BU AI Bibliography.

Creates weekly, monthly, and quarterly triggers that run Claude Code
autonomously to execute update scripts and handle any issues.

Usage:
    python install_schedules.py          # Install all triggers
    python install_schedules.py --list   # Show existing triggers
    python install_schedules.py --remove # Remove all BU bib triggers
"""

import argparse
import subprocess
import sys


WEEKLY_PROMPT = """Run the weekly bibliography update for the BU AI Bibliography project.

Steps:
1. cd ~/bu-ai-bibliography
2. Run: .venv/bin/python update_weekly.py
3. Read the output. If it succeeded, verify the site loads by checking docs/data.js exists and is valid.
4. If it failed, read the error traceback, investigate the cause, fix the issue in the code, and retry.
5. Git commits must use: git config user.name "Marc Woernle" and git config user.email "marcwho13@gmail.com"
6. Do NOT add Co-Authored-By lines to commits.
7. If you fix a bug, commit the fix separately with a descriptive message like "fix: handle OpenAlex API change".
8. If there are persistent issues you can't resolve, create a GitHub Issue describing the problem.
"""

MONTHLY_PROMPT = """Run the monthly bibliography update for the BU AI Bibliography project.

Steps:
1. cd ~/bu-ai-bibliography
2. Run: .venv/bin/python update_monthly.py
3. Read the output. Check the monthly report was generated in output/monthly_report_YYYYMM.md.
4. If it failed, read the error, investigate, fix, and retry.
5. Git commits: user.name "Marc Woernle", user.email "marcwho13@gmail.com". No Co-Authored-By.
6. If you fix a bug, commit the fix separately.
7. Verify the GitHub Issue was created with the monthly report summary.
"""

QUARTERLY_PROMPT = """Run the quarterly review for the BU AI Bibliography project.

Steps:
1. cd ~/bu-ai-bibliography
2. Run: .venv/bin/python quarterly_review.py
3. Read the generated report at output/quarterly_review_YYYYMMDD.md.
4. Review the key findings: faculty gaps, random sample quality, trends.
5. If the script fails, fix it and retry.
6. Verify a GitHub Issue was created linking to the report.
7. Do NOT push any data changes — this is read-only analysis.
"""


def run_cmd(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    """Run a command and return result."""
    result = subprocess.run(cmd, capture_output=True, text=True)
    if check and result.returncode != 0:
        print(f"Error: {result.stderr}")
    return result


def install_triggers():
    """Install Claude Code scheduled triggers."""
    print("Setting up Claude Code scheduled triggers...\n")

    triggers = [
        {
            "name": "bu-bib-weekly",
            "schedule": "0 3 * * 0",  # Sunday 3am
            "prompt": WEEKLY_PROMPT,
            "description": "Weekly BU AI Bibliography update",
        },
        {
            "name": "bu-bib-monthly",
            "schedule": "0 4 1 * *",  # 1st of month 4am
            "prompt": MONTHLY_PROMPT,
            "description": "Monthly BU AI Bibliography deep update",
        },
        {
            "name": "bu-bib-quarterly",
            "schedule": "0 5 1 1,4,7,10 *",  # 1st of Jan/Apr/Jul/Oct at 5am
            "prompt": QUARTERLY_PROMPT,
            "description": "Quarterly BU AI Bibliography review",
        },
    ]

    for t in triggers:
        print(f"  Creating trigger: {t['name']} ({t['schedule']})")
        # Note: This uses the claude CLI's schedule command
        # The actual command depends on Claude Code's scheduling API
        print(f"    → Run: claude schedule create --name '{t['name']}' --cron '{t['schedule']}' --prompt '...'")
        print(f"    Description: {t['description']}")
        print()

    print("To install triggers manually, run these commands:")
    print()
    for t in triggers:
        prompt_escaped = t["prompt"].replace("'", "'\\''").strip()
        print(f"claude schedule create \\")
        print(f"  --name '{t['name']}' \\")
        print(f"  --cron '{t['schedule']}' \\")
        print(f"  --project ~/bu-ai-bibliography \\")
        print(f"  --prompt '{prompt_escaped[:200]}...'")
        print()


def list_triggers():
    """List existing Claude Code triggers."""
    result = run_cmd(["claude", "schedule", "list"], check=False)
    if result.returncode == 0:
        print(result.stdout)
    else:
        print("Could not list triggers. Is Claude Code CLI installed?")
        print(f"Error: {result.stderr}")


def main():
    parser = argparse.ArgumentParser(description="Set up bibliography update schedules")
    parser.add_argument("--list", action="store_true", help="List existing triggers")
    parser.add_argument("--remove", action="store_true", help="Remove BU bib triggers")
    args = parser.parse_args()

    if args.list:
        list_triggers()
    elif args.remove:
        print("To remove triggers, run:")
        print("  claude schedule delete bu-bib-weekly")
        print("  claude schedule delete bu-bib-monthly")
        print("  claude schedule delete bu-bib-quarterly")
    else:
        install_triggers()


if __name__ == "__main__":
    main()
