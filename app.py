#!/usr/bin/env python3
import subprocess, sys, os, time
# V12 EN - launcher, web runs inside bot_v7.py
def main():
    target = "bot_v7.py"
    cexs = os.getenv('ENABLED_CEXS','FULL 10 CEX DEFAULT')
    print(f"[APP V12 EN FULL] Starting {target} 11 CHAINS + 10 CEX + ALL DEX | ENABLED_CEXS={cexs} | PORT={os.getenv('PORT','10000')}")
    while True:
        try:
            result = subprocess.run([sys.executable, target])
            print(f"[APP] {target} exit code {result.returncode}, restart in 10s")
        except Exception as e:
            print(f"[APP ERR] {e}")
            import traceback; traceback.print_exc()
        time.sleep(10)

if __name__ == "__main__":
    main()
