#!/usr/bin/env python3
"""Khoi chay bot (bot_v7.py = BOT V11.1 CEX<->DEX) va tu khoi dong lai neu bot dung."""
import os, subprocess, sys, time

def main():
    target = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot_v7.py")
    print(f"[APP] Khoi chay {target} | PORT={os.getenv('PORT', '10000')}", flush=True)
    fails = 0
    while True:
        t0 = time.time()
        try:
            code = subprocess.run([sys.executable, target]).returncode
            print(f"[APP] bot thoat voi ma {code}", flush=True)
        except Exception as e:
            print(f"[APP] loi: {e}", flush=True)
        fails = fails + 1 if time.time() - t0 < 60 else 0   # chet ngay khi khoi dong -> cho lau hon
        wait = min(10 * (2 ** min(fails, 5)), 300)
        print(f"[APP] khoi dong lai sau {wait}s", flush=True)
        time.sleep(wait)

if __name__ == "__main__":
    main()
