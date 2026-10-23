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

## Helper library and cooperative parker (Phase 2)

## Daemon (Phase 3)

## Mini-lab and demo scenario (Phase 4)

## Dashboard (Phase 5)

## cuda-checkpoint parker and CLI (Phase 6)
