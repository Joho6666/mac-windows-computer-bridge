from __future__ import annotations

import json
import math
import platform
import statistics
import time

import torch


def synchronize(device: str) -> None:
    if device == "cuda":
        torch.cuda.synchronize()
    elif device == "mps":
        torch.mps.synchronize()


def main() -> None:
    if torch.cuda.is_available():
        device = "cuda"
        device_name = torch.cuda.get_device_name(0)
        free_memory, total_memory = torch.cuda.mem_get_info()
        memory_limit = min(int(free_memory * 0.70), 4 * 1024**3)
        initial_memory = torch.cuda.memory_allocated()
        torch.cuda.reset_peak_memory_stats()
    elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        device = "mps"
        device_name = torch.backends.mps.get_name()
        memory_limit = 2 * 1024**3
        initial_memory = torch.mps.current_allocated_memory()
        total_memory = None
    else:
        print(json.dumps({"host": platform.node(), "torch": torch.__version__, "device": None,
                          "cuda_available": torch.cuda.is_available(),
                          "mps_built": bool(getattr(torch.backends, "mps", None) and torch.backends.mps.is_built()),
                          "mps_available": bool(getattr(torch.backends, "mps", None) and torch.backends.mps.is_available())}, ensure_ascii=False))
        return

    matrices = []
    for size in (512, 1024, 2048, 4096, 8192):
        started = time.monotonic()
        try:
            left = torch.ones((size, size), device=device, dtype=torch.float32)
            right = torch.ones((size, size), device=device, dtype=torch.float32)
            output = torch.mm(left, right)
            synchronize(device)
            if not math.isclose(float(output[0, 0].item()), float(size), rel_tol=0, abs_tol=0.01):
                raise AssertionError(f"matrix result mismatch at size {size}")
            timings = []
            for _ in range(3):
                tick = time.monotonic()
                output = torch.mm(left, right)
                synchronize(device)
                timings.append(time.monotonic() - tick)
            peak = torch.cuda.max_memory_allocated() - initial_memory if device == "cuda" else torch.mps.current_allocated_memory() - initial_memory
            if peak > memory_limit:
                matrices.append({"size": size, "status": "memory_limit", "peak_bytes": peak, "limit_bytes": memory_limit})
                del left, right, output
                if device == "cuda":
                    torch.cuda.empty_cache()
                else:
                    torch.mps.empty_cache()
                break
            matrices.append({"size": size, "status": "passed", "seconds_median": statistics.median(timings),
                             "seconds_max": max(timings), "peak_additional_bytes": peak})
            del left, right, output
            if device == "cuda":
                torch.cuda.empty_cache()
            else:
                torch.mps.empty_cache()
        except (RuntimeError, AssertionError) as error:
            matrices.append({"size": size, "status": "failed", "error": str(error)[:500], "seconds": time.monotonic() - started})
            if device == "cuda":
                torch.cuda.empty_cache()
            else:
                torch.mps.empty_cache()
            break
        if time.monotonic() - started > 120:
            matrices[-1]["status"] = "time_limit"
            break

    batch = 64
    model = torch.nn.Sequential(torch.nn.Linear(1024, 2048), torch.nn.ReLU(), torch.nn.Linear(2048, 1024)).to(device)
    optimizer = torch.optim.SGD(model.parameters(), lr=0.01)
    source = torch.ones((batch, 1024), device=device)
    target = torch.zeros((batch, 1024), device=device)
    synchronize(device)
    losses = []
    started = time.monotonic()
    for _ in range(10):
        optimizer.zero_grad(set_to_none=True)
        loss = torch.nn.functional.mse_loss(model(source), target)
        loss.backward()
        optimizer.step()
        synchronize(device)
        losses.append(float(loss.item()))
    neural = {"steps": len(losses), "seconds": time.monotonic() - started, "initial_loss": losses[0],
              "final_loss": losses[-1], "finite": all(math.isfinite(value) for value in losses)}
    print(json.dumps({"host": platform.node(), "torch": torch.__version__, "device": device,
                      "device_name": device_name, "memory_limit_bytes": memory_limit,
                      "total_gpu_memory_bytes": total_memory, "matrix": matrices, "neural": neural}, ensure_ascii=False))


if __name__ == "__main__":
    main()
