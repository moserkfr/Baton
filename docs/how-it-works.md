# How Baton works

Written for the student presenting this to judges. Each section says what a component does, why it is
built this way, and the trade-offs you should be ready to defend.

## The big picture

A shared lab GPU server has a problem: students start a notebook, grab a GPU, run a two-minute training
job, then spend the next hour editing code, at lunch, or gone home, while the GPU stays reserved. Nobody
else can use it. Baton is one small program (a daemon) per GPU machine that:

1. **Watches** real GPU activity per process (not web traffic).
2. **Decides** when a session is idle: it holds GPU memory but has done no compute for a while.
3. **Warns** the owner, then after a grace period **parks** the session: its GPU memory is copied to
   host RAM and the GPU is freed, but the process keeps running with all its Python variables.
4. **Hands** the freed GPU to the next person in a **fair queue**.
5. **Restores** the parked session when its owner comes back, with every tensor and optimizer state intact.

There are three layers to the demo: a simulator (lab-scale numbers), a live mini-lab on one laptop GPU
(proof the mechanism works), and a dashboard.

_Sections below are filled in phase by phase._

## Simulator (Phase 1)

**What it does.** It replays the *same* week of lab demand under four policies and counts what each
one costs. Code: `baton/simulator/` (`workload.py` makes the demand, `policies.py` describes
the policies, `sim.py` is the event engine, `report.py` runs the experiments). Results:
`results/sim_results.md` plus charts. Run it with `python -m baton.simulator.report`.

**Why a simulator at all?** One laptop GPU can prove the mechanism works, but not what it is worth to
a lab of 40 students. No public trace records notebook sessions, so we build them from the statistics
NotebookOS published, and borrow real batch-job arrivals and lengths from the SenseTime Helios trace.

### How a notebook session is modelled
A session is a list of *segments* the student goes through once they have a GPU:

- **setup gap** (about 3 min): open the notebook, load data and model onto the GPU;
- **work sittings**: tasks (GPU busy) separated by short gaps while the student edits code;
- **breaks** between sittings (about 50 min: lunch, a lecture);
- a **tail** before logout. Some sessions (12%) are left open overnight (+14 h).
- 66% of sessions never run a task at all: the notebook is opened, the model is loaded, and the
  student never trains. NotebookOS reports that "nearly 70%" of reserved GPUs were never used.

Gaps are tagged *attended* (the student is at the notebook, so a web-activity culler sees activity)
or *unattended* (the student is away). This tag is what separates the activity culler (watches web
traffic) from GPU-aware policies (watch GPU compute): editing code for 40 minutes without running
anything looks busy to the culler but idle to us.

If a student must wait for a GPU, their whole session shifts later: they can't do GPU work early.

### Calibration (defend these numbers)
Task lengths and gaps are log-normal (the usual shape for "mostly short, a few long" durations).
The few free parameters were tuned until the generated sessions matched NotebookOS:

| Statistic | NotebookOS | Ours |
| --- | --- | --- |
| median task | 2 min | 2.0 min |
| tasks < 5 min | 75% | 76% |
| median gap between tasks | 5 min | 5.0 min |
| gaps < 8 min | 75% | 76% |
| sessions GPU-active <= 5% of lifetime | 74-75% | 72-74% |
| sessions GPU-active <= 31% of lifetime | 90% | 91% |
| sessions never active | "nearly 70%" | 67% |

The exact table regenerates in `results/sim_results.md`. `tests/test_simulator.py` fails if the
generator drifts away from these targets.

### Assumptions you should say out loud
1. **Lab size and demand.** 4 machines x 2 GPUs, 40 students, 5 working days. Each student has their
   own session rate (gamma-distributed, mean 0.45 sessions/day), so some students are heavy users.
   Arrivals fall between 9:00 and 17:00, peaking around midday.
2. **Batch jobs** are 1-GPU jobs sampled from Helios (Venus cluster: 49,640 jobs that ran >= 1 min),
   12 per day, capped at 12 h (Helios has two-week jobs that make no sense for a student lab). They
   compute the whole time, so no idle policy touches them: they are background load.
3. **One flat pool of GPUs.** Which machine a GPU is on doesn't change any metric we report.
4. **Same fair queue for every policy**, so differences come from the reclaim policy, not the
   queue. Real small labs often have no queue at all (people email), so this flatters the baselines.
5. **Killed sessions cost a re-run.** When the culler or the cancel policy kills a session and the
   student comes back, they request a GPU again and re-run every task they had completed (re-executing
   notebook cells to rebuild state). That is the "GPU-h re-running lost work" column. Students who never
   come back still count as "lost state", but cost no re-run.
6. **Park/restore times come from the laptop**: 1.2 s park, 0.27 s restore. A parked owner who
   returns while a GPU is free waits only the restore. If no GPU is free, they queue, with priority
   for the first 2 minutes of waiting.
