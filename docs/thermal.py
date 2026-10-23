"""Check 10: 60 s full GPU load; sample temp/clocks/power/throttle reasons every 3 s; time a fixed matmul batch."""
import threading, time, psutil, pynvml as N, torch
b = psutil.sensors_battery()
print("battery:", None if b is None else f"{b.percent}% plugged_in={b.power_plugged}")
N.nvmlInit(); h = N.nvmlDeviceGetHandleByIndex(0)
reasons = {getattr(N, k): k.replace("nvmlClocksEventReason", "").replace("nvmlClocksThrottleReason", "") for k in dir(N)
           if (k.startswith("nvmlClocksEventReason") or k.startswith("nvmlClocksThrottleReason")) and k[-4:] not in ("None", "_All") and not k.endswith("All")}
print("power limit:", N.nvmlDeviceGetEnforcedPowerLimit(h) / 1000, "W | max gfx clock:", N.nvmlDeviceGetMaxClockInfo(h, N.NVML_CLOCK_GRAPHICS), "MHz",
      "| slowdown temp:", N.nvmlDeviceGetTemperatureThreshold(h, N.NVML_TEMPERATURE_THRESHOLD_SLOWDOWN), "C")
stop, tput = False, []
def load():
    a = torch.randn(4096, 4096, device="cuda"); b = torch.randn(4096, 4096, device="cuda")
    while not stop:
        t = time.perf_counter()
        for _ in range(10): c = a @ b
        torch.cuda.synchronize(); tput.append((time.time(), 10 * 2 * 4096**3 / (time.perf_counter() - t) / 1e12))
threading.Thread(target=load, daemon=True).start()
t0 = time.time()
print(" t(s) temp(C) gfx(MHz) mem(MHz) power(W) util%  TFLOPS  active clock-limit reasons")
while time.time() - t0 < 60:
    time.sleep(3)
    try: r = N.nvmlDeviceGetCurrentClocksEventReasons(h)
    except AttributeError: r = N.nvmlDeviceGetCurrentClocksThrottleReasons(h)
    active = sorted({v for k, v in reasons.items() if r & k}) or ["none"]
    recent = [x for ts, x in tput if ts > time.time() - 3]
    print(f"{time.time()-t0:5.0f} {N.nvmlDeviceGetTemperature(h, N.NVML_TEMPERATURE_GPU):6d} {N.nvmlDeviceGetClockInfo(h, N.NVML_CLOCK_GRAPHICS):8d} "
          f"{N.nvmlDeviceGetClockInfo(h, N.NVML_CLOCK_MEM):8d} {N.nvmlDeviceGetPowerUsage(h)/1000:8.1f} {N.nvmlDeviceGetUtilizationRates(h).gpu:5d} "
          f"{(sum(recent)/len(recent) if recent else 0):7.2f}  {','.join(active)}")
stop = True; time.sleep(1)
first = [x for ts, x in tput if ts < t0 + 10]; last = [x for ts, x in tput if ts > t0 + 50]
print(f"throughput first 10 s {sum(first)/len(first):.2f} TFLOPS, last 10 s {sum(last)/len(last):.2f} TFLOPS -> drop {100*(1-sum(last)/len(last)/(sum(first)/len(first))):.1f}%")
