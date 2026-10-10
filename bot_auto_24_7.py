"""
BOT FULL - SOL MEXC ARBITRAGE ALERT
Nhiem vu:
1. Lay danh sach token mang SOL niem yet tren MEXC
2. Xac dinh token thuoc mang Solana (qua API capital/config/getall)
3. So sanh gia MEXC vs DEX Solana (Jupiter + DexScreener)
4. Gui canh bao Telegram neu chenh lech > 3%
"""

import requests
import time
import os
from datetime import datetime

# --- CAU HINH ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "8942050944:AAF6tftI7lTYErDCSnLhQSG1BXkUeU7-m3E")
CHAT_ID = os.getenv("CHAT_ID", "469374705")
CHECK_INTERVAL = 120  # 2 phut check 1 lan
PRICE_DIFF_THRESHOLD = 3.0  # chenh 3% thi bao

notified_tokens = set()

def send_telegram(msg):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = {"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"}
        r = requests.post(url, data=data, timeout=10)
        print(f"Telegram sent: {r.status_code}")
        return r.status_code == 200
    except Exception as e:
        print(f"Loi gui Telegram: {e}")
        return False

def get_mexc_sol_tokens():
    """Lay danh sach token co mang SOL tren MEXC"""
    try:
        url = "https://api.mexc.com/api/v3/capital/config/getall"
        r = requests.get(url, timeout=15)
        data = r.json()
        sol_tokens = []
        for coin in data:
            coin_name = coin.get("coin", "")
            networks = coin.get("networkList", [])
            for net in networks:
                if net.get("network", "").upper() == "SOL" and net.get("depositEnable", False):
                    contract = net.get("contract", "")  # mint address Solana
                    sol_tokens.append({
                        "symbol": coin_name,
                        "contract": contract,
                        "network": "SOL",
                        "withdrawEnable": net.get("withdrawEnable", False)
                    })
        print(f"[MEXC] Tim thay {len(sol_tokens)} token mang SOL")
        return sol_tokens
    except Exception as e:
        print(f"Loi lay MEXC SOL tokens: {e}")
        return []

def get_mexc_price(symbol):
    """Lay gia tren MEXC: SYMBOL/USDT"""
    try:
        # Thu USDT pair
        for quote in ["USDT", "USDC"]:
            pair = f"{symbol}{quote}"
            url = f"https://api.mexc.com/api/v3/ticker/price?symbol={pair}"
            r = requests.get(url, timeout=10)
            if r.status_code == 200:
                data = r.json()
                price = float(data.get("price", 0))
                if price > 0:
                    return price, pair
        return None, None
    except Exception as e:
        print(f"Loi gia MEXC {symbol}: {e}")
        return None, None

def get_sol_dex_price(contract_or_symbol):
    """Lay gia tren DEX Solana qua DexScreener + Jupiter"""
    price = None
    source = ""
    # Thu DexScreener neu co contract (mint address)
    if contract_or_symbol and len(contract_or_symbol) > 30:  # mint address dai
        try:
            url = f"https://api.dexscreener.com/latest/dex/tokens/{contract_or_symbol}"
            r = requests.get(url, timeout=10)
            data = r.json()
            pairs = data.get("pairs", [])
            if pairs:
                # Lay pair co thanh khoan cao nhat
                best = max(pairs, key=lambda x: float(x.get("liquidity", {}).get("usd", 0) or 0))
                price = float(best.get("priceUsd", 0))
                source = f"DexScreener/{best.get('dexId')}"
                if price and price > 0:
                    return price, source
        except Exception as e:
            print(f"Loi DexScreener {contract_or_symbol}: {e}")

    # Fallback: Jupiter Price API cho token symbol (neu la token lon)
    try:
        # Jupiter can tim bang symbol, nhung can mint - o day dung co che search
        # Voi token khong co mint, bo qua
        pass
    except:
        pass

    return price, source

def main_loop():
    print("="*60)
    print("BOT FULL SOL-MEXC ARBITRAGE BAT DAU")
    print(f"Thoi gian: {datetime.now()}")
    print(f"Nguong chenh lech: {PRICE_DIFF_THRESHOLD}%")
    print("="*60)

    send_telegram(f"🚀 <b>BOT SOL-MEXC FULL DA BAT DAU</b>\n\n"
                  f"Nhiem vu:\n"
                  f"1. Quet token mang SOL tren MEXC\n"
                  f"2. So sanh gia MEXC vs DEX Solana\n"
                  f"3. Bao khi chenh > {PRICE_DIFF_THRESHOLD}%\n\n"
                  f"Check moi {CHECK_INTERVAL}s")

    while True:
        try:
            sol_tokens = get_mexc_sol_tokens()
            # Chi check 30 token dau moi lan de tranh bi rate limit
            # Uu tien token moi hoac co thanh khoan
            for token in sol_tokens[:40]:
                symbol = token["symbol"]
                contract = token["contract"]

                if not contract:
                    continue  # bo qua token khong co mint

                mexc_price, mexc_pair = get_mexc_price(symbol)
                if not mexc_price:
                    continue

                dex_price, dex_source = get_sol_dex_price(contract)
                if not dex_price:
                    continue

                diff_percent = abs(mexc_price - dex_price) / dex_price * 100

                print(f"{symbol}: MEXC={mexc_price:.6f} | DEX={dex_price:.6f} ({dex_source}) | Diff={diff_percent:.2f}%")

                if diff_percent >= PRICE_DIFF_THRESHOLD:
                    key = f"{symbol}_{int(mexc_price*1000)}"
                    if key in notified_tokens:
                        continue  # da bao roi
                    notified_tokens.add(key)

                    # Tinh loi nhuan
                    if mexc_price < dex_price:
                        action = "MUA MEXC -> BAN DEX"
                        profit = f"+{diff_percent:.2f}%"
                    else:
                        action = "MUA DEX -> BAN MEXC"
                        profit = f"+{diff_percent:.2f}%"

                    msg = (
                        f"💰 <b>CO HOI ARBITRAGE SOL!</b>\n\n"
                        f"Token: <b>{symbol}</b>\n"
                        f"Mang: SOL (Solana)\n"
                        f"Contract: <code>{contract[:12]}...{contract[-6:]}</code>\n\n"
                        f"📊 Gia MEXC ({mexc_pair}): <b>${mexc_price:.6f}</b>\n"
                        f"📊 Gia DEX ({dex_source}): <b>${dex_price:.6f}</b>\n"
                        f"📈 Chênh lệch: <b>{profit}</b>\n"
                        f"🎯 Huong: {action}\n\n"
                        f"🔗 MEXC: https://www.mexc.com/exchange/{mexc_pair}\n"
                        f"🔗 DexScreener: https://dexscreener.com/solana/{contract}\n\n"
                        f"⏰ {datetime.now().strftime('%H:%M:%S %d/%m')}"
                    )
                    send_telegram(msg)
                    time.sleep(2)

            print(f"--- Xong 1 vong, nghi {CHECK_INTERVAL}s ---")
            time.sleep(CHECK_INTERVAL)

        except Exception as e:
            print(f"Loi main loop: {e}")
            time.sleep(30)

if __name__ == "__main__":
    main_loop()
