"""
promptdrift CLI

Usage:
  promptdrift compare v1.yaml v2.yaml --cases golden.json
  promptdrift compare v1.yaml v2.yaml --cases golden.json --no-judge --output report.json
  promptdrift compare v1.yaml v2.yaml --cases golden.json --fail-on-regression  # CI mode
  promptdrift init --dir ./prompts
"""
from __future__ import annotations
import argparse
import sys
import json
from pathlib import Path


def cmd_compare(args):
    from .compare import compare
    from .reporter import print_report, print_json, save_report

    try:
        report = compare(
            config_a=args.config_a,
            config_b=args.config_b,
            cases=args.cases,
            use_judge=not args.no_judge,
            judge_provider=args.judge_provider,
            judge_model=args.judge_model,
            verbose=not args.quiet,
        )
    except FileNotFoundError as e:
        print(f"\n  ✗ File not found: {e}", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"\n  ✗ Config error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"\n  ✗ Error: {e}", file=sys.stderr)
        if args.verbose:
            import traceback; traceback.print_exc()
        sys.exit(1)

    if args.json:
        print_json(report)
    else:
        print_report(
            report,
            show_cases=not args.summary,
            show_outputs=args.show_outputs,
        )

    if args.output:
        save_report(report, args.output)

    # Exit 1 in CI if regressions found
    if report.regressions and args.fail_on_regression:
        sys.exit(1)


def cmd_init(args):
    """Scaffold example prompt configs and test cases."""
    import yaml
    out = Path(args.dir)
    out.mkdir(parents=True, exist_ok=True)

    v1 = {
        "version": "v1",
        "provider": "openai",
        "model": "gpt-4o-mini",
        "system": "You are a helpful assistant. Be concise.",
        "prompt_template": "{input}",
        "temperature": 0.0,
        "max_tokens": 256,
    }
    v2 = {
        "version": "v2",
        "provider": "openai",
        "model": "gpt-4o-mini",
        "system": "You are a helpful assistant. Be concise and give a concrete example.",
        "prompt_template": "{input}",
        "temperature": 0.0,
        "max_tokens": 512,
    }
    cases = [
        {"id": "math", "input": "What is 12 × 8?", "expected": "96"},
        {"id": "summarise", "input": "Summarise in one sentence: The quick brown fox jumps over the lazy dog."},
        {"id": "code", "input": "Write a Python one-liner to reverse a string."},
        {"id": "reasoning", "input": "If 5 machines make 5 widgets in 5 minutes, how long does it take 100 machines to make 100 widgets?", "expected": "5 minutes"},
    ]

    with open(out / "v1.yaml", "w") as f:
        yaml.dump(v1, f, default_flow_style=False, sort_keys=False)
    with open(out / "v2.yaml", "w") as f:
        yaml.dump(v2, f, default_flow_style=False, sort_keys=False)
    with open(out / "golden.json", "w") as f:
        json.dump(cases, f, indent=2)

    print(f"\n  ✓ Created in {out}/")
    print(f"    v1.yaml      baseline prompt config")
    print(f"    v2.yaml      new prompt config")
    print(f"    golden.json  test cases\n")
    print(f"  Next:")
    print(f"    export OPENAI_API_KEY=sk-...")
    print(f"    promptdrift compare {out}/v1.yaml {out}/v2.yaml --cases {out}/golden.json\n")


def main():
    parser = argparse.ArgumentParser(
        prog="promptdrift",
        description="⚡ Semantic diff for LLM prompts. Catch regressions before prod.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  promptdrift compare v1.yaml v2.yaml --cases golden.json
  promptdrift compare v1.yaml v2.yaml --cases golden.json --no-judge
  promptdrift compare v1.yaml v2.yaml --cases golden.json --fail-on-regression
  promptdrift compare v1.yaml v2.yaml --cases golden.json --json -o report.json
  promptdrift init --dir ./prompts
        """,
    )
    sub = parser.add_subparsers(dest="command")

    # compare
    cp = sub.add_parser("compare", help="Compare two prompt versions")
    cp.add_argument("config_a", help="Baseline prompt config (YAML/JSON)")
    cp.add_argument("config_b", help="New prompt config (YAML/JSON)")
    cp.add_argument("--cases", required=True, help="Test cases file (JSON/JSONL/YAML)")
    cp.add_argument("--no-judge", action="store_true", help="Skip LLM-as-judge (embedding only, faster)")
    cp.add_argument("--judge-provider", default="openai")
    cp.add_argument("--judge-model", default="gpt-4o-mini")
    cp.add_argument("--json", action="store_true", help="Output raw JSON")
    cp.add_argument("--output", "-o", metavar="FILE", help="Save report to JSON file")
    cp.add_argument("--summary", action="store_true", help="Summary only, no per-case details")
    cp.add_argument("--show-outputs", action="store_true", help="Show truncated A/B outputs per case")
    cp.add_argument("--fail-on-regression", action="store_true", help="Exit 1 if regressions found (CI)")
    cp.add_argument("--quiet", "-q", action="store_true", help="Suppress progress output")
    cp.add_argument("--verbose", "-v", action="store_true", help="Verbose error traces")

    # init
    ip = sub.add_parser("init", help="Scaffold example files")
    ip.add_argument("--dir", default="./prompts", help="Output directory (default: ./prompts)")

    args = parser.parse_args()
    if args.command == "compare":
        cmd_compare(args)
    elif args.command == "init":
        cmd_init(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
