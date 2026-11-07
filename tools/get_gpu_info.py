#!/usr/bin/env python3
"""Standalone GPU info script - copy and run on remote machine.

Usage on remote machine (192.168.100.2):
    python3 get_gpu_info.py
"""

import json
import sys

def get_gpu_info():
    """Return runtime GPU hardware availability and metadata."""
    info = {
        "available": False,
        "backend": "numpy",
        "device_name": None,
        "device_count": 0,
        "cuda_version": None,
        "total_memory_mb": None,
    }

    # Try CuPy first
    try:
        import cupy as cp
        count = int(cp.cuda.runtime.getDeviceCount())
        if count > 0:
            dev = cp.cuda.runtime.getDeviceProperties(0)
            raw_name = dev["name"] if isinstance(dev, dict) else getattr(dev, "name", "GPU")
            name = raw_name.decode("utf-8") if isinstance(raw_name, bytes) else str(raw_name)
            total_mem = dev.get("totalGlobalMem") if isinstance(dev, dict) else getattr(dev, "totalGlobalMem", None)
            info["available"] = True
            info["backend"] = "cupy"
            info["device_name"] = name
            info["device_count"] = count
            info["cuda_version"] = getattr(cp.cuda.runtime, "runtimeGetVersion", lambda: None)()
            if total_mem:
                info["total_memory_mb"] = round(float(total_mem) / (1024 * 1024), 1)
            try:
                mempool = cp.get_default_memory_pool()
                info["used_memory_mb"] = round(float(mempool.used_bytes()) / (1024 * 1024), 2)
                info["pool_memory_mb"] = round(float(mempool.total_bytes()) / (1024 * 1024), 2)
            except Exception:
                pass
            return info
    except Exception:
        pass

    # Try PyTorch CUDA
    try:
        import torch
        if bool(torch.cuda.is_available()) and int(torch.cuda.device_count()) > 0:
            info["available"] = True
            info["backend"] = "torch"
            info["device_name"] = str(torch.cuda.get_device_name(0))
            info["device_count"] = int(torch.cuda.device_count())
            return info
    except Exception:
        pass

    return info


if __name__ == "__main__":
    info = get_gpu_info()
    print(json.dumps(info, indent=2))
    sys.exit(0 if info["available"] else 1)
