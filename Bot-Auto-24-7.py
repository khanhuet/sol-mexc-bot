"""
BOT TU DONG 100% - SOL vs MEXC - chay 24/7
Khong can them token thu cong - tu quet toan bo token SOL tren MEXC
"""

import requests
import time
import json
import os
from datetime import datetime

# ============== DIEN THONG TIN ==============
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "Dien token vao day neu chay local")
CHAT_ID = os.getenv("CHAT_ID", "Dien chat_id vao day")
# ============================================

last_alert = {}  # cooldown 1 phut
known_mexc_symbols = set()

def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown", "disable_web_page_preview": True}
    try:
        requests.post(url, json=data, timeout=10)
        print(f"-> Da gui Telegram")
    except Exception as e:
        print(f"Loi Telegram: {e}")

def get_all_sol_tokens_from_jupiter():
    """Lay toan bo token SOL verified tu Jupiter - co ca mint va symbol"""
    try:
        # Jupiter token list verified
        url = "https://price.jup.ag/v6/tokens"
        # Hoac dung cache token list chuan hon
        url2 = "https://tokens.jup.ag/tokens?tags=verified"
        # Thu url2 truoc
        res = requests.get(url2, timeout=15).json()
        # Tao dict symbol -> mint (lay mint dau tien neu trung symbol)
        mapping = {}
        for t in res:
            sym = t.get('symbol','').upper()
            if sym and sym not in mapping:
                mapping[sym] = t.get('address')
        print(f"Da lay {len(mapping)} token SOL tu Jupiter")
        return mapping
    except Exception as e:
        print(f"Loi lay Jupiter token list: {e}")
        # Fallback list nho
        return {"WIF":"EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm", "BONK":"DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"}

def get_mexc_all_usdt_pairs():
    """Lay tat ca cap USDT tren MEXC"""
    try:
        url = "https://api.mexc.com/api/v3/ticker/price"
        res = requests.get(url, timeout=10).json()
        # Chi lay cap USDT
        usdt_pairs = {}
        for item in res:
            if item['symbol'].endswith('USDT'):
                base = item['symbol'].replace('USDT','')
                usdt_pairs[base] = float(item['price'])
        return usdt_pairs
    except Exception as e:
        print(f"Loi MEXC: {e}")
        return {}

def get_jupiter_batch_prices(mints):
    """Lay gia hang loat tu Jupiter - toi da 100 mint 1 lan"""
    try:
        # Chia nho neu qua nhieu
        prices = {}
        chunk_size = 100
        mint_list = list(mints)
        for i in range(0, len(mint_list), chunk_size):
            chunk = mint_list[i:i+chunk_size]
            ids = ",".join(chunk)
            url = f"https://api.jup.ag/price/v2?ids={ids}"
            res = requests.get(url, timeout=10).json()
            data = res.get('data', {})
            for mint, info in data.items():
                if info and 'price' in info:
                    prices[mint] = float(info['price'])
        return prices
    except Exception as e:
        print(f"Loi Jupiter price: {e}")
        return {}

def check_arbitrage_auto():
    print(f"\n=== Quet tu dong luc {datetime.now().strftime('%H:%M:%S')} ===")
    
    # 1. Lay danh sach token SOL va gia MEXC
    sol_mapping = get_all_sol_tokens_from_jupiter()  # symbol -> mint
    mexc_prices = get_mexc_all_usdt_pairs()  # symbol -> price
    
    # 2. Tim giao nhau: token vua co tren SOL vua co tren MEXC
    common_symbols = set(sol_mapping.keys()) & set(mexc_prices.keys())
    print(f"Tim thay {len(common_symbols)} token chung giua SOL va MEXC")
    
    if not common_symbols:
        return
    
    # 3. Lay gia DEX cho cac token chung
    mints_to_check = [sol_mapping[s] for s in common_symbols]
    jup_prices_by_mint = get_jupiter_batch_prices(mints_to_check)
    # Dao nguoc: mint -> symbol
    mint_to_symbol = {v:k for k,v in sol_mapping.items()}
    
    # 4. So sanh
    for mint, dex_price in jup_prices_by_mint.items():
        symbol = mint_to_symbol.get(mint)
        if not symbol:
            continue
        cex_price = mexc_prices.get(symbol)
        if not cex_price or dex_price == 0:
            continue
        
        diff = (dex_price - cex_price) / cex_price * 100
        abs_diff = abs(diff)
        
        # Chi in ra neu chenh > 2% de de debug
        if abs_diff > 2:
            print(f"{symbol}: DEX {dex_price:.6f} | MEXC {cex_price:.6f} | {diff:+.2f}%")
        
        if abs_diff > 3:
            now = time.time()
            if symbol in last_alert and (now - last_alert[symbol] < 60):
                continue
            last_alert[symbol] = now
            
            link_mexc = f"https://www.mexc.com/exchange/{symbol}_USDT"
            text = f"""⚡️ CHENH LECH {abs_diff:.2f}% - {symbol}

Token: *{symbol}*
Contract: `{mint}`

Gia MEXC: ${cex_price:.6f}
Gia DEX: ${dex_price:.6f}
Chenh: {diff:+.2f}%

Huong: {"MUA MEXC BAN DEX" if diff>0 else "MUA DEX BAN MEXC"}

Link MEXC: {link_mexc}
Dex: https://jup.ag/swap/USDT-{mint}
Solscan: https://solscan.io/token/{mint}
"""
            send_telegram(text)

def check_new_listing_auto():
    global known_mexc_symbols
    try:
        url = "https://api.mexc.com/api/v3/exchangeInfo"
        res = requests.get(url, timeout=10).json()
        current = set([s['symbol'] for s in res['symbols'] if s['status']=='ENABLED' and s['symbol'].endswith('USDT')])
        
        if not known_mexc_symbols:
            known_mexc_symbols = current
            print(f"Luu {len(current)} cap USDT lam moc")
            return
        
        new_list = current - known_mexc_symbols
        if new_list:
            sol_map = get_all_sol_tokens_from_jupiter()
            for full_sym in new_list:
                base = full_sym.replace('USDT','')
                if base in sol_map:
                    mint = sol_map[base]
                    text = f"""🆕 *{base} DA LIET KE TREN MEXC* - Mang SOL

Token: *{base}*
Contract: `{mint}`

Link giao dich: https://www.mexc.com/exchange/{base}_USDT
Solscan: https://solscan.io/token/{mint}

Co hoi chenh lech moi!
"""
                    send_telegram(text)
            known_mexc_symbols = current
    except Exception as e:
        print(f"Loi new listing: {e}")

if __name__ == "__main__":
    print("BOT TU DONG BAT DAU")
    send_telegram("Bot SOL AUTO da bat dau - Tu quet toan bo token SOL tren MEXC")
    
    count = 0
    while True:
        try:
            check_arbitrage_auto()
            count += 1
            if count % 5 == 0:
                check_new_listing_auto()
            time.sleep(20)
        except Exception as e:
            print(f"Loi loop: {e}")
            time.sleep(10)
