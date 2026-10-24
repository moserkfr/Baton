# Baton

A small daemon that reclaims idle GPUs on shared lab servers. It spots processes that hold GPU memory but
do no compute, **parks** them (GPU memory moved to host RAM, process still running), gives the freed GPU
to the next student in a **fair queue**, and **restores** the parked session later with every tensor and
optimizer state intact.

Three layers:
1. **Simulator**: a 40-student, 8-GPU lab replaying calibrated notebook sessions plus Helios batch jobs
   under four policies. This is where the headline numbers come from.
2. **Live mini-lab**: this laptop's RTX 3050 acts as a 2-slot server, with 4 student processes and the
   real daemon.
3. **Dashboard**: a FastAPI page with the live slot map, queue, receipts and results.

How it works and why: [docs/how-it-works.md](docs/how-it-works.md). Decisions: [DECISIONS.md](DECISIONS.md).

## Headline results

| Policy (simulated lab, 5 days, mean of 5 seeds) | Idle GPU-h held | p95 wait | Sessions with lost state |
| --- | --- | --- | --- |
| Hold until logout | 385 | 264 min | 0 of 84 |
| Activity culler (kill after 30 min) | 63 | 0 min | 70 of 84 |
| GPU-aware cancel (warn 45, cancel 90 min) | 124 | 12 min | 48 of 84 |
| **Park and restore (ours)** | **69** | **0 min** | **0 of 84** |

Live on the laptop: a student was parked in 0.3 s and restored in 0.1 s, with an identical checksum
([results/scenario_results.md](results/scenario_results.md)).

## Install (Windows 11, Python 3.13, NVIDIA GPU)

```powershell
python -m venv --system-site-packages .venv      # reuses an existing torch 2.10+cu130 install
.venv\Scripts\python -m pip install -r requirements.txt
```
No torch yet? Install it first from `https://download.pytorch.org/whl/cu130`.
The simulator also wants the Helios trace (35 MB). Without it, the simulator falls back to a log-normal
stand-in for batch jobs:
```powershell
mkdir data\helios; curl.exe -L -o data\helios\data.zip https://raw.githubusercontent.com/S-Lab-System-Group/HeliosData/master/data.zip
.venv\Scripts\python -c "import zipfile; zipfile.ZipFile('data/helios/data.zip').extractall('data/helios')"
```

## Run the simulator (no GPU needed, ~1 min)

```powershell
.venv\Scripts\python -m baton.simulator.report          # writes results/sim_results.md + charts
```

## Run the live demo (about 2 minutes of GPU time)

Close other GPU apps first and keep the laptop plugged in. Use two terminals:
```powershell
# terminal 1: the dashboard, open http://127.0.0.1:8000
.venv\Scripts\python -m baton.dashboard.app
# terminal 2: the scripted story (starts its own daemon + students A-D, cleans up afterwards)
.venv\Scripts\python -m baton.minilab.scenario --pace 2
```
What happens: A and B take both slots, B goes idle, C queues, B is warned then parked, C gets the slot,
D queues, B comes back and jumps ahead of D, B is restored with a matching checksum, then D gets A's slot.
Logs and numbers land in `results/scenario_runN.{log,json}`.

**Driving it by hand instead:** start the daemon, then students in separate terminals, and type commands
into each student (`work 20`, `checksum`, `touch`, `exit`):
```powershell
.venv\Scripts\python -m baton.daemon.server            # idle window 30 s, grace 10 s (config.yaml)
.venv\Scripts\python -m baton.minilab.students A
.venv\Scripts\python -m baton.minilab.students B
.venv\Scripts\python -m baton.cli.gpuq status          # or park-now B / resume B
```
Preview the dashboard without a GPU:
`python -m baton.dashboard.preview` (writes `data/preview.db`), then
`python -m baton.dashboard.app --db data/preview.db`.

## Friendly features

| Feature | How to use it |
| --- | --- |
| Jupyter with no code changes | `%load_ext baton.jupyter` in the first cell; `%gpuq`, `%gpuq snooze`, `%gpuq away 45` |
| Back at 3pm (park now, restore just before you return) | `gq.away(minutes=45)`, `gpuq away B 15:00`, `POST /api/away/{session}` |
| Snooze a warning (3 a day) | `gq.snooze()`, `gpuq snooze B`, Snooze button on the dashboard |
| Notifications (phone, Discord, Slack) | `notify.webhooks` in config.yaml; in-app inbox always on |
| Queue ETA | shown in `gpuq status`, the dashboard queue, and `status.queue[].eta_s` |
| Why was I parked? | `gpuq why s3`, `GET /api/sessions/{sid}/timeline` |
| Memory right-sizing tips | `gpuq status` (TIPS), `session.tip` in the API |
| Spill to disk after long parks | `parker.spill_after_s` in config.yaml |
| Deadline mode | `policy.schedules` in config.yaml, or `gpuq mode exam --idle 600 --grace 120` / `gpuq mode off` |
| Impact stats and leaderboard | `gpuq stats`, `GET /api/users/{user}`, `GET /api/leaderboard` |
| Dry-run report for lab admins | run the daemon with `--dry-run`, then `gpuq dryrun-report` |
| Live dashboard updates | `GET /api/stream` (server-sent events) |

Building a frontend? Everything is documented in [API.md](API.md), and FastAPI serves interactive docs at `/docs`.

## Using it from your own code

```python
from baton.client.gpuq_client import GpuqClient
gq = GpuqClient(user="alice").connect()          # waits for a slot, caps memory to it
model = gq.track(MyNet().cuda())
opt = gq.track(torch.optim.Adam(model.parameters()))
for batch in loader:
    with gq.step():                               # never parked mid-step; restores automatically if parked
        train_step(model, opt, batch)
```

## Tests

```powershell
.venv\Scripts\python -m pytest tests -q            # 74 tests; tests/gpu needs the GPU (~30 s)
```

## Configuration

Everything lives in [config.yaml](config.yaml): idle window, grace period, utilization threshold, slot
count and cap, whitelist, dry-run mode, queue weights, parker kind, simulator settings. Any key can be
overridden on the daemon's command line: `--set policy.idle_window_s=1800`.

## Honest limitations

- Cooperative parking needs students to import `gpuq_client`. The `cuda-checkpoint` parker (Linux, driver
  570+) removes that requirement; it is a tested stub here because the tool doesn't run on Windows.
- About 100 MiB of CUDA context stays on the GPU per parked process. Only the driver-level parker frees it.
- The simulator's notebook workload is synthetic (calibrated to NotebookOS's published statistics), and the
  lab is simulated. The live demo is one laptop GPU acting as a 2-slot server, not a real lab.

## Layout

```
baton/
  daemon/      monitor.py (NVML), policy.py, slots.py, queue.py, core.py, server.py (TCP), state.py (SQLite),
               eta.py, notify.py, tips.py, schedule.py, explain.py, stats.py, dryrun.py
  parkers/     base.py (interface), cooperative.py, cuda_checkpoint.py (Linux-only stub)
  client/      gpuq_client.py (students import this), tensor_parking.py, autotrack.py, bench_parking.py
  jupyter.py   %load_ext baton.jupyter
  minilab/     students.py (A-D), launcher.py, scenario.py
  simulator/   workload.py, policies.py, sim.py, report.py
  dashboard/   app.py, preview.py, static/
  cli/         gpuq.py
  protocol.py, config.py, runtime.py (PID tracking + safe cleanup)
docs/          brief.md, how-it-works.md, feasibility scripts from the laptop check
results/       simulator tables + charts, park benchmark, scenario logs
tests/         unit tests; tests/gpu needs the GPU
```
