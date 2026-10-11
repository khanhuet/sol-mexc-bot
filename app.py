#!/usr/bin/env python3
import subprocess, sys, time, os

def run():
    print("[APP V7] Starting bot_v7.py - V7 co san web server + waitress")
    while True:
        try:
            result = subprocess.run([sys.executable, "bot_v7.py"])
            print(f"[APP] bot_v7.py exit {result.returncode}, restart in 10s")
        except Exception as e:
            print(f"[APP ERR] {e}")
            import traceback; traceback.print_exc()
        time.sleep(10)

if __name__ == "__main__":
    run()
