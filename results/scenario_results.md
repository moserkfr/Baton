# Mini-lab scenario results (live, RTX 3050 Laptop, 2 slots x 2.2 GiB)

Idle window 30 s, grace 10 s. Full timestamped logs: `scenario_run1.log`, `scenario_run2.log`. `scenario_failed_attempt1.log` is the first attempt, which exposed a real bug (queued clients sent no reports and were dropped as silent); fixed in gpuq_client before runs 1 and 2.

| Measurement | Run 1 | Run 2 |
| --- | --- | --- |
| VRAM baseline (MiB) | 308 | 308 |
| VRAM with A and B running (MiB) | 2205 | 2205 |
| B memory reserved (MiB, self-reported) | 1152 | 1152 |
| B warned after idle (s) | 31.0 | 30.9 |
| B park time (s) | 0.28 | 0.30 |
| B state moved to host RAM (GiB) | 0.63 | 0.63 |
| VRAM after B parked (MiB) | 1147 | 1147 |
| C waited in queue (s) | 33.2 | 33.3 |
| B checksum before park | 81504806dc5f37b2 | 4138ddb254d39e07 |
| B checksum while parked | 81504806dc5f37b2 | 4138ddb254d39e07 |
| B checksum after restore | 81504806dc5f37b2 | 4138ddb254d39e07 |
| Checksums match | True | True |
| B wait on return (s, until C finished) | 12.8 | 12.7 |
| B restore time (s) | 0.10 | 0.06 |
| Returning B served before D (who queued earlier) | True | True |
| B loss before park | 8.744 | 8.718 |
| B loss first step after restore | 8.522 | 8.555 |
| D waited (s) | 23.8 | 23.7 |
| VRAM at end (MiB) | 308 | 308 |
| VRAM back to baseline | True | True |
| Duration (s) | 113 | 112 |

Receipt (run 1): [{"beneficiary": "student-C", "owner": "student-B", "waited_s": 33.2, "saved_s": 9.7, "open": false}]

Parking B freed 1057 MiB of device memory; the remainder of B's footprint is its CUDA context, which only the driver-level cuda-checkpoint parker can release.
