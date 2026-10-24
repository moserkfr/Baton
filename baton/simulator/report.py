"""Run the simulator experiments and write results/ (markdown tables, JSON, charts).

    python -m baton.simulator.report            # full run, 5 seeds
    python -m baton.simulator.report --seeds 2  # quicker

Experiments:
1. Main comparison: the four policies over the same workload, averaged over several seeds.
2. Idle-window sensitivity: park-and-restore (and the activity culler) at 10 / 30 / 60 min.
3. Load sensitivity: hold-until-logout vs park-and-restore as demand grows.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, replace
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from ..config import load_config, resolve_path
from .policies import Policy, activity_culler, hold_until_logout, park_and_restore, standard_policies
from .sim import QueueParams, metrics, simulate
from .workload import WorkloadParams, calibration_report, generate_workload, helios_path

# Reference categorical palette (dataviz skill, light mode), fixed order: one hue per policy.
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]
INK, INK2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"

KEYS = ["idle_gpu_hours_held", "gpu_hours_reclaimed", "wait_median_min", "wait_p95_min",
        "sessions_state_lost", "rerun_gpu_hours", "return_wait_p95_min", "parks", "jain_gpu_hours"]


def run_policies(policies: list[Policy], wp: WorkloadParams, seeds: list[int], n_gpus: int,
                 qp: QueueParams, helios_csv: Path | None) -> dict[str, dict[str, list[float]]]:
    """metric values per policy per seed. Reclaimed hours are measured against hold-until-logout."""
    out: dict[str, dict[str, list[float]]] = {p.name: {} for p in policies}
    hold = hold_until_logout()
    for seed in seeds:
        sessions = generate_workload(seed, wp, helios_csv)
        base = metrics(simulate(sessions, hold, n_gpus, qp), wp.students)
        for p in policies:
            m = base if p.kind == "hold" else metrics(simulate(sessions, p, n_gpus, qp), wp.students)
            m = dict(m, gpu_hours_reclaimed=base["idle_gpu_hours_held"] - m["idle_gpu_hours_held"])
            for k, v in m.items():
                out[p.name].setdefault(k, []).append(float(v))
    return out


def mean(d: dict[str, list[float]], k: str) -> float:
    return float(np.mean(d[k]))


def fmt(v: float, k: str) -> str:
    if k == "jain_gpu_hours":
        return f"{v:.2f}"
    if k in ("sessions_state_lost", "parks"):
        return f"{v:.0f}"
    return f"{v:.1f}"


def main_table(res: dict[str, dict[str, list[float]]]) -> str:
    head = ("| Policy | Idle GPU-h held | GPU-h reclaimed | Median wait (min) | p95 wait (min) | "
            "Sessions with lost state | GPU-h re-running lost work | Jain fairness |")
    lines = [head, "|" + " --- |" * 8]
    for name, d in res.items():
        lost = mean(d, "sessions_state_lost")
        lines.append(
            f"| {name} | {mean(d, 'idle_gpu_hours_held'):.0f} | {mean(d, 'gpu_hours_reclaimed'):.0f} | "
            f"{mean(d, 'wait_median_min'):.1f} | {mean(d, 'wait_p95_min'):.0f} | "
            f"{lost:.0f} of {mean(d, 'sessions'):.0f} | {mean(d, 'rerun_gpu_hours'):.1f} | "
            f"{mean(d, 'jain_gpu_hours'):.2f} |")
    return "\n".join(lines)


# ---------------------------------------------------------------- charts

def _style(ax, title: str, ylabel: str) -> None:
    ax.set_facecolor(SURFACE)
    ax.set_title(title, loc="left", fontsize=13, color=INK, pad=12)
    ax.set_ylabel(ylabel, color=INK2)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color(INK2)
    ax.tick_params(colors=INK2, length=0)


def _num(v: float) -> str:
    return f"{v:.1f}" if 0 < abs(v) < 10 else f"{v:.0f}"


def _short(name: str) -> str:
    return name.split(" (")[0].replace("Park and restore", "Park & restore\n(ours)").replace(
        "GPU-aware cancel", "GPU-aware\ncancel").replace("Activity culler", "Activity\nculler").replace(
        "Hold until logout", "Hold until\nlogout")


def bar_chart(res, key: str, title: str, ylabel: str, path: Path) -> None:
    names = list(res)
    vals = [mean(res[n], key) for n in names]
    fig, ax = plt.subplots(figsize=(8, 4.5), facecolor=SURFACE)
    bars = ax.bar(range(len(names)), vals, width=0.55, color=SERIES[:len(names)],
                  edgecolor=SURFACE, linewidth=2)
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, v, _num(v), ha="center", va="bottom", color=INK, fontsize=11)
    ax.set_xticks(range(len(names)), [_short(n) for n in names], color=INK)
    _style(ax, title, ylabel)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def wait_chart(res, path: Path) -> None:
    """Median and p95 wait as two small panels (same unit, but very different scales)."""
    names = list(res)
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5), facecolor=SURFACE)
    for ax, key, title in ((axes[0], "wait_median_min", "Median wait for a GPU"),
                           (axes[1], "wait_p95_min", "95th-percentile wait for a GPU")):
        vals = [mean(res[n], key) for n in names]
        bars = ax.bar(range(len(names)), vals, width=0.55, color=SERIES, edgecolor=SURFACE, linewidth=2)
        for b, v in zip(bars, vals):
            ax.text(b.get_x() + b.get_width() / 2, v, _num(v), ha="center", va="bottom", color=INK)
        ax.set_xticks(range(len(names)), [_short(n) for n in names], color=INK, fontsize=9)
        _style(ax, title, "minutes")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def line_chart(xs, series: dict[str, list[float]], colors: list[str], title: str, xlabel: str,
               ylabel: str, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(8, 4.5), facecolor=SURFACE)
    ends = [ys[-1] for ys in series.values()]
    span = max(max(max(ys) for ys in series.values()), 1e-9)
    close = len(ends) == 2 and abs(ends[0] - ends[1]) < 0.06 * span  # nudge end labels apart
    for i, ((name, ys), c) in enumerate(zip(series.items(), colors)):
        ax.plot(xs, ys, color=c, linewidth=2, marker="o", markersize=8,
                markeredgecolor=SURFACE, markeredgewidth=2, label=name)
        dy = 0 if not close else (9 if ys[-1] >= min(ends) and i == ends.index(max(ends)) else -9)
        ax.annotate(name, (xs[-1], ys[-1]), xytext=(8, dy), textcoords="offset points",
                    va="center", color=INK, fontsize=10)
    ax.set_xticks(xs)
    ax.set_xlabel(xlabel, color=INK2)
    ax.legend(frameon=False, labelcolor=INK, loc="lower left")
    ax.set_ylim(bottom=0)
    _style(ax, title, ylabel)
    ax.set_xlim(xs[0] - (xs[-1] - xs[0]) * 0.05, xs[-1] + (xs[-1] - xs[0]) * 0.35)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


# ---------------------------------------------------------------- main

def build_params(cfg: dict) -> tuple[WorkloadParams, int, QueueParams, Policy]:
    sc, qc = cfg["simulator"], cfg["queue"]
    wp = WorkloadParams(students=sc["students"], days=sc["days"],
                        sessions_per_student_day=sc["sessions_per_student_day"],
                        batch_jobs_per_day=sc["batch_jobs_per_day"])
    qp = QueueParams(usage_halflife_min=qc["usage_halflife_h"] * 60,
                     usage_weight=qc["usage_weight_s_per_gpuh"] / 60,
                     returning_priority_min=qc["returning_priority_s"] / 60)
    ours = park_and_restore(sc["idle_window_min"], sc["grace_min"], sc["park_s"], sc["restore_s"])
    return wp, sc["machines"] * sc["gpus_per_machine"], qp, ours


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--out", default="results")
    args = ap.parse_args(argv)

    cfg = load_config()
    sc = cfg["simulator"]
    wp, n_gpus, qp, ours = build_params(cfg)
    seeds = [sc["seed"] + i for i in range(args.seeds)]
    helios = helios_path(resolve_path("data"), sc["helios_cluster"])
    helios_csv = helios if helios.exists() else None
    out = resolve_path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    print(f"Helios trace: {'loaded ' + str(helios.name) if helios_csv else 'MISSING, lognormal fallback'}")

    # 1. calibration + main comparison
    calib = calibration_report(generate_workload(sc["seed"], replace(wp, days=200), None))
    policies = standard_policies(sc["idle_window_min"], sc["park_s"], sc["restore_s"], sc["grace_min"])
    main_res = run_policies(policies, wp, seeds, n_gpus, qp, helios_csv)
    bar_chart(main_res, "idle_gpu_hours_held", f"GPU-hours held while idle ({wp.days} working days)",
              "GPU-hours", out / "idle_gpu_hours.png")
    wait_chart(main_res, out / "wait_times.png")

    # 2. idle-window sweep
    windows = [10, 30, 60]
    sweep = {}
    for w in windows:
        pols = [activity_culler(w), park_and_restore(w, sc["grace_min"], sc["park_s"], sc["restore_s"])]
        sweep[w] = run_policies(pols, wp, seeds, n_gpus, qp, helios_csv)
    park_by_w = [list(sweep[w].values())[1] for w in windows]
    cull_by_w = [list(sweep[w].values())[0] for w in windows]
    line_chart(windows, {"Park & restore": [mean(d, "gpu_hours_reclaimed") for d in park_by_w],
                         "Activity culler": [mean(d, "gpu_hours_reclaimed") for d in cull_by_w]},
               [SERIES[3], SERIES[1]], "GPU-hours reclaimed vs idle window", "idle window (minutes)",
               "GPU-hours reclaimed", out / "sweep_idle_window.png")

    # 3. load sweep
    rates = [0.3, 0.45, 0.6]
    load = {r: run_policies([hold_until_logout(), ours], replace(wp, sessions_per_student_day=r),
                            seeds, n_gpus, qp, helios_csv) for r in rates}
    line_chart(rates, {"Hold until logout": [mean(list(load[r].values())[0], "wait_p95_min") for r in rates],
                       "Park & restore": [mean(list(load[r].values())[1], "wait_p95_min") for r in rates]},
               [SERIES[0], SERIES[3]], "95th-percentile wait as demand grows",
               "notebook sessions per student per day", "minutes", out / "sweep_load.png")

    # ---- markdown
    md = [f"# Simulator results\n",
          f"Lab: {sc['machines']} machines x {sc['gpus_per_machine']} GPUs = {n_gpus} GPUs, {wp.students} students, "
          f"{wp.days} working days, {wp.sessions_per_student_day} notebook sessions per student per day "
          f"(mean) + {wp.batch_jobs_per_day:g} Helios batch jobs per day. Mean of {len(seeds)} seeds "
          f"({seeds[0]}-{seeds[-1]}). Park {sc['park_s']} s, restore {sc['restore_s']} s (measured on the laptop). "
          f"Idle window {sc['idle_window_min']} min + {sc['grace_min']} min grace for park-and-restore.\n",
          "## Four policies, same workload\n", main_table(main_res), "",
          "GPU-hours reclaimed = idle GPU-hours held under hold-until-logout minus idle GPU-hours held under the "
          "policy. Waits include every request: first requests, and returns after a park (incl. restore time) "
          "or a kill. Jain fairness is over GPU-hours held per student.\n",
          "![Idle GPU-hours](idle_gpu_hours.png)\n", "![Wait times](wait_times.png)\n",
          "## Idle-window sensitivity\n",
          "| Idle window | Policy | GPU-h reclaimed | p95 wait (min) | Parks / kills | Warned, then resumed | "
          "Returns | p95 wait on return (min) | Sessions with lost state |",
          "|" + " --- |" * 9]
    for w in windows:
        for name, d in sweep[w].items():
            acts = mean(d, "parks") if "Park" in name else mean(d, "sessions_state_lost")
            md.append(f"| {w} min | {name.split(' (')[0]} | {mean(d, 'gpu_hours_reclaimed'):.0f} | "
                      f"{mean(d, 'wait_p95_min'):.0f} | {acts:.0f} | {mean(d, 'warned_then_resumed'):.0f} | "
                      f"{mean(d, 'returns'):.0f} | {mean(d, 'return_wait_p95_min'):.1f} | "
                      f"{mean(d, 'sessions_state_lost'):.0f} |")
    md += ["", "![Idle-window sweep](sweep_idle_window.png)\n", "## Load sensitivity\n",
           "| Sessions / student / day | Policy | Median wait (min) | p95 wait (min) | Idle GPU-h held |",
           "|" + " --- |" * 5]
    for r in rates:
        for name, d in load[r].items():
            md.append(f"| {r} | {name.split(' (')[0]} | {mean(d, 'wait_median_min'):.1f} | "
                      f"{mean(d, 'wait_p95_min'):.0f} | {mean(d, 'idle_gpu_hours_held'):.0f} |")
    md += ["", "![Load sweep](sweep_load.png)\n", "## Workload calibration (NotebookOS targets)\n",
           "| Statistic | Published | Generated |", "| --- | --- | --- |"]
    md += [f"| {c.name} | {c.target} | {c.achieved:.1f} {c.unit} |" for c in calib]
    (out / "sim_results.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    (out / "sim_results.json").write_text(json.dumps(
        {"main": main_res, "sweep": {str(w): v for w, v in sweep.items()},
         "load": {str(r): v for r, v in load.items()}, "calibration": [asdict(c) for c in calib],
         "workload_params": asdict(wp), "seeds": seeds}, indent=1), encoding="utf-8")
    print(main_table(main_res))
    print(f"wrote {out / 'sim_results.md'}")


if __name__ == "__main__":
    main()
