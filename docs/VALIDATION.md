# Validation status

## Verified in the originating setup

- Mac and Windows workers were reachable through the configured MCP/SSH bridge.
- Ten short Mac-to-Windows Python jobs completed with expected output.
- Ten short Windows-to-Mac jobs completed with expected output when initiated from the Windows-side MCP process.
- Small one-chunk Unicode file transfers passed in both directions in the earlier bridge setup.
- On a Mac M6-class machine, PyTorch MPS ran the ten-step MLP and matrix checks through 8192 × 8192 within the configured 2 GiB additional-allocation cap. A CPU matrix sweep also passed through 8192 × 8192.

These are individual setup observations, not guarantees for another machine. Hostnames and raw logs have intentionally been omitted.

## Not yet accepted end-to-end

- Windows CUDA benchmark and GPU memory ceiling.
- 1 MiB through 256 MiB transfer throughput, interrupted transfer/resume, and direct SCP comparison.
- Desktop text entry, save/readback, and screenshot correspondence in both directions.
- WPS calculation/PDF export; current UIA support needs app-specific verification.
- Multi-file office sequence, queue contention, cancellation process termination, and restart recovery.
- Locked-desktop and permission-revocation behavior.

Run the scripts in `benchmarks/` only in dedicated environments. For Windows install the official CUDA wheel appropriate to the installed PyTorch release, then run `benchmark_gpu.py`; for Mac install the matching PyTorch release and verify MPS availability. Consult [PyTorch's previous-version install matrix](https://pytorch.org/get-started/previous-versions/) for the exact wheel index; do not assume CUDA toolkit and wheel versions are interchangeable. Keep benchmarks bounded and stop if the machine is under active user load.
