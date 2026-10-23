import subprocess, sys, os, time, pynvml as N
HERE = os.path.dirname(os.path.abspath(__file__))
cap = sys.argv[1] if len(sys.argv) > 1 else "2.5"
N.nvmlInit(); h = N.nvmlDeviceGetHandleByIndex(0)
used = lambda: N.nvmlDeviceGetMemoryInfo(h).used // 2**20
print("device used before:", used(), "MiB")
procs = []
for name in ("slotA", "slotB"):
    p = subprocess.Popen([sys.executable, os.path.join(HERE, "slot_worker.py"), name, cap],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    print(p.stdout.readline().rstrip()); procs.append(p)
    print("  device used now:", used(), "MiB")
print("both slots full simultaneously; device used:", used(), "MiB of", N.nvmlDeviceGetMemoryInfo(h).total // 2**20)
print(subprocess.run(["powershell","-NoProfile","-Command",
  r"(Get-Counter '\GPU Adapter Memory(*)\Dedicated Usage','\GPU Adapter Memory(*)\Shared Usage').CounterSamples | ? CookedValue -gt 0 | % { '{0}  {1:N0} MiB' -f $_.Path, ($_.CookedValue/1MB) }"],
  capture_output=True, text=True).stdout)
for p in procs:
    p.stdin.write("go\n"); p.stdin.flush()
for p in procs:
    out, _ = p.communicate(timeout=60); print(out.rstrip()); print("  exit code", p.returncode)
time.sleep(1); print("device used after:", used(), "MiB")
