#!/usr/bin/env python3
"""Plans and publishes Corral's PHP feed. See .github/workflows/php.yml.

php.json lists every build: {"builds": {"8.4.27": {"macos-aarch64":
{"url": ..., "sha256": ...}}}}. Corral accepts it only with a valid
php.json.sig, made with CORRAL_PHP_FEED_KEY.

scripts/security.json holds the oldest patch of each line without known flaws.
Raising a line there and pushing marks older versions as vulnerable in every
Corral, without a new release of the app.
"""
import base64
import glob
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

REPO = os.environ.get("GITHUB_REPOSITORY", "manuelcardona0228/corral_php")
FEED_TAG = "php-feed"
FEED_URL = f"https://github.com/{REPO}/releases/download/{FEED_TAG}/php.json"
LINES = ["8.5", "8.4", "8.3", "8.2"]
RUNNERS = {
    "macos-aarch64": "macos-15",
    "macos-x86_64": "macos-15-intel",
    "linux-x86_64": "ubuntu-24.04",
    "linux-aarch64": "ubuntu-24.04-arm",
}


def security():
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "security.json")) as f:
        return json.load(f)


def fetch(url):
    with urllib.request.urlopen(url, timeout=30) as res:
        return res.read()


def current_feed():
    try:
        return json.loads(fetch(FEED_URL))
    except urllib.error.HTTPError as err:
        if err.code == 404:  # the first run
            return {"builds": {}}
        raise


def newest(line):
    raw = fetch(f"https://www.php.net/releases/index.php?json&version={line}")
    return json.loads(raw)["version"]


def plan():
    wanted = [v.strip() for v in os.environ.get("VERSIONS", "").split(",") if v.strip()]
    for v in wanted:
        if not re.fullmatch(r"\d+\.\d+\.\d+", v):
            sys.exit(f"not an exact version: {v}")
    wanted = wanted or [newest(line) for line in LINES]
    feed = current_feed()
    builds = feed["builds"]
    jobs = [
        {"version": v, "platform": platform, "runner": runner}
        for v in wanted
        for platform, runner in RUNNERS.items()
        if platform not in builds.get(v, {})
    ]
    print(f"matrix={json.dumps(jobs)}")
    print(f"any={'true' if jobs else 'false'}")
    print(f"resign={'true' if feed.get('secure') != security() else 'false'}")


def gh(*args, check=True):
    return subprocess.run(["gh", *args, "--repo", REPO], check=check, capture_output=True, text=True)


def ensure_release(tag, title, notes):
    # Prereleases never become "latest", which is what Corral's own
    # upgrades read.
    if gh("release", "view", tag, check=False).returncode != 0:
        gh("release", "create", tag, "--title", title, "--notes", notes, "--prerelease", "--latest=false")


def sign(raw):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key = base64.b64decode(os.environ["CORRAL_PHP_FEED_KEY"].strip())
    return base64.b64encode(Ed25519PrivateKey.from_private_bytes(key[:32]).sign(raw))


def publish(folder):
    feed = current_feed()
    files = sorted(glob.glob(os.path.join(folder, "php-*.tar.gz")))
    for path in files:
        name = os.path.basename(path)
        match = re.fullmatch(r"php-(\d+\.\d+\.\d+)-(.+)\.tar\.gz", name)
        if not match:
            sys.exit(f"unexpected file: {name}")
        version, platform = match.groups()
        tag = f"php-{version}"
        ensure_release(tag, f"PHP {version}", f"Corral's builds of PHP {version}.")
        gh("release", "upload", tag, path, "--clobber")
        with open(path, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()
        feed["builds"].setdefault(version, {})[platform] = {
            "url": f"https://github.com/{REPO}/releases/download/{tag}/{name}",
            "sha256": digest,
        }
    feed["secure"] = security()
    raw = json.dumps(feed, indent=2, sort_keys=True).encode()
    with open("php.json", "wb") as f:
        f.write(raw)
    with open("php.json.sig", "wb") as f:
        f.write(sign(raw) + b"\n")
    ensure_release(FEED_TAG, "PHP feed", "The list of PHP builds Corral installs from.")
    gh("release", "upload", FEED_TAG, "php.json", "php.json.sig", "--clobber")


if __name__ == "__main__":
    if sys.argv[1:2] == ["plan"]:
        plan()
    elif sys.argv[1:2] == ["publish"]:
        publish(sys.argv[2])
    else:
        sys.exit("usage: php_feed.py plan | publish <folder>")
