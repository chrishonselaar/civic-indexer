#!/usr/bin/env python3
"""RunPod helper for the OCR drain — rp.py's create, but with the baked image.

Kept separate from experiments/ocr_ab/rp.py so the drain runbook doesn't depend
on A/B-session tooling defaults (that create hardcodes the bare pytorch image).

usage:
  python3 pod.py create <gpuTypeId> <name> <cloud> [image] [countryCodes] [minVcpu]
      countryCodes: comma-separated filter, e.g. NL,DE,BE,FR,SE,GB
      (EU codes shave minutes off the 1.5 GB payload upload vs a US machine)
      minVcpu: minVCPUPerGPU filter — crop+render are CPU-bound, ask for >=16
      (the 2026-07-23 drain pods drew 8 vs 25 vCPU at the same price)
  python3 pod.py get <podId>          # poll for ip= and ports={'22': NNNNN}
  python3 pod.py pods
  python3 pod.py rm <podId>           # ALWAYS tear down; verify with `pods`
"""
import json
import os
import sys
import urllib.error
import urllib.request

KEY = open(os.path.expanduser("~/.config/runpod/api_key")).read().strip()
BASE = "https://rest.runpod.io/v1"
IMAGE = "ghcr.io/chrishonselaar/ocr-mineru:v1"  # public; vLLM 0.11.2 + mineru baked


def call(method, path, body=None):
    req = urllib.request.Request(
        BASE + path, method=method,
        data=json.dumps(body).encode() if body else None,
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            raw = r.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code} on {method} {path}: {e.read().decode()[:400]}")
        return None


def show(p):
    m = p.get("machine") or {}
    print(p.get("id"), p.get("name"), p.get("desiredStatus"),
          m.get("gpuTypeId") or (p.get("gpuTypeIds") or [None])[0],
          "ip=" + str(p.get("publicIp")), "ports=" + str(p.get("portMappings")))


cmd = sys.argv[1]
if cmd == "pods":
    d = call("GET", "/pods")
    for p in (d if isinstance(d, list) else (d or {}).get("data", [])):
        show(p)
elif cmd == "create":
    gpu, name, cloud = sys.argv[2], sys.argv[3], sys.argv[4]
    body = {
        "name": name,
        "imageName": sys.argv[5] if len(sys.argv) > 5 else IMAGE,
        "gpuTypeIds": [gpu],
        "cloudType": cloud,  # SECURE | COMMUNITY
        "gpuCount": 1,
        "containerDiskInGb": 60,
        "volumeInGb": 0,
        "ports": ["22/tcp"],
        "supportPublicIp": True,
    }
    if len(sys.argv) > 6:
        body["countryCodes"] = sys.argv[6].split(",")
    if len(sys.argv) > 7:
        body["minVCPUPerGPU"] = int(sys.argv[7])
    r = call("POST", "/pods", body)
    print(json.dumps(r, indent=2)[:1500] if r else "FAILED")
elif cmd == "get":
    p = call("GET", f"/pods/{sys.argv[2]}")
    if p:
        show(p)
elif cmd == "rm":
    print(call("DELETE", f"/pods/{sys.argv[2]}"))
