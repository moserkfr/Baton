"""Check 5 worker: cap to CAP_GIB via memory fraction, fill to cap, wait, then try to exceed."""
import sys, os, torch
name, cap_gib = sys.argv[1], float(sys.argv[2])
total = torch.cuda.get_device_properties(0).total_memory
torch.cuda.set_per_process_memory_fraction(cap_gib * 2**30 / total)
def say(*a): print(f"[{name} pid={os.getpid()}]", *a, flush=True)
chunks, step = [], 128 * 2**20
while True:
    try:
        chunks.append(torch.ones(step // 4, device="cuda"))
    except torch.OutOfMemoryError:
        break
chunks.pop(); held = torch.cuda.memory_allocated() / 2**30  # leave 128 MiB headroom for kernels
free, _ = torch.cuda.mem_get_info()
say(f"FILLED allocated={held:.2f} GiB reserved={torch.cuda.memory_reserved()/2**30:.2f} GiB device_free={free/2**20:.0f} MiB")
import time
def bw():
    torch.cuda.synchronize(); t=time.perf_counter()
    for _ in range(3):
        for c in chunks: c.sum()
    torch.cuda.synchronize(); return 3*held/(time.perf_counter()-t)
say(f"BW alone-or-first: {bw():.0f} GiB/s over held data")
sys.stdin.readline()  # wait for parent "go" while both slots are full
say(f"BW with both slots full: {bw():.0f} GiB/s")
try:
    extra = torch.ones(512 * 2**20 // 4, device="cuda")
    say("EXCEED: 512 MiB extra alloc SUCCEEDED (cap not enforced!)")
except torch.OutOfMemoryError as e:
    say("EXCEED: clean torch.OutOfMemoryError ->", str(e).split(".")[0][:110])
# still usable after the OOM? free one chunk and compute
chunks.pop(); torch.cuda.empty_cache()
a = torch.randn(1024, 1024, device="cuda"); s = (a @ a).sum().item()
say(f"POST-OOM matmul ok (sum={s:.1f}), checksum of held data ok={all(bool(c[0] == 1) for c in chunks[:3])}")
