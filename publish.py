"""
publish.py — optional, secret-gated publishers (this task's requirement S3).

Every subcommand reads its own credentials from environment variables and does nothing but print
"skipped: <VAR> not set" (returns 0) when they are absent, so `python publish.py all` is always
safe to run unconditionally from CI regardless of which secrets (if any) are configured. No
subcommand ever raises past its own boundary or makes the calling process exit non-zero on a
network/API failure — every HTTP call and every external-CLI call is wrapped in try/except and
only prints a warning, matching this task's "never fail the run" requirement (the workflow step
also sets continue-on-error as a second line of defense).

Each subcommand is written against its provider's own documented request shape (see the docstring
above each `cmd_*`) — none of it has been exercised against a live endpoint in this environment
(every outbound host below is network-blocked here); `tests.py`'s `test_publish_*` functions cover
the payload shapes with a monkeypatched `requests.post` and the "no secret -> exit 0" contract.
"""
from __future__ import annotations
import argparse
import datetime
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile

import requests

import config
import digest as digest_mod

BUTTONDOWN_URL = "https://api.buttondown.com/v1/emails"
BLUESKY_SESSION_URL = "https://bsky.social/xrpc/com.atproto.server.createSession"
BLUESKY_RECORD_URL = "https://bsky.social/xrpc/com.atproto.repo.createRecord"


def _latest_digest(edition_key: str) -> dict | None:
    """Most recent persisted results/digest/<edition>_<as_of>.json (digest.py's own output),
    or None if digest.py has not produced one for this edition yet — never a hard failure, the
    caller just skips that edition/provider for this run."""
    as_ofs = digest_mod._list_persisted_digests(edition_key)
    if not as_ofs:
        return None
    path = os.path.join(digest_mod.DIGEST_DIR, f"{edition_key}_{as_ofs[0]}.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _post_json(url, payload, headers=None, label="") -> int:
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=20)
        if resp.status_code >= 300:
            print(f"warning: {label} POST returned HTTP {resp.status_code}: {resp.text[:300]}")
        else:
            print(f"{label}: sent")
    except requests.RequestException as e:
        print(f"warning: {label} request failed: {e}")
    return 0


def _one_liner_for_social(d: dict, url: str) -> str:
    t = d.get("tally") or {}
    text = (f"{d['as_of']}: {t.get('ALIVE', 0)} alive / {t.get('FADING', 0)} fading / "
            f"{t.get('DEAD', 0)} dead. {url}")
    if len(text.encode("utf-8")) > 300:
        # Keep the link intact; trim the lead-in instead of the URL.
        overflow = len(text.encode("utf-8")) - 300
        lead = f"{d['as_of']}: {t.get('ALIVE', 0)} alive / {t.get('FADING', 0)} fading / {t.get('DEAD', 0)} dead."
        lead = lead[:max(0, len(lead) - overflow - 1)].rstrip() + "…"
        text = f"{lead} {url}"
    return text


# ---------------------------------------------------------------------------
# Buttondown — POST the English digest as an email. Korean digest as a second email only if
# BUTTONDOWN_SEND_KO=1. https://docs.buttondown.com/api-emails-create
# ---------------------------------------------------------------------------

def cmd_buttondown(args=None) -> int:
    api_key = os.environ.get("BUTTONDOWN_API_KEY")
    if not api_key:
        print("skipped: BUTTONDOWN_API_KEY not set")
        return 0
    headers = {"Authorization": f"Token {api_key}"}

    d = _latest_digest("en")
    if d is None:
        print("skipped buttondown (en): no digest available yet this run")
    else:
        _post_json(BUTTONDOWN_URL,
                   {"subject": d["title"], "body": d["markdown"], "status": "about_to_send"},
                   headers=headers, label="buttondown (en)")

    if os.environ.get("BUTTONDOWN_SEND_KO") == "1":
        d_ko = _latest_digest("ko")
        if d_ko is None:
            print("skipped buttondown (ko): BUTTONDOWN_SEND_KO=1 but no Korean digest yet this run")
        else:
            _post_json(BUTTONDOWN_URL,
                       {"subject": d_ko["title"], "body": d_ko["markdown"], "status": "about_to_send"},
                       headers=headers, label="buttondown (ko)")
    return 0


