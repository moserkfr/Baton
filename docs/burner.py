"""Test GPU process: allocates N GB and alternates matmul bursts. Args: gb seconds [busy_frac]"""
import sys, time, os, torch
gb = float(sys.argv[1]); secs = float(sys.argv[2]); busy = float(sys.argv[3]) if len(sys.argv) > 3 else 1.0
x = torch.empty(int(gb * 2**30 // 4), dtype=torch.float32, device="cuda").fill_(1.0)
a = torch.randn(2048, 2048, device="cuda")
print(f"READY pid={os.getpid()}", flush=True)
end = time.time() + secs
while time.time() < end:
    t0 = time.time()
    while time.time() - t0 < busy:
        a = (a @ a).clamp_(-1, 1)
    torch.cuda.synchronize()
    if busy < 1.0:
        time.sleep(1.0 - busy)
print("DONE", flush=True)
