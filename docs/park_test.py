"""Check 6: cooperative park (GPU->CPU, free VRAM) and restore, with checksums and timings."""
import time, hashlib, gc, subprocess, psutil, pynvml as N
N.nvmlInit(); h = N.nvmlDeviceGetHandleByIndex(0)
dev_used = lambda: N.nvmlDeviceGetMemoryInfo(h).used / 2**20
proc = psutil.Process()
rss = lambda: proc.memory_info().rss / 2**20
smi = lambda: subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"], capture_output=True, text=True).stdout.strip()

base_dev = dev_used()
import torch, torch.nn as nn
torch.zeros(1, device="cuda"); torch.cuda.synchronize()
ctx_dev = dev_used()
print(f"device used before CUDA init {base_dev:.0f} MiB; after context init {ctx_dev:.0f} MiB -> context ~{ctx_dev-base_dev:.0f} MiB")

def sha(t): return hashlib.sha256(t.detach().cpu().contiguous().numpy().tobytes()).hexdigest()[:16]

class State:  # what a helper library would own: every GPU tensor of the student
    def __init__(self, gb):
        g = torch.Generator(device="cuda").manual_seed(0)
        self.big = torch.randn(int(gb * 2**30 // 4), device="cuda", generator=g)
        self.model = nn.Sequential(nn.Linear(1024, 4096), nn.GELU(), nn.Linear(4096, 4096), nn.GELU(), nn.Linear(4096, 10)).cuda()
        self.opt = torch.optim.Adam(self.model.parameters()); self.step()  # optimizer state lives on GPU too
    def step(self):
        x = torch.randn(256, 1024, device="cuda"); loss = self.model(x).square().mean()
        self.opt.zero_grad(); loss.backward(); self.opt.step(); return loss.item()
    def tensors(self):  # big tensor, params, grads, optimizer state
        yield self, "big"
        for p in self.model.parameters():
            yield p, "data"
            if p.grad is not None: yield p, "grad"
        for st in self.opt.state.values():
            for k, v in st.items():
                if torch.is_tensor(v) and k != "step": yield st, k  # Adam keeps step on CPU by design
    def move(self, device, pin=False):
        for obj, attr in list(self.tensors()):
            t = obj[attr] if isinstance(obj, dict) else getattr(obj, attr)
            if device == "cuda":
                n = t.to("cuda", non_blocking=pin)
            elif pin:  # copy into page-locked host buffer (faster DMA both ways)
                n = torch.empty(t.shape, dtype=t.dtype, pin_memory=True); n.copy_(t, non_blocking=True)
            else:
                n = t.to("cpu")
            if isinstance(obj, dict): obj[attr] = n
            else: setattr(obj, attr, n)
        torch.cuda.synchronize()

def run(gb, pin=False, verbose=False):
    gc.collect(); torch.cuda.empty_cache()
    s = State(gb); torch.cuda.synchronize()
    ck_big, ck_w = sha(s.big), sha(s.model[0].weight)
    out_before = s.model(torch.ones(4, 1024, device="cuda")).sum().item()
    f0 = torch.cuda.mem_get_info()[0]; d0 = dev_used(); r0 = rss(); smi0 = smi()
    t = time.perf_counter(); s.move("cpu", pin); gc.collect(); torch.cuda.empty_cache(); torch.cuda.synchronize(); t_park = time.perf_counter() - t
    f1 = torch.cuda.mem_get_info()[0]; d1 = dev_used(); r1 = rss(); smi1 = smi()
    t = time.perf_counter(); s.move("cuda", pin); t_rest = time.perf_counter() - t
    ok = sha(s.big) == ck_big and sha(s.model[0].weight) == ck_w
    out_after = s.model(torch.ones(4, 1024, device="cuda")).sum().item()
    loss = s.step()  # training continues after restore (optimizer state intact)
    resid = d1 - ctx_dev
    print(f"{gb:>4} GB pin={pin!s:5} park {t_park*1000:6.0f} ms ({gb/t_park:5.2f} GB/s) | restore {t_rest*1000:6.0f} ms ({gb/t_rest:5.2f} GB/s) | "
          f"VRAM freed {(f1-f0)/2**20:6.0f} MiB (NVML {d0-d1:6.0f}, smi {smi0}->{smi1}) | RAM +{r1-r0:6.0f} MiB | "
          f"checksums ok={ok} forward same={abs(out_after-out_before)<1e-4} train step ok={loss==loss}")
    if verbose:
        print(f"      while parked: torch allocated={torch.cuda.memory_allocated()} B, NVML used above context baseline={resid:.0f} MiB")
    del s; gc.collect(); torch.cuda.empty_cache()

run(1.0, verbose=True)          # functional test a-c
for gb in (0.5, 1.0, 2.0):     # timings d
    run(gb)
for gb in (0.5, 1.0, 2.0):
    run(gb, pin=True)
gc.collect(); torch.cuda.empty_cache()
print(f"end: device used {dev_used():.0f} MiB (vs {base_dev:.0f} before CUDA init) -> unreleasable context ~{dev_used()-base_dev:.0f} MiB")