# ---------------------------------------------------------------------------
# Bluesky (AT Protocol) — createSession, then repo.createRecord with an app.bsky.feed.post record
# and a link facet over the digest URL. https://docs.bsky.app/docs/api/com-atproto-server-create-session
# https://docs.bsky.app/docs/api/com-atproto-repo-create-record
# ---------------------------------------------------------------------------

def cmd_bluesky(args=None) -> int:
    handle = os.environ.get("BLUESKY_HANDLE")
    app_password = os.environ.get("BLUESKY_APP_PASSWORD")
    if not handle or not app_password:
        print("skipped: BLUESKY_HANDLE/BLUESKY_APP_PASSWORD not set")
        return 0
    d = _latest_digest("en")
    if d is None:
        print("skipped bluesky: no English digest available yet this run")
        return 0

    try:
        sess = requests.post(BLUESKY_SESSION_URL,
                              json={"identifier": handle, "password": app_password}, timeout=20)
        sess.raise_for_status()
        session = sess.json()
        access_jwt, did = session["accessJwt"], session["did"]
    except (requests.RequestException, KeyError, ValueError) as e:
        print(f"warning: bluesky createSession failed: {e}")
        return 0

    url = digest_mod._item_url("en", d["as_of"])
    text = _one_liner_for_social(d, url)
    facets = []
    idx = text.rfind(url)
    if idx >= 0:
        byte_start = len(text[:idx].encode("utf-8"))
        byte_end = byte_start + len(url.encode("utf-8"))
        facets = [{"index": {"byteStart": byte_start, "byteEnd": byte_end},
                   "features": [{"$type": "app.bsky.richtext.facet#link", "uri": url}]}]

    record = {
        "$type": "app.bsky.feed.post",
        "text": text,
        "createdAt": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "facets": facets,
    }
    payload = {"repo": did, "collection": "app.bsky.feed.post", "record": record}
    return _post_json(BLUESKY_RECORD_URL, payload, headers={"Authorization": f"Bearer {access_jwt}"},
                       label="bluesky")


# ---------------------------------------------------------------------------
# Mastodon — POST /api/v1/statuses. https://docs.joinmastodon.org/methods/statuses/#create
# ---------------------------------------------------------------------------

def cmd_mastodon(args=None) -> int:
    instance = os.environ.get("MASTODON_INSTANCE")
    token = os.environ.get("MASTODON_TOKEN")
    if not instance or not token:
        print("skipped: MASTODON_INSTANCE/MASTODON_TOKEN not set")
        return 0
    d = _latest_digest("en")
    if d is None:
        print("skipped mastodon: no English digest available yet this run")
        return 0
    url = digest_mod._item_url("en", d["as_of"])
    text = _one_liner_for_social(d, url)
    endpoint = instance.rstrip("/") + "/api/v1/statuses"
    return _post_json(endpoint, {"status": text}, headers={"Authorization": f"Bearer {token}"},
                       label="mastodon")


# ---------------------------------------------------------------------------
# Kaggle — `kaggle datasets version` (create on first run if the dataset does not exist yet).
# https://www.kaggle.com/docs/api
# ---------------------------------------------------------------------------

def _current_as_of() -> str:
    try:
        with open(os.path.join(config.RESULTS_DIR, "latest.json"), encoding="utf-8") as f:
            return json.load(f).get("as_of", "unknown")
    except (OSError, json.JSONDecodeError):
        return "unknown"


def _prepare_dataset_dir() -> str:
    """results/*.json + docs/api/v1/**/latest.json, laid out under one temp directory — the file
    set spec_v3's Kaggle/Hugging Face requirement names verbatim. Read-only over the repo; never
    touches results/ or docs/ itself."""
    tmp = tempfile.mkdtemp(prefix="netcheck_dataset_")
    for path in glob.glob(os.path.join(config.RESULTS_DIR, "*.json")):
        shutil.copy2(path, tmp)
    api_root = os.path.join(config.DOCS_DIR, "api", "v1")
    if os.path.isdir(api_root):
        for root, _dirs, files in os.walk(api_root):
            for fn in files:
                if fn != "latest.json":
                    continue
                rel = os.path.relpath(root, api_root)
                dest_dir = os.path.join(tmp, "api_v1", rel)
                os.makedirs(dest_dir, exist_ok=True)
                shutil.copy2(os.path.join(root, fn), os.path.join(dest_dir, fn))
    return tmp


