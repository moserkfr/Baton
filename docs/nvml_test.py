"""Check 4: which NVML queries work, before and while our own GPU process runs."""
import subprocess, sys, time, os
import pynvml as N

HERE = os.path.dirname(os.path.abspath(__file__))

def tryq(name, fn):
    try:
        r = fn()
        print(f"  [OK]   {name}: {r}")
        return r
    except N.NVMLError as e:
        print(f"  [FAIL] {name}: {e}")
    except Exception as e:
        print(f"  [FAIL] {name}: {type(e).__name__}: {e}")

def fmt_procs(ps):
    return [(p.pid, None if p.usedGpuMemory is None else round(p.usedGpuMemory / 2**20)) for p in ps]

def snapshot(h, label):
    print(f"--- {label}")
    m = tryq("nvmlDeviceGetMemoryInfo (MiB used/total)", lambda: (N.nvmlDeviceGetMemoryInfo(h).used // 2**20, N.nvmlDeviceGetMemoryInfo(h).total // 2**20))
    tryq("nvmlDeviceGetUtilizationRates (gpu%, mem%)", lambda: (lambda u: (u.gpu, u.memory))(N.nvmlDeviceGetUtilizationRates(h)))
    tryq("nvmlDeviceGetComputeRunningProcesses (pid, MiB)", lambda: fmt_procs(N.nvmlDeviceGetComputeRunningProcesses(h)))
    tryq("nvmlDeviceGetGraphicsRunningProcesses (pid, MiB)", lambda: fmt_procs(N.nvmlDeviceGetGraphicsRunningProcesses(h)))
    tryq("nvmlDeviceGetProcessUtilization (pid, sm%, mem%)", lambda: [(s.pid, s.smUtil, s.memUtil) for s in N.nvmlDeviceGetProcessUtilization(h, 0)])
    if hasattr(N, "nvmlDeviceGetProcessesUtilizationInfo"):
        tryq("nvmlDeviceGetProcessesUtilizationInfo (v2 API)", lambda: [(s.pid, s.smUtil, s.memUtil) for s in N.nvmlDeviceGetProcessesUtilizationInfo(h, 0).procUtilArray])
    tryq("nvmlDeviceGetAccountingMode", lambda: N.nvmlDeviceGetAccountingMode(h))

N.nvmlInit()
print("NVML driver", N.nvmlSystemGetDriverVersion(), "NVML lib", N.nvmlSystemGetNVMLVersion())
h = N.nvmlDeviceGetHandleByIndex(0)
snapshot(h, "baseline (no test process)")

p = subprocess.Popen([sys.executable, os.path.join(HERE, "burner.py"), "1.0", "25", "0.6"], stdout=subprocess.PIPE, text=True)
print("started test process, line:", p.stdout.readline().strip(), "(expect ~1 GiB tensor + context, 60% busy)")
time.sleep(6)
snapshot(h, f"with test process pid={p.pid} running")
# sample per-process util a few times to see if the 60% duty cycle is visible
print("--- per-process util samples over ~5 s:")
for _ in range(5):
    time.sleep(1)
    try:
        s = [(x.pid, x.smUtil) for x in N.nvmlDeviceGetProcessUtilization(h, 0)]
    except N.NVMLError as e:
        s = f"err {e}"
    print("  ", s, " device util:", N.nvmlDeviceGetUtilizationRates(h).gpu)
p.kill(); p.wait()
time.sleep(2)
snapshot(h, "after killing test process")
N.nvmlShutdown()
