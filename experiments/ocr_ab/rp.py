import json, os, sys, urllib.request, urllib.error
KEY = open(os.path.expanduser("~/.config/runpod/api_key")).read().strip()
BASE = "https://rest.runpod.io/v1"

def call(method, path, body=None):
    req = urllib.request.Request(BASE + path, method=method,
        data=json.dumps(body).encode() if body else None,
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            raw = r.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code} on {method} {path}: {e.read().decode()[:400]}")
        return None

cmd = sys.argv[1]

def show(p):
    m = p.get("machine") or {}
    print(p.get("id"), p.get("name"), p.get("desiredStatus"),
          m.get("gpuTypeId") or (p.get("gpuTypeIds") or [None])[0],
          "ip=" + str(p.get("publicIp")), "ports=" + str(p.get("portMappings")))

if cmd == "pods":
    d = call("GET", "/pods")
    pods = d if isinstance(d, list) else (d or {}).get("data", [])
    for p in pods:
        show(p)

elif cmd == "create":
    # create <gpuTypeId> <name> <cloud>
    gpu, name, cloud = sys.argv[2], sys.argv[3], sys.argv[4]
    body = {
        "name": name,
        "imageName": "runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04",
        "gpuTypeIds": [gpu],
        "cloudType": cloud,          # SECURE | COMMUNITY
        "gpuCount": 1,
        "containerDiskInGb": 60,
        "volumeInGb": 0,
        "ports": ["22/tcp"],
        "supportPublicIp": True,
    }
    r = call("POST", "/pods", body)
    print(json.dumps(r, indent=2)[:1500] if r else "FAILED")

elif cmd == "get":
    p = call("GET", f"/pods/{sys.argv[2]}")
    if p:
        show(p)

elif cmd == "rm":
    print(call("DELETE", f"/pods/{sys.argv[2]}"))

# extra: cost detail
if cmd == "cost":
    for pid in sys.argv[2:]:
        p = call("GET", f"/pods/{pid}")
        if p:
            m = p.get("machine") or {}
            print(f"{pid} {p.get('name')}: gpu={m.get('gpuTypeId')} costPerHr=${p.get('costPerHr')} cloud={p.get('cloudType') or p.get('machineType')} dc={m.get('dataCenterId')}")
