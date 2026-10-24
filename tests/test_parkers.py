"""Parker plug-ins: the cooperative parker's messages and the cuda-checkpoint stub's commands."""
import subprocess
import sys

import pytest

from baton.parkers.base import ParkerError, make_parker
from baton.parkers.cooperative import CooperativeParker
from baton.parkers.cuda_checkpoint import CudaCheckpointParker


class Sess:
    sid, pid, has_client = "s1", 4242, False

    def __init__(self):
        self.sent = []

    def send(self, msg):
        self.sent.append(msg)


def test_cooperative_parker_sends_messages_and_is_async():
    s = Sess()
    p = CooperativeParker()
    assert p.park(s) is None and s.sent[-1] == {"type": "park"}
    assert p.resume(s, 1, 0.367) is None and s.sent[-1]["type"] == "resume" and s.sent[-1]["slot"] == 1
    assert not p.can_park(s)  # no client -> cooperative parking impossible


@pytest.mark.skipif(sys.platform.startswith("linux"), reason="checks the non-Linux refusal")
def test_cuda_checkpoint_refuses_clearly_on_this_os():
    with pytest.raises(ParkerError, match="not supported on this OS"):
        CudaCheckpointParker()
    with pytest.raises(ParkerError):
        make_parker("cuda_checkpoint")


def test_cuda_checkpoint_builds_the_right_commands():
    calls, state = [], {"v": "running"}

    def runner(cmd):
        calls.append(cmd)
        if "--get-state" in cmd:
            return subprocess.CompletedProcess(cmd, 0, state["v"] + "\n", "")
        state["v"] = "checkpointed" if state["v"] == "running" else "running"
        return subprocess.CompletedProcess(cmd, 0, "", "")

    p = CudaCheckpointParker(runner=runner, platform="linux")
    s = Sess()
    assert p.can_park(s)  # transparent: no helper library needed
    r = p.park(s)
    assert r.ok and calls[-1] == ["cuda-checkpoint", "--toggle", "--pid", "4242"]
    assert calls[-2] == ["cuda-checkpoint", "--get-state", "--pid", "4242"]
    assert not p.park(s).ok  # already checkpointed
    assert p.resume(s, 0, 0.4).ok and state["v"] == "running"


def test_unknown_parker_kind():
    with pytest.raises(ValueError):
        make_parker("magic")
