#!/usr/bin/env python3
import subprocess, sys, os, time
# V13 FINAL - 11 CHAINS + 10 CEX + ALL DEX + MAKER 0% + INVENTORY + WS LIVE + AI
def main():
    target = "bot_v7.py"
    cexs = os.getenv('ENABLED_CEXS','MEXC,GATE,BYBIT,BINANCE,OKX,KUCOIN,BITGET,BINGX,HTX,LBANK')
    print(f"[APP V13 FINAL] Starting {target} | 11 CHAINS + 10 CEX + ALL DEX + MAKER + INVENTORY | CEX={cexs} | PORT={os.getenv('PORT','10000')}")
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
