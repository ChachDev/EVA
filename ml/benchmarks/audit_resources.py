"""Sample Windows host and existing llama worker during a local run (MiB, inclusive of other consumers)."""
import argparse
import ctypes
import json
import subprocess
import time
from ctypes import wintypes
from pathlib import Path

class Host(ctypes.Structure):
    _fields_=[("length",wintypes.DWORD),("load",wintypes.DWORD)]+[(n,ctypes.c_ulonglong) for n in
               "physical free page freepage virt freevirt extended".split()]

class Process(ctypes.Structure):
    _fields_=[("size",wintypes.DWORD),("faults",wintypes.DWORD)]+[(n,ctypes.c_size_t) for n in
               "peak rss pool_peak pool nonpool_peak nonpool page page_peak".split()]

def sample(pid):
    host=Host(); host.length=ctypes.sizeof(host)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(host)):raise ctypes.WinError()
    fn=ctypes.windll.kernel32.OpenProcess;fn.restype=wintypes.HANDLE
    handle=fn(0x0410,False,pid)
    memory=Process();memory.size=ctypes.sizeof(memory)
    ok=False
    if handle:
        try: ok=ctypes.windll.psapi.GetProcessMemoryInfo(wintypes.HANDLE(handle),ctypes.byref(memory),memory.size)
        finally: ctypes.windll.kernel32.CloseHandle(wintypes.HANDLE(handle))
    gpu=subprocess.check_output(["nvidia-smi","--query-gpu=memory.used","--format=csv,noheader,nounits"],text=True)
    return {"system_used_mib":(host.physical-host.free)//2**20,"worker_rss_mib":memory.rss//2**20 if ok else None,
            "worker_process_lifetime_peak_mib":memory.peak//2**20 if ok else None,"gpu_used_mib":int(gpu.splitlines()[0])}

if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--pid",type=int,required=True);p.add_argument("--out",type=Path,required=True)
    p.add_argument("--samples",type=int,default=12); args=p.parse_args()
    with args.out.open("x",encoding="utf8") as f:
        for i in range(args.samples):
            f.write(json.dumps(sample(args.pid))+"\n"); f.flush()
            if i+1<args.samples:time.sleep(2)
