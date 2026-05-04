"""
Terminal reporter — the output that gets shared on Twitter.
"""
from __future__ import annotations
import json
from .core import DiffReport, CaseResult

# ANSI
R = "\033[0m"; BOLD = "\033[1m"; DIM = "\033[2m"
RED = "\033[91m"; GRN = "\033[92m"; YLW = "\033[93m"
BLU = "\033[94m"; MGT = "\033[95m"; CYN = "\033[96m"
ORG = "\033[38;5;208m"

LEVEL_COLOR = {"none": GRN, "minor": CYN, "moderate": YLW, "major": ORG, "regression": RED}
LEVEL_ICON  = {"none": "●", "minor": "◐", "moderate": "◑", "major": "◕", "regression": "✗"}
VERDICT_STR = {"improved": f"{GRN}↑ improved{R}", "regressed": f"{RED}↓ regressed{R}",
               "neutral": f"{DIM}→ neutral{R}", None: f"{DIM}—{R}"}


def _bar(v: float, w: int = 22, color: str = GRN) -> str:
    filled = round(v * w)
    return color + "█" * filled + DIM + "░" * (w - filled) + R


def _trunc(s: str, n: int = 72) -> str:
    s = s.replace("\n", " ").strip()
    return s[:n-3] + "..." if len(s) > n else s


def print_report(report: DiffReport, show_cases: bool = True, show_outputs: bool = False):
    W = 72
    print()
    print(f"{BOLD}{ORG}{'━' * W}{R}")
    print(f"{BOLD}  ⚡ promptdrift{R}  {DIM}v0.1.1 · github.com/dakshtrehan/promptdrift{R}")
    print(f"{BOLD}{ORG}{'━' * W}{R}")
    print()
    print(f"  {DIM}A{R}  {BOLD}{report.version_a}{R}  {DIM}({report.model_a}){R}")
    print(f"  {DIM}B{R}  {BOLD}{report.version_b}{R}  {DIM}({report.model_b}){R}")
    print(f"  {DIM}Cases: {report.total_cases}{R}")
    print()

    # Summary bars
    dp = round(report.avg_drift * 100, 1)
    rp = round(report.regression_rate * 100, 1)
    dc = GRN if dp < 10 else YLW if dp < 30 else RED
    print(f"  {BOLD}Summary{R}")
    print(f"  {'─' * (W-2)}")
    print(f"  Avg drift         {_bar(report.avg_drift, 22, dc)}  {dc}{BOLD}{dp}%{R}")
    print(f"  Regression rate   {_bar(report.regression_rate, 22, RED)}  {RED}{BOLD}{rp}%{R}")
    print()

    ni = len(report.improvements)
    nr = len(report.regressions)
    nn = len(report.neutral)
    print(f"  {GRN}↑ Improved  {ni:3d}{R}   {RED}↓ Regressed {nr:3d}{R}   {DIM}→ Neutral   {nn:3d}{R}")
    print()

    # Overall verdict
    if nr == 0 and dp < 5:
        verdict = f"{GRN}{BOLD}  ✓  SAFE TO SHIP — no regressions, minimal drift{R}"
    elif nr == 0:
        verdict = f"{YLW}{BOLD}  ⚡  REVIEW — semantic drift detected, no regressions{R}"
    else:
        verdict = f"{RED}{BOLD}  ✗  DO NOT SHIP — {nr} regression(s) detected{R}"

    print(f"  {'─' * (W-2)}")
    print(verdict)
    print(f"  {'─' * (W-2)}")
    print()

    if not show_cases:
        _footer(W)
        return

    # Per-case breakdown
    print(f"{BOLD}  Cases{R}\n")
    for c in report.cases:
        col = LEVEL_COLOR[c.drift_level]
        icon = LEVEL_ICON[c.drift_level]
        sim_pct = round(c.similarity * 100, 1)
        verdict_s = VERDICT_STR.get(c.judge_verdict, VERDICT_STR[None])

        print(f"  {col}{icon}{R} {BOLD}{c.case_id}{R}  "
              f"{DIM}sim={sim_pct}%{R}  {_bar(c.drift_score, 16, col)}  {verdict_s}")
        print(f"    {DIM}in › {R}{_trunc(c.input, 62)}")

        if show_outputs:
            print(f"    {DIM}A  › {R}{_trunc(c.output_a, 62)}")
            print(f"    {DIM}B  › {R}{_trunc(c.output_b, 62)}")

        if c.judge_reasoning and c.judge_verdict in ("improved", "regressed"):
            rc = GRN if c.judge_verdict == "improved" else RED
            print(f"    {rc}⚖  {c.judge_reasoning}{R}")

        if c.latency_a or c.latency_b:
            delta = round(c.latency_b - c.latency_a, 3)
            lc = RED if delta > 0.5 else GRN if delta < -0.2 else DIM
            print(f"    {DIM}lat A={c.latency_a}s  B={c.latency_b}s  Δ={lc}{delta:+.3f}s{R}{DIM}{R}")
        print()

    # Regressions detail
    if report.regressions:
        print(f"  {RED}{BOLD}Regressions ({len(report.regressions)}){R}")
        print(f"  {'─' * (W-2)}")
        for c in report.regressions:
            print(f"\n  {RED}✗{R} {BOLD}{c.case_id}{R}")
            print(f"    {DIM}Input:  {R}{_trunc(c.input, 60)}")
            print(f"    {DIM}A:      {R}{_trunc(c.output_a, 60)}")
            print(f"    {RED}B:      {R}{_trunc(c.output_b, 60)}")
            if c.judge_reasoning:
                print(f"    {RED}Why:    {c.judge_reasoning}{R}")
        print()

    _footer(W)


def _footer(w: int):
    print(f"{BOLD}{ORG}{'━' * w}{R}\n")


def print_json(report: DiffReport):
    print(report.to_json())


def save_report(report: DiffReport, path: str):
    with open(path, "w") as f:
        f.write(report.to_json())
    print(f"  {GRN}✓{R} Report saved → {BOLD}{path}{R}")
