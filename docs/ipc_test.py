"""Check 7: which daemon->student control channels work here, measured while the student runs GPU work."""
import signal, socket, threading, time, sys, json, statistics, http.server, urllib.request
from multiprocessing.connection import Listener, Client
import torch

print("POSIX signals present:", {s: hasattr(signal, s) for s in ("SIGUSR1", "SIGUSR2", "SIGSTOP", "SIGCONT")},
      "| Windows-only:", {s: hasattr(signal, s) for s in ("CTRL_C_EVENT", "CTRL_BREAK_EVENT")})

# fake student: GPU busy loop in main thread, control listener in a background thread
stop = False
def gpu_load():
    a = torch.randn(2048, 2048, device="cuda")
    while not stop: a = (a @ a).clamp_(-1, 1); torch.cuda.synchronize()
threading.Thread(target=gpu_load, daemon=True).start()

def bench(name, setup, call, n=200):
    try:
        cleanup = setup(); call(); lat = []
        for _ in range(n):
            t = time.perf_counter(); r = call(); lat.append((time.perf_counter() - t) * 1e3)
        assert r == "ok", r
        cleanup and cleanup()
        print(f"  [OK]   {name:28s} median {statistics.median(lat):.3f} ms  p99 {sorted(lat)[int(n*.99)]:.3f} ms")
    except Exception as e:
        print(f"  [FAIL] {name}: {type(e).__name__}: {e}")

# 1. TCP localhost, line protocol
def tcp_setup():
    srv = socket.create_server(("127.0.0.1", 0)); port = srv.getsockname()[1]
    def serve():
        c, _ = srv.accept(); f = c.makefile("rw")
        for line in f: f.write("ok\n"); f.flush()
    threading.Thread(target=serve, daemon=True).start()
    global tcp_f; s = socket.create_connection(("127.0.0.1", port)); s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1); tcp_f = s.makefile("rw")
def tcp_call(): tcp_f.write('{"cmd":"park"}\n'); tcp_f.flush(); return tcp_f.readline().strip()

# 2. Windows named pipe via multiprocessing.connection (AF_PIPE)
def pipe_setup():
    lst = Listener(r"\\.\pipe\gpucheck_ipc", family="AF_PIPE", authkey=b"k")
    def serve():
        c = lst.accept()
        while True:
            try: c.recv(); c.send("ok")
            except EOFError: break
    threading.Thread(target=serve, daemon=True).start()
    global pipe_c; pipe_c = Client(r"\\.\pipe\gpucheck_ipc", family="AF_PIPE", authkey=b"k")
def pipe_call(): pipe_c.send({"cmd": "park"}); return pipe_c.recv()

# 3. HTTP endpoint in the student (stdlib server, keep-alive off = worst case)
class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"])); b = b"ok"
        self.send_response(200); self.send_header("Content-Length", "2"); self.end_headers(); self.wfile.write(b)
    def log_message(self, *a): pass
def http_setup():
    global http_port; srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H); http_port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start(); return srv.shutdown
def http_call():
    req = urllib.request.Request(f"http://127.0.0.1:{http_port}/park", data=b'{"cmd":"park"}', method="POST")
    return urllib.request.urlopen(req).read().decode()

print("Round-trip latency while student keeps the GPU 100% busy:")
bench("TCP localhost (JSON lines)", tcp_setup, tcp_call)
bench("Named pipe (AF_PIPE)", pipe_setup, pipe_call)
bench("HTTP POST (stdlib server)", http_setup, http_call, n=100)
stop = True
