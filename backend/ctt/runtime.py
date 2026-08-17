"""CPU scheduling for the translation job.

The pipeline is CPU-bound and long-running: a chapter occupies every core for
hours. That is fine on an idle machine and miserable if you want to use it for
anything else meanwhile.

Priority, not thread count, is the lever that matters. On this 6-core i5 the
translator already runs `n_threads=6` (llama.cpp is fastest at one thread per
*physical* core), so cutting threads costs throughput immediately. Lowering
priority costs almost nothing while the machine is idle -- the scheduler only
takes cycles away when something else actually wants them.

Memory is not adjustable in any useful way: measured across n_ctx 512..4096 the
resident set moved 6383MB -> 6594MB, so essentially all of it is the model
weights. Wanting a smaller footprint means wanting a smaller model.
"""

from __future__ import annotations

import logging
import os
import platform
from dataclasses import dataclass

log = logging.getLogger(__name__)

PROFILES = ("performance", "balanced", "background")


@dataclass(frozen=True)
class Profile:
    """How much of the machine a run may take."""

    name: str
    nice: int
    """Higher is politer. Mapped to Windows priority classes below."""
    thread_fraction: float
    """Share of physical cores to use, when the caller asks for a thread count."""
    description: str


# Wall time and CPU below are measured, not estimated: one llama.cpp
# translation of three lines on a 6-core i5-11600KF, percentages against the
# 12 logical cores psutil reports.
#
#   performance  6 threads  19.9s  795% avg
#   balanced     6 threads  19.8s  795% avg
#   background   3 threads  20.4s  605% avg
#
# The surprise is `background`: halving the threads costs 2.5% of the wall
# time while giving back a quarter of the CPU. This workload is bound by
# memory bandwidth rather than arithmetic, so the extra cores were mostly
# waiting on RAM anyway.
_PROFILES: dict[str, Profile] = {
    "performance": Profile(
        "performance", nice=0, thread_fraction=1.0,
        description="全速、普通优先级。挂机跑用这个",
    ),
    "balanced": Profile(
        "balanced", nice=10, thread_fraction=1.0,
        description="线程数不变，只降优先级。空闲时和全速一样快（实测 19.8s vs 19.9s），"
                    "你要用机器时系统会先让给你",
    ),
    "background": Profile(
        "background", nice=19, thread_fraction=0.5,
        description="只用一半核心 + 最低优先级。实测只慢 2.5%（20.4s）却少占 24% CPU——"
                    "这个负载卡在内存带宽上，多给核心也是在等内存。打游戏建议选这个",
    ),
}


def get_profile(name: str) -> Profile:
    return _PROFILES.get(name, _PROFILES["balanced"])


def physical_cores() -> int:
    try:
        import psutil

        return psutil.cpu_count(logical=False) or os.cpu_count() or 4
    except ImportError:
        # Logical count halved is the right guess on any hyper-threaded chip,
        # and harmless on one without.
        return max(1, (os.cpu_count() or 4) // 2)


def threads_for(profile_name: str) -> int:
    """Thread count for llama.cpp under a profile.

    Based on *physical* cores. llama.cpp gains nothing from hyper-threads and
    often loses to the extra contention.
    """
    profile = get_profile(profile_name)
    return max(1, round(physical_cores() * profile.thread_fraction))


def apply_priority(profile_name: str) -> str:
    """Lower this process's scheduling priority. Returns what was applied.

    Applies to the whole process, not one thread: llama.cpp and ONNX Runtime
    both spawn their own worker pools, and those workers are what actually
    consume the cores. A thread-level change would miss all of them.
    """
    profile = get_profile(profile_name)

    try:
        import psutil

        process = psutil.Process()
        if platform.system() == "Windows":
            classes = {
                0: psutil.NORMAL_PRIORITY_CLASS,
                10: psutil.BELOW_NORMAL_PRIORITY_CLASS,
                19: psutil.IDLE_PRIORITY_CLASS,
            }
            target = classes.get(profile.nice, psutil.BELOW_NORMAL_PRIORITY_CLASS)
            process.nice(target)
            # Deprioritise disk I/O too. Page images are several MB each, and
            # on a busy machine that is enough to make a game stutter.
            try:
                process.ionice(psutil.IOPRIO_LOW if profile.nice else psutil.IOPRIO_NORMAL)
            except (AttributeError, OSError):
                pass
            return f"{profile.name} (priority class {target})"

        process.nice(profile.nice)
        return f"{profile.name} (nice {profile.nice})"

    except ImportError:
        if hasattr(os, "nice") and profile.nice:
            os.nice(profile.nice)
            return f"{profile.name} (os.nice {profile.nice})"
        log.info("psutil not installed; leaving priority alone")
        return "unchanged"
    except Exception as exc:  # noqa: BLE001 - never fail a run over priority
        log.warning("could not set priority: %s", exc)
        return "unchanged"


def describe() -> list[dict]:
    """Profile list for the settings UI."""
    return [
        {
            "name": p.name,
            "threads": threads_for(p.name),
            "description": p.description,
        }
        for p in _PROFILES.values()
    ]
