"""
promptdrift CLI

Usage:
  promptdrift compare v1.yaml v2.yaml --cases golden.json
  promptdrift compare v1.yaml v2.yaml --cases golden.json --no-judge --output report.json
  promptdrift compare v1.yaml v2.yaml --cases golden.json --fail-on-regression  # CI mode
  promptdrift diff outputs.jsonl                     # offline, no model calls
  promptdrift init --dir ./prompts
"""
from __future__ import annotations
import argparse
import sys
import json
from pathlib import Path


def cmd_compare(args):
    from .compare import compare

    try:
        report = compare(
            config_a=args.config_a,
            config_b=args.config_b,
            cases=args.cases,
            use_judge=not args.no_judge,
            judge_provider=args.judge_provider,
            judge_model=args.judge_model,
            judge_both_orders=not args.judge_single_order,
            verbose=not args.quiet,
            checks=_load_checks(args.checks),
            samples=args.samples,
            bootstrap=args.bootstrap,
            seed=args.seed,
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

    _emit(report, args)


def _emit(report, args):
    """Print / save a report and set the exit code. Shared by compare and diff."""
    from .reporter import print_json, print_report, save_report

    if args.json:
        print_json(report)
    else:
        print_report(report, show_cases=not args.summary, show_outputs=args.show_outputs)

    if args.output:
        save_report(report, args.output)

    # Exit 1 in CI if regressions found
    if report.regressions and args.fail_on_regression:
        sys.exit(1)


def cmd_diff(args):
    """Offline mode: score stored outputs without calling any model."""
    from .offline import compare_outputs

    try:
        report = compare_outputs(
            args.outputs,
            version_a=args.name_a,
            version_b=args.name_b,
            use_judge=args.judge,
            judge_provider=args.judge_provider,
            judge_model=args.judge_model,
            judge_both_orders=not args.judge_single_order,
            checks=_load_checks(args.checks),
            bootstrap=args.bootstrap,
            seed=args.seed,
            verbose=args.verbose,
        )
    except FileNotFoundError as e:
        print(f"\n  ✗ File not found: {e}", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"\n  ✗ Input error: {e}", file=sys.stderr)
        sys.exit(1)

    _emit(report, args)


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


def _load_checks(path):
    """Load a default checks spec from a JSON or YAML file (None when not given)."""
    if not path:
        return None
    from .checks import normalize_checks

    with open(path) as f:
        if str(path).lower().endswith((".yaml", ".yml")):
            import yaml
            spec = yaml.safe_load(f)
        else:
            spec = json.load(f)
    normalize_checks(spec, expected="")  # validate early; 'exact' may use each case's expected
    return spec


def _add_judge_args(p):
    p.add_argument("--judge-provider", default=None,
                   help="openai | anthropic | ollama (default: from OPENAI_API_KEY, "
                        "else ANTHROPIC_API_KEY)")
    p.add_argument("--judge-model", default=None,
                   help="Judge model (default depends on the provider)")
    p.add_argument("--judge-single-order", action="store_true",
                   help="Judge A-vs-B only (v0.1.1 behaviour) instead of both orders")


def _add_output_args(p):
    p.add_argument("--bootstrap", type=int, default=1000, metavar="N",
                   help="Bootstrap resamples for 95%% CIs (default 1000, 0 disables)")
    p.add_argument("--seed", type=int, default=0, help="Random seed for the bootstrap")
    p.add_argument("--checks", metavar="FILE",
                   help="Default deterministic checks (JSON/YAML) for cases without their own")
    p.add_argument("--json", action="store_true", help="Output raw JSON")
    p.add_argument("--output", "-o", metavar="FILE", help="Save report to JSON file")
    p.add_argument("--summary", action="store_true", help="Summary only, no per-case details")
    p.add_argument("--show-outputs", action="store_true",
                   help="Show truncated A/B outputs per case")
    p.add_argument("--fail-on-regression", action="store_true",
                   help="Exit 1 if regressions found (CI)")


def main(argv=None):
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
  promptdrift diff outputs.jsonl --name-a old --name-b new
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
    _add_judge_args(cp)
    cp.add_argument("--samples", "-n", type=int, default=1,
                    help="Samples per side per case; >1 reports noise floor and excess drift")
    _add_output_args(cp)
    cp.add_argument("--quiet", "-q", action="store_true", help="Suppress progress output")
    cp.add_argument("--verbose", "-v", action="store_true", help="Verbose error traces")

    # diff (offline)
    dp = sub.add_parser("diff", help="Compare stored outputs offline (no model calls)")
    dp.add_argument("outputs", help="JSONL (or JSON array) of {case_id, output_a, output_b}")
    dp.add_argument("--name-a", default="A", help="Label for side A (default: A)")
    dp.add_argument("--name-b", default="B", help="Label for side B (default: B)")
    dp.add_argument("--judge", action="store_true",
                    help="Also run the LLM judge (calls a model; off by default)")
    _add_judge_args(dp)
    _add_output_args(dp)
    dp.add_argument("--verbose", "-v", action="store_true", help="Show per-case progress")

    # init
    ip = sub.add_parser("init", help="Scaffold example files")
    ip.add_argument("--dir", default="./prompts", help="Output directory (default: ./prompts)")

    args = parser.parse_args(argv)
    if args.command == "compare":
        cmd_compare(args)
    elif args.command == "diff":
        cmd_diff(args)
    elif args.command == "init":
        cmd_init(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
