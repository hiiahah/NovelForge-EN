#!/usr/bin/env python3
"""
setup_second_genspark_account.py
Helper script to log into the second Genspark Plus account (e.g. sigmaishere619@gmail.com)
in an isolated Firefox profile, and automatically capture cookies into:
  genspark_cookies_sigmaishere619.json
"""

import os
import sys
import time
import json
import sqlite3
import subprocess
from pathlib import Path
from curl_cffi import requests

DISPLAY = os.environ.get("DISPLAY", ":1.0")
TARGET_EMAIL = sys.argv[1] if len(sys.argv) > 1 else "sigmaishere619@gmail.com"
PROFILE_DIR = "/home/ubuntu/snap/firefox/common/profile_sigma"
os.makedirs(PROFILE_DIR, exist_ok=True)

USER_ENDPOINT = "https://www.genspark.ai/api/user"
LOGIN_URL = "https://www.genspark.ai/api/login?redirect_url=https://www.genspark.ai/"
DEFAULT_IMPERSONATE = "chrome124"

COOKIE_DESTINATIONS = [
    "/home/ubuntu/NovelForge-EN/backend/app/services/ai/providers/genspark_cookies_sigmaishere619.json",
    "/home/ubuntu/NovelForge-EN/backend/genspark_cookies_sigmaishere619.json",
    "/home/ubuntu/nvidia_chat_bot/genspark_cookies_sigmaishere619.json",
]

def check_cookies_in_profile(profile_path: str):
    db_path = os.path.join(profile_path, "cookies.sqlite")
    if not os.path.exists(db_path):
        return {}
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        c = conn.cursor()
        c.execute("SELECT name, value, host, path FROM moz_cookies WHERE host LIKE '%genspark.ai%'")
        rows = c.fetchall()
        conn.close()
        return {r[0]: {"value": r[1], "host": r[2], "path": r[3]} for r in rows}
    except Exception:
        return {}

def test_session(cookie_dict):
    if not cookie_dict:
        return None
    s = requests.Session(impersonate=DEFAULT_IMPERSONATE)
    for name, meta in cookie_dict.items():
        s.cookies.set(name, meta["value"], domain=meta["host"], path=meta["path"])
    try:
        r = s.get(USER_ENDPOINT, timeout=8)
        if r.status_code == 200:
            cogen = r.json().get("data", {}).get("cogen", {})
            return cogen
    except Exception:
        pass
    return None

def main():
    print("="*70)
    print(f"GENSPARK SECOND ACCOUNT SETUP: [{TARGET_EMAIL}]")
    print(f"Isolated Firefox Profile: {PROFILE_DIR}")
    print(f"Target VNC Display: {DISPLAY} (Port 5901)")
    print("="*70)

    # 1. Check if profile already has valid cookies
    existing = check_cookies_in_profile(PROFILE_DIR)
    user_info = test_session(existing)
    if user_info and user_info.get("email"):
        found_email = user_info.get("email")
        plan = user_info.get("plan", "unknown")
        print(f"✨ Found existing valid session for [{found_email}] (Plan: {plan})!")
        save_cookies(existing, found_email)
        return

    # 2. Launch Firefox on DISPLAY
    print(f"[*] Launching isolated Firefox profile on DISPLAY={DISPLAY}...")
    env = os.environ.copy()
    env["DISPLAY"] = DISPLAY
    proc = subprocess.Popen([
        "firefox",
        "--profile", PROFILE_DIR,
        "--new-instance",
        LOGIN_URL
    ], env=env)

    print(f"\n👉 Firefox has been opened on VNC :1 (port 5901).")
    print(f"👉 Complete the Google Sign-In for [{TARGET_EMAIL}] in the browser window.")
    print(f"[*] Waiting for login to be detected (timeout 10 minutes)...")

    start = time.time()
    while time.time() - start < 600:
        cookies = check_cookies_in_profile(PROFILE_DIR)
        user_info = test_session(cookies)
        if user_info and user_info.get("email"):
            email = user_info.get("email")
            plan = user_info.get("plan", "unknown")
            print(f"\n🎉 SUCCESS! Detected login as [{email}] (Plan: {plan})!")
            save_cookies(cookies, email)
            return
        time.sleep(3)

    print("⚠️ Timeout reached waiting for login.")

def save_cookies(cookie_dict, email):
    cookie_list = [
        {"name": name, "value": meta["value"], "domain": meta["host"], "path": meta["path"]}
        for name, meta in cookie_dict.items()
    ]
    for dest in COOKIE_DESTINATIONS:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "w") as f:
            json.dump(cookie_list, f, indent=2)
        print(f"  [+] Saved cookies to: {dest}")
    print(f"✅ Account [{email}] is now permanently configured in the Genspark pool!")

if __name__ == "__main__":
    main()
