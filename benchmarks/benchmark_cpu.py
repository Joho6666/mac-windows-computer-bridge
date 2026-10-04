from __future__ import annotations

import json
import os
import platform
import statistics
import time

os.environ.setdefault("OMP_NUM_THREADS", "4")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "4")
os.environ.setdefault("MKL_NUM_THREADS", "4")

import numpy as np


def main() -> None:
    rng = np.random.default_rng(20261004)
    source = rng.standard_normal(5_000_000, dtype=np.float32)
    started = time.monotonic()
    processed = np.sort(source)
    data_processing = {
        "elements": int(processed.size),
        "seconds": time.monotonic() - started,
        "checksum": float(processed[:100].sum(dtype=np.float64) + processed[-100:].sum(dtype=np.float64)),
    }
    del source, processed

    matrices = []
    for size in (512, 1024, 2048, 4096, 6144, 8192):
        started = time.monotonic()
        try:
            left = rng.standard_normal((size, size), dtype=np.float32)
            right = rng.standard_normal((size, size), dtype=np.float32)
            timings = []
            for _ in range(3):
                tick = time.monotonic()
                result = left @ right
                timings.append(time.monotonic() - tick)
                if not np.isfinite(result).all():
                    raise ArithmeticError("non-finite result")
                if time.monotonic() - started > 120:
                    break
            matrices.append({
                "size": size,
                "status": "passed" if time.monotonic() - started <= 120 else "time_limit",
                "seconds_median": statistics.median(timings),
                "seconds_max": max(timings),
                "peak_input_result_bytes": int(left.nbytes + right.nbytes + result.nbytes),
                "checksum": float(result[0, :100].sum(dtype=np.float64)),
            })
            del left, right, result
            if matrices[-1]["status"] == "time_limit":
                break
        except (MemoryError, ArithmeticError) as error:
            matrices.append({"size": size, "status": "failed", "error": str(error)})
            break

    print(json.dumps({
        "host": platform.node(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "numpy": np.__version__,
        "data_processing": data_processing,
        "matrix": matrices,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
