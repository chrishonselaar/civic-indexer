#!/usr/bin/env python3
"""Vast.ai helper for OCR burst — pod.py's ergonomics, with this session's lessons baked
in as defaults so `up` hands you a KNOWN-GOOD host in one command.

Why this exists (full writeup: experiments/ocr_ab/results/dpi-ab-and-provider-cost.md):
Vast is cheaper AND faster per page than RunPod on a good 3090 (measured 4,472 p/GPU-h /
$0.045/1k vs 3,176 / $0.069), but its marketplace is heterogeneous — we burned 6 hosts
before one worked. The two failure modes and their fixes, encoded here:

  1. vCPU STARVATION — MinerU's crop/preprocess is CPU-bound; a 5-vCPU 3090 ran the GPU at
     4% util => 355 p/h (10x worse than RunPod). FIX: hard-filter cpu_cores_effective>=16.
  2. PULL RELIABILITY = DATACENTER PEERING — the US 137.175.76.x datacenter layer-retry-
     looped on BOTH the 24GB and 12.9GB slim image; EU hosts (NL, FR) pulled both fine.
     FIX: default to EU geolocation + treat hosts as DISPOSABLE (auto-destroy + reroll on
     a pull that stalls/loops/times out).

Plus: `get` returns the DIRECT public_ipaddr:port (the ssh6.vast.ai proxy failed this
session), and `up --gate` runs a short real OCR pass and rerolls if throughput is low
(catches thermal throttling / shared GPUs that pass the spec filter).

Image: ghcr.io/chrishonselaar/ocr-mineru:slim (13.1GB, validated — needs no runtime pip).
Reads VAST_AI_API_KEY from ./.env or the environment. Shells out to the vastai CLI
(default `uvx vastai@latest`; override with the VASTAI env var) so it inherits the CLI's
validated request shapes rather than hand-rolling the Vast REST API.

usage:
  python3 vast.py offers [--max-dph 0.30]
  python3 vast.py up [--image IMG] [--gate <pdf_dir>] [--gate-min 2000]
                     [--max-dph 0.30] [--max-tries 4]      # rent best, wait, (gate), print ssh
  python3 vast.py get <id>            # DIRECT ssh host:port + status
  python3 vast.py instances
  python3 vast.py rm <id>            # destroy — ALWAYS tear down
"""
import json
import os
import re
import shlex
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
IMAGE = "ghcr.io/chrishonselaar/ocr-mineru:slim"
VASTAI = os.environ.get("VASTAI", "uvx vastai@latest").split()
# EU-ish country codes = reliable GHCR peering (this session's pull-failure fix)
EU = {"NL", "DE", "FR", "SE", "GB", "AT", "CZ", "PL", "ES", "IT", "PT", "DK", "FI",
      "NO", "CH", "BE", "IE", "RO", "LT", "EE", "LV", "HU", "SK", "BG", "GR", "HR",
      "SI", "UA", "FR", "MD", "RS"}
BASE_Q = ("gpu_name=RTX_3090 num_gpus=1 rentable=true verified=true cuda_vers>=12.4 "
          "cpu_cores_effective>=16 disk_space>=45 inet_down>=400")


def key():
    v = os.environ.get("VAST_AI_API_KEY")
    if not v:
        env = os.path.join(REPO, ".env")
        if os.path.exists(env):
            for ln in open(env):
                if ln.startswith("VAST_AI_API_KEY="):
                    v = ln.split("=", 1)[1].strip().strip("'\"")
    if not v:
        sys.exit("VAST_AI_API_KEY not in env or ./.env")
    return v


def vast(*args, raw=False, confirm=False):
    """Run the vastai CLI. raw=True parses --raw JSON; confirm feeds 'y' to prompts."""
    cmd = VASTAI + list(args) + ["--api-key", key()]
    if raw:
        cmd += ["--raw"]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       input="y\n" if confirm else None)
    if raw:
        try:
            return json.loads(r.stdout)
        except json.JSONDecodeError:
            sys.exit(f"vastai returned non-JSON:\n{r.stdout[:400]}\n{r.stderr[:400]}")
    return r.stdout + r.stderr


def geo(o):
    return (o.get("geolocation") or "").split(",")[-1].strip().upper()


def offers(max_dph):
    """Verified >=16-vCPU 3090s under max_dph, EU-PREFERRED (not EU-only): EU hosts first
    (reliable GHCR peering), then non-EU verified hosts as reroll fallbacks — the gate +
    retry-loop detection protect against the riskier peering. EU-only kept leaving the pool
    at a single host, so a failed pull had nothing to reroll to (observed 3x this session)."""
    o = vast("search", "offers", BASE_Q, "-o", "dph+", raw=True)
    ok = [x for x in o if x.get("dph_total", 99) <= max_dph]
    eu = [x for x in ok if geo(x) in EU]
    non = [x for x in ok if geo(x) not in EU]
    return eu + non