7. **Our policy** parks after 30 min of zero GPU compute plus a 5-minute grace after the warning.
   **The activity culler** kills after 30 min with no web activity (jupyterhub-idle-culler style).
   **GPU-aware cancel** warns at 45 and cancels at 90 min of zero compute (Princeton's example config).
8. **Waits** count every GPU request: first requests and returns after a park or kill. We average 5
   random seeds, because single runs of a queue are noisy.

### What the results say (and don't)
- Park-and-restore reclaims almost as many GPU-hours as the aggressive culler (about 316 vs 322 a
  week), and **no session loses state**, against 70 of 84 for the culler and 48 of 84 for GPU-aware
  cancel. The culler reclaims slightly more only because it acts at 30 min while we act at 35 (30 + 5 min
  grace after the warning): about 73 parks x 5 min = 6 GPU-hours, exactly the gap.
- Against hold-until-logout, the p95 wait for a GPU drops from about 4.4 hours to about zero.
- **Load sweep:** hold-until-logout waits blow up as demand grows (a queue near capacity behaves like
  this), while park-and-restore stays near zero at all three load levels. The headline uses the middle level.
- **Idle-window sweep:** a 10-minute window reclaims more but warns students who are just thinking
  between runs ("warned, then resumed" goes from 1 to 15 a week). 30 minutes is the sensible default.
- **Fairness (Jain's index over GPU-hours held)** barely moves (about 0.46 for both). Students have
  different demand by design, so equal GPU-hours isn't the goal. Don't oversell fairness; the
  wait-time and lost-state columns are the strong claims.
- **The honest caveat:** the notebook workload is synthetic (calibrated, not replayed), and the lab is
  simulated. Say so up front, as the brief recommends.

## Helper library and cooperative parker (Phase 2)

**What it does.** `gpuq_client` is the small library a student's script imports. It registers with
the daemon, caps the process's GPU memory to its slot, reports memory use every second, and obeys
`park` and `resume` messages. Code: `baton/client/` (`tensor_parking.py` moves the tensors,
`gpuq_client.py` does the talking), and the shared wire format is in `baton/protocol.py`.

**How parking works, step by step.**
1. The student tells the library what lives on the GPU: `gq.track(model)`, `gq.track(optimizer)`,
   `gq.track(data_tensor)`. From those, the parker finds every parameter, gradient, buffer (e.g.
   BatchNorm statistics) and optimizer state tensor.
2. On `park`, for every tensor *that is on the GPU*, its bytes are copied into a pinned (page-locked)
   host buffer. Then the tensor is re-pointed at that buffer with `t.data = buffer`. This is the key
   trick: the tensor *object* stays the same, so the student's variables, the model and the optimizer
   all still work; they just point at host memory now.
3. GPU memory is released: garbage collection, cuBLAS's workspace is freed, and `torch.cuda.empty_cache()`
   hands the memory back to the driver so another process can use it.
4. On `resume`, each tensor gets a fresh GPU allocation on its **original device**, the bytes are
   copied back, and the tensor is re-pointed again.

**Why these choices.**
- *Original device, not "everything to cuda".* Adam keeps its `step` counter on the CPU on purpose.
  Moving it to the GPU breaks `optimizer.step()`. We only move what was on a GPU and remember where it was.
- *Reused pinned buffers.* Copies from pinned memory are fast (DMA), but allocating pinned memory is
  slow. So buffers are allocated once (first park, or up front with `prealloc()`) and reused. The cost:
  that host RAM stays reserved for the student even while they run.
- *Parking only at a safe point.* GPU work happens inside `with gq.step(): ...`. Park runs on a
  background thread and takes the same lock as `step()`, so it waits for a running step to finish and
  never moves a tensor while a kernel uses it. It has to be a background thread: an idle student never
  reaches the next step, so their main thread would never get a chance to park.
- *Coming back is automatic.* Calling `gq.step()` while parked sends `request` to the daemon and
  blocks until `resume` arrives. The student's code doesn't change at all.
- *Self-reported PID and memory.* On Windows a venv's `python.exe` is a launcher, so the PID a parent
  sees is wrong; the client sends `os.getpid()`. NVML can't report per-process memory under WDDM, so
  the client reports `torch.cuda.memory_reserved()`.
- *Fail open.* If the daemon dies, the client restores its tensors and keeps working. A broken
  Baton must never block a student.

**Measured** (`results/park_bench.md`, RTX 3050 Laptop):

| State | First park (incl. pinned alloc) | Park, reused buffers | Restore |
| --- | --- | --- | --- |
| 0.56 GiB | 0.54 s | 0.26 s | 0.08 s |
| 1.06 GiB | 0.87 s | 0.30 s | 0.12 s |
| 2.06 GiB | 1.74 s | 0.42 s | 0.25 s |

While parked, device memory drops to the CUDA context (~87 MiB) plus ~18 MiB. Checksums match after
every round trip, and training after a park/restore produces **bit-identical** losses to training
without one (`tests/gpu/test_tensor_parking.py`).

**Limits to say out loud.** Only tracked tensors move. A GPU tensor the student never registered stays
on the GPU and still counts against memory (the daemon sees it in the memory report). Views that share
storage with a tracked tensor are not handled specially. The CUDA context stays; only the driver-level
`cuda-checkpoint` parker can free that.

**The daemon-side interface** (`baton/parkers/base.py`): `park(session)` and
`resume(session, slot, cap_fraction)`. A parker returns either a finished result or "pending" (the
cooperative parker just sends a message and the client's `parked`/`resumed` reply completes it). That
lets the subprocess-based `cuda-checkpoint` parker plug in unchanged.

## Daemon (Phase 3)

## Mini-lab and demo scenario (Phase 4)

## Dashboard (Phase 5)

## cuda-checkpoint parker and CLI (Phase 6)
