#!/usr/bin/env python3
"""Download every asset listed in assets_manifest.json into ./assets.

Wikimedia rate-limits anonymous clients hard, so files are fetched one at a
time with a pause and Retry-After is honoured. Needs curl, npm and tar.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(ROOT, "assets")
UA_BROWSER = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
UA_BOT = "ReelAssetFetch/1.0 (serial media download script)"


def fetch(url, out, referer=None):
    os.makedirs(os.path.dirname(out), exist_ok=True)
    if os.path.exists(out) and os.path.getsize(out) > 20000:
        return True
    wiki = "wikimedia.org" in url
    for _ in range(8):
        hdr = out + ".hdr"
        cmd = ["curl", "-s", "-L", "--max-time", "300", "-A", UA_BOT if wiki else UA_BROWSER, "-D", hdr, "-o", out, url]
        if referer:
            cmd += ["-e", referer]
        subprocess.run(cmd)
        head = open(hdr, errors="ignore").read() if os.path.exists(hdr) else ""
        if os.path.exists(hdr):
            os.remove(hdr)
        status = re.findall(r"HTTP/[\d.]+ (\d+)", head)
        if status and status[-1] == "200" and os.path.getsize(out) > 20000:
            if wiki:
                time.sleep(8)
            return True
        m = re.search(r"retry-after:\s*(\d+)", head, re.I)
        wait = min(900, int(m.group(1)) + 5) if m else 30
        print(f"  {status[-1] if status else '???'} for {os.path.basename(out)}; retrying in {wait}s")
        time.sleep(wait)
    if os.path.exists(out):
        os.remove(out)
    return False


def main():
    man = json.load(open(os.path.join(ROOT, "assets_manifest.json"), encoding="utf-8"))
    failed = []
    for group in ("photos", "videos", "music", "globe"):
        for it in man[group]:
            ok = fetch(it["url"], os.path.join(ASSETS, it["file"]), it.get("referer"))
            print(("ok   " if ok else "FAIL ") + it["file"])
            if not ok:
                failed.append(it["file"])
    for f in man["fonts"]:
        targets = {src: os.path.join(ASSETS, dst) for src, dst in f["files"].items()}
        if all(os.path.exists(t) for t in targets.values()):
            continue
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["npm", "pack", "--silent", f["npm"]], cwd=tmp, check=True)
            tgz = [x for x in os.listdir(tmp) if x.endswith(".tgz")][0]
            subprocess.run(["tar", "-xzf", tgz] + list(targets), cwd=tmp, check=True)
            for src, dst in targets.items():
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                os.replace(os.path.join(tmp, src), dst)
        print("ok   fonts from", f["npm"])
    if failed:
        sys.exit("missing: " + ", ".join(failed))


if __name__ == "__main__":
    main()