def cmd_kaggle(args=None) -> int:
    username = os.environ.get("KAGGLE_USERNAME")
    key = os.environ.get("KAGGLE_KEY")
    if not username or not key:
        print("skipped: KAGGLE_USERNAME/KAGGLE_KEY not set")
        return 0
    try:
        os.environ["KAGGLE_USERNAME"] = username
        os.environ["KAGGLE_KEY"] = key
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "kaggle"], check=False)

        dataset_id = f"{username}/dead-or-alive-strategy-verdicts"
        dataset_dir = _prepare_dataset_dir()
        with open(os.path.join(dataset_dir, "dataset-metadata.json"), "w") as f:
            json.dump({
                "id": dataset_id,
                "title": "Dead or Alive - strategy verdicts",
                "licenses": [{"name": "CC-BY-4.0"}],
            }, f, indent=2)

        as_of = _current_as_of()
        result = subprocess.run(
            ["kaggle", "datasets", "version", "-p", dataset_dir, "-m", f"week of {as_of}"],
            capture_output=True, text=True)
        if result.returncode != 0:
            # First run: the dataset doesn't exist yet under this account.
            create = subprocess.run(["kaggle", "datasets", "create", "-p", dataset_dir],
                                     capture_output=True, text=True)
            if create.returncode == 0:
                print("kaggle: dataset created")
            else:
                print(f"warning: kaggle datasets create/version failed: "
                      f"{(create.stderr or result.stderr)[:300]}")
        else:
            print("kaggle: dataset version pushed")
    except Exception as e:  # never let an optional publisher take the pipeline down
        print(f"warning: kaggle publish failed: {e}")
    return 0


# ---------------------------------------------------------------------------
# Hugging Face Hub — upload the same file set to a dataset repo, creating it if missing.
# https://huggingface.co/docs/huggingface_hub
# ---------------------------------------------------------------------------

def _hf_readme_card(repo: str, d: dict | None) -> str:
    lines = ["---", "license: cc-by-4.0", "---", "", f"# {config.PROJECT_NAME} — dataset", ""]
    lines.append(config.LEGAL_DISCLAIMER)
    lines.append("")
    if d:
        lines.append(d.get("markdown", ""))
    return "\n".join(lines)


def cmd_huggingface(args=None) -> int:
    token = os.environ.get("HF_TOKEN")
    repo = os.environ.get("HF_DATASET_REPO")
    if not token or not repo:
        print("skipped: HF_TOKEN/HF_DATASET_REPO not set")
        return 0
    try:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "huggingface_hub"], check=False)
        from huggingface_hub import HfApi  # imported lazily: only needed on this gated path

        api = HfApi(token=token)
        api.create_repo(repo_id=repo, repo_type="dataset", private=False, exist_ok=True,
                         token=token)

        d = _latest_digest("en")
        readme_dir = tempfile.mkdtemp(prefix="netcheck_hf_readme_")
        readme_path = os.path.join(readme_dir, "README.md")
        with open(readme_path, "w", encoding="utf-8") as f:
            f.write(_hf_readme_card(repo, d))
        api.upload_file(path_or_fileobj=readme_path, path_in_repo="README.md", repo_id=repo,
                         repo_type="dataset", token=token)

        dataset_dir = _prepare_dataset_dir()
        api.upload_folder(folder_path=dataset_dir, repo_id=repo, repo_type="dataset", token=token)
        print("huggingface: uploaded")
    except Exception as e:
        print(f"warning: huggingface publish failed: {e}")
    return 0


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

_COMMANDS = {
    "buttondown": cmd_buttondown,
    "bluesky": cmd_bluesky,
    "mastodon": cmd_mastodon,
    "kaggle": cmd_kaggle,
    "huggingface": cmd_huggingface,
}


def cmd_all(args=None) -> int:
    for name, fn in _COMMANDS.items():
        try:
            fn(args)
        except Exception as e:  # belt-and-braces: one provider's bug must never sink the others
            print(f"warning: publisher '{name}' raised unexpectedly: {e}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="netcheck optional publishers (spec_v3-adjacent, "
                                                   "this task's S3)")
    parser.add_argument("target", choices=list(_COMMANDS) + ["all"])
    args = parser.parse_args(argv)
    if args.target == "all":
        return cmd_all(args)
    return _COMMANDS[args.target](args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