def gh_token():
    try:
        t = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True)
        return t.stdout.strip() or None
    except FileNotFoundError:
        return None


def instance(iid):
    for x in vast("show", "instances", raw=True):
        if x.get("id") == iid:
            return x
    return None


def direct_ssh(o):
    ip = o.get("public_ipaddr")
    ports = (o.get("ports") or {}).get("22/tcp") or [{}]
    return ip, ports[0].get("HostPort")


def ssh_base(ip, port):
    return ["ssh", "-p", str(port), "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null", "-o", "ConnectTimeout=25",
            f"root@{ip}"]


def wait_running(iid, timeout=600):
    """Poll until running; abort early on a pull retry-loop. Returns the instance dict or None."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        o = instance(iid)
        st = o.get("actual_status") if o else None
        msg = (o.get("status_msg") or "") if o else ""
        print(f"  [{int(time.time()-t0):4d}s] {st} {msg[:44]}", flush=True)
        if st == "running":
            return o
        if "Retrying" in msg or "rror" in msg:   # pull retry-loop / error — reroll
            print("  -> pull failing on this host", flush=True)
            return None
        time.sleep(20)
    print("  -> readiness timeout", flush=True)
    return None


def gate(ip, port, pdf_dir, gate_min):
    """Ship ocr_worker.py + PDFs and read the INSTANTANEOUS steady-state rate off the 2nd
    batch (48 pp / that batch's ocr seconds) — a ~2 min micro-bench, not the full run.
    Instantaneous (not cumulative) so the CUDA-graph warmup in batch 1 doesn't drag it:
    measured batch-2 instantaneous is ~4,600 p/h on a good 16-vCPU Vast 3090 and ~2,630 on
    a RunPod community pod, vs ~360 on a CPU-starved 5-vCPU host — so gate_min ~2,000 passes
    real hosts (incl. RunPod-class) and rejects starved ones. (An earlier CUMULATIVE metric
    read ~2,150 for RunPod and would have wrongly failed it against a 2,500 bar.) Kills the
    bench + cleans up on the way out. Returns pages/GPU-h, or 0 on error/timeout."""
    worker = os.path.join(HERE, "ocr_worker.py")
    pdfs = [f for f in os.listdir(pdf_dir) if f.endswith(".pdf")]
    if not pdfs:
        sys.exit(f"--gate dir has no PDFs: {pdf_dir}")
    print(f"  gate: shipping {len(pdfs)} PDF(s) + worker, timing ~2 batches...", flush=True)
    sb = ssh_base(ip, port)
    tar = subprocess.Popen(["tar", "-cf", "-", "-C", pdf_dir] + pdfs, stdout=subprocess.PIPE)
    subprocess.run(sb + ["mkdir -p /root/gp && tar -xf - -C /root/gp"],
                   stdin=tar.stdout, capture_output=True, text=True)
    subprocess.run(["scp", "-P", str(port), "-o", "StrictHostKeyChecking=no",
                    "-o", "UserKnownHostsFile=/dev/null", worker, f"root@{ip}:/root/"],
                   capture_output=True, text=True)
    subprocess.run(sb + ["cd /root && rm -rf gout && "
                         "(nohup python3 ocr_worker.py gp gout 200 > gate.log 2>&1 &)"],
                   capture_output=True, text=True)
    # remote probe: L = the last batch>=2 timing line (skips warmup batch 1) -> pp + ocr secs
    # for the instantaneous rate; T = final total (tiny corpus finished first); E = FATAL error.
    probe = (
        "l=$(grep -aoE 'batch [2-9]/[0-9]+: [0-9]+ pp ocr [0-9.]+s' /root/gate.log | tail -1);"
        "t=$(grep -aoE '[0-9]+ pages/GPU-hour end-to-end' /root/gate.log | grep -oE '^[0-9]+');"
        "e=$(grep -aiE 'Traceback|No module named|Failed to find C compiler|"
        "Engine core initialization failed|CUDA out of memory' /root/gate.log | tail -1);"
        "echo \"L=$l\"; echo \"T=$t\"; echo \"E=$e\"")
    rate = 0
    for _ in range(24):  # ~6 min ceiling (model load + 2 hard-corpus batches ~2.5 min)
        time.sleep(15)
        kv = dict(ln.split("=", 1) for ln in
                  subprocess.run(sb + [probe], capture_output=True, text=True).stdout.splitlines()
                  if "=" in ln)
        m = re.search(r"([0-9]+) pp ocr ([0-9.]+)s", kv.get("L", ""))
        if m:                                    # instantaneous: pages / this-batch seconds
            rate = round(int(m.group(1)) / float(m.group(2)) * 3600); break
        if kv.get("T"):
            rate = int(kv["T"]); break
        if kv.get("E"):
            print(f"  gate error: {kv['E'][:80]}", flush=True); break
    # stop the bench + clean up so a passing host is pristine
    subprocess.run(sb + ["pkill -9 -f ocr_worker.py; rm -rf /root/gp /root/gout /root/gate.log"],
                   capture_output=True, text=True)
    return rate


def up(args):
    max_dph = float(args.get("--max-dph", "0.30"))
    max_tries = int(args.get("--max-tries", "4"))
    image = args.get("--image", IMAGE)
    gate_dir = args.get("--gate")
    gate_min = int(args.get("--gate-min", "2000"))
    tok = gh_token()
    login = f"-u chrishonselaar -p {tok} ghcr.io" if tok else None

    cands = offers(max_dph)
    if not cands:
        sys.exit(f"no verified EU >=16vCPU 3090 offers under ${max_dph}/h")
    n_try = min(max_tries, len(cands))
    print(f"{len(cands)} candidate offer(s); trying up to {n_try}", flush=True)

    for attempt, off in enumerate(cands[:n_try], 1):
        oid, dph = off["id"], off["dph_total"]
        print(f"\n=== try {attempt}: offer {oid} ${dph:.3f}/h "
              f"{int(off['cpu_cores_effective'])}vCPU {geo(off)} ===", flush=True)
        cargs = ["create", "instance", str(oid), "--image", image, "--disk", "45",
                 "--ssh", "--direct", "--onstart-cmd", "sleep infinity"]
        if login:
            cargs += ["--login", login]
        res = vast(*cargs, raw=True)
        iid = res.get("new_contract")
        if not res.get("success") or not iid:
            print(f"  create failed: {str(res)[:120]}", flush=True)
            continue
        print(f"  instance {iid} created; waiting for image pull...", flush=True)
        try:
            o = wait_running(iid)
            if not o:
                raise RuntimeError("not ready")
            ip, port = direct_ssh(o)
            print(f"  UP: ssh -p {port} root@{ip}", flush=True)
            if gate_dir:
                ph = gate(ip, port, gate_dir, gate_min)
                print(f"  gate: {ph} pages/GPU-hour (min {gate_min})", flush=True)
                if ph < gate_min:
                    raise RuntimeError(f"throughput {ph} < {gate_min}")
            print(f"\nREADY instance={iid}  ssh -p {port} root@{ip}", flush=True)
            print(f"  rm with: python3 {os.path.basename(__file__)} rm {iid}", flush=True)
            return
        except (RuntimeError, KeyboardInterrupt) as e:
            print(f"  rerolling ({e}); destroying {iid}", flush=True)
            vast("destroy", "instance", str(iid), confirm=True)
    sys.exit(f"no host passed after {n_try} tr{'y' if n_try == 1 else 'ies'} "
             f"({len(cands)} candidate(s) in pool)")


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    cmd = sys.argv[1]
    # crude --flag value parser for the rest
    rest, kv = sys.argv[2:], {}
    i = 0
    pos = []
    while i < len(rest):
        if rest[i].startswith("--"):
            kv[rest[i]] = rest[i + 1] if i + 1 < len(rest) and not rest[i + 1].startswith("--") else "1"
            i += 2 if rest[i] in kv and kv[rest[i]] != "1" else 1
        else:
            pos.append(rest[i]); i += 1

    if cmd == "offers":
        for o in offers(float(kv.get("--max-dph", "0.30"))):
            print(f"{o['id']:>10} dph={o['dph_total']:.3f} "
                  f"vcpu={int(o['cpu_cores_effective'])} down={o['inet_down']:.0f} "
                  f"disk={int(o['disk_space'])} {o.get('geolocation','?')}")
    elif cmd == "up":
        up(kv)
    elif cmd == "get":
        o = instance(int(pos[0]))
        if not o:
            sys.exit("no such instance")
        ip, port = direct_ssh(o)
        print(o["id"], o.get("actual_status"), f"ssh -p {port} root@{ip}",
              (o.get("status_msg") or "")[:40])
    elif cmd == "instances":
        for o in vast("show", "instances", raw=True):
            ip, port = direct_ssh(o)
            print(o["id"], o.get("actual_status"),
                  f"{o.get('gpu_name')} ${o.get('dph_total',0):.3f}/h",
                  f"ssh -p {port} root@{ip}")
    elif cmd == "rm":
        print(vast("destroy", "instance", pos[0], confirm=True))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
