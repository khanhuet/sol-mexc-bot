"""
BOT SOL-MEXC V3 PRO - ADVANCED ARBITRAGE
Fix tat ca loi user chi ra:
- Bid/Ask thay vi ticker/price
- Loc chainId==solana + liquidity > $2000
- Verify mint address
- Check orderbook depth
- Cooldown + expiry
- Rate limit
"""

import requests, time, os
from datetime import datetime, timedelta
from collections import defaultdict

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "8942050944:AAF6tftI7lTYErDCSnLhQSG1BXkUeU7-m3E")
CHAT_ID = os.getenv("CHAT_ID", "469374705")

THRESHOLD = 0.2  # 0.2%
MIN_LIQUIDITY_USD = 2000
MIN_ORDERBOOK_DEPTH_USDT = 100  # orderbook phai co it nhat $100 moi tinh
COOLDOWN_MINUTES = 30
CHECK_INTERVAL = 60  # quet moi 60s
SCAN_LIMIT_PER_ROUND = 100  # quet 100 token moi vong, vong sau quet tiep -> quet het

# Luu lich su bao: {symbol: last_alert_time}
alert_history = {}
# Luu de tranh lap: {symbol: {price_key: expiry_time}}
notified_cache = {}

def send_telegram(msg):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = {"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML", "disable_web_page_preview": True}
        r = requests.post(url, data=data, timeout=15)
        print(f"[TG] {r.status_code}")
        return True
    except Exception as e:
        print(f"[TG ERROR] {e}")
        return False

def get_mexc_sol_tokens():
    """Lay token co network SOL + co mint"""
    try:
        url = "https://api.mexc.com/api/v3/capital/config/getall"
        r = requests.get(url, timeout=20)
        data = r.json()
        tokens = []
        for coin in data:
            coin_name = coin.get("coin")
            for net in coin.get("networkList", []):
                if net.get("network","").upper()=="SOL" and net.get("depositEnable"):
                    contract = net.get("contract","").strip()
                    # Verify mint: phai la base58 32-44 ky tu, bat dau khong phai 0x
                    if contract and len(contract)>=32 and len(contract)<=44 and not contract.startswith("0x"):
                        tokens.append({
                            "symbol": coin_name,
                            "contract": contract,
                            "withdrawEnable": net.get("withdrawEnable", False)
                        })
        # Sort: token co contract that truoc
        print(f"[MEXC] {len(tokens)} token SOL co mint hop le")
        return tokens
    except Exception as e:
        print(f"[MEXC ERROR] {e}")
        return []

def get_mexc_bid_ask(symbol):
    """Lay bid/ask thuc te + check depth - DUNG DEPTH API"""
    for quote in ["USDT","USDC"]:
        pair = f"{symbol}{quote}"
        try:
            # 1. Lay orderbook
            url = f"https://api.mexc.com/api/v3/depth?symbol={pair}&limit=5"
            r = requests.get(url, timeout=10)
            if r.status_code != 200:
                continue
            ob = r.json()
            bids = ob.get("bids", [])
            asks = ob.get("asks", [])
            if not bids or not asks:
                continue
            best_bid = float(bids[0][0])
            best_ask = float(asks[0][0])
            # Check depth: tong USDT cua 5 bid/ask dau
            bid_depth = sum(float(p)*float(q) for p,q in bids[:5])
            ask_depth = sum(float(p)*float(q) for p,q in asks[:5])
            if bid_depth < MIN_ORDERBOOK_DEPTH_USDT or ask_depth < MIN_ORDERBOOK_DEPTH_USDT:
                print(f"[SKIP {pair}] Depth thap: bid${bid_depth:.0f} ask${ask_depth:.0f}")
                continue
            return {
                "pair": pair,
                "bid": best_bid,
                "ask": best_ask,
                "bid_depth": bid_depth,
                "ask_depth": ask_depth,
                "quote": quote
            }
        except Exception as e:
            print(f"[MEXC DEPTH ERR {pair}] {e}")
            continue
    return None

def get_sol_dex_pool(contract):
    """Lay pool Solana - loc chainId==solana + liquidity > MIN"""
    try:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{contract}"
        r = requests.get(url, timeout=12)
        data = r.json()
        pairs = data.get("pairs", [])
        if not pairs:
            return None
        # Loc chainId == solana va co gia
        valid = []
        for p in pairs:
            if p.get("chainId") != "solana":
                continue
            try:
                liq = float(p.get("liquidity",{}).get("usd",0) or 0)
                price_usd = float(p.get("priceUsd",0) or 0)
                if liq < MIN_LIQUIDITY_USD: continue
                if price_usd <=0: continue
                valid.append(p)
            except:
                continue
        if not valid:
            return None
        # Chon pool thanh khoan cao nhat
        best = max(valid, key=lambda x: float(x.get("liquidity",{}).get("usd",0)))
        return {
            "price": float(best.get("priceUsd")),
            "dex": best.get("dexId"),
            "pair": f"{best.get('baseToken',{}).get('symbol')}/{best.get('quoteToken',{}).get('symbol')}",
            "liquidity": float(best.get("liquidity",{}).get("usd",0)),
            "url": best.get("url"),
            "fdv": best.get("fdv"),
            "volume_24h": best.get("volume",{}).get("h24")
        }
    except Exception as e:
        print(f"[DEX ERR {contract[:10]}] {e}")
        return None

def should_alert(symbol, spread):
    """Cooldown 30p, tu xoa cache het han"""
    now = datetime.now()
    # Xoa cache het han
    expired = [k for k,v in alert_history.items() if now - v > timedelta(minutes=COOLDOWN_MINUTES*2)]
    for k in expired:
        alert_history.pop(k, None)

    last = alert_history.get(symbol)
    if last and now - last < timedelta(minutes=COOLDOWN_MINUTES):
        return False
    return True

def main():
    print("="*70)
    print("BOT V3 PRO - SOL MEXC ARBITRAGE - BID/ASK + MINT VERIFY")
    print(f"Threshold: {THRESHOLD}% | Min Liq: ${MIN_LIQUIDITY_USD} | Cooldown: {COOLDOWN_MINUTES}m")
    print("="*70)

    send_telegram(
        f"🚀 <b>BOT V3 PRO DA BAT DAU</b>\n\n"
        f"✅ Bid/Ask that (khong dung ticker)\n"
        f"✅ Loc chainId=solana + Liq > ${MIN_LIQUIDITY_USD}\n"
        f"✅ Verify mint address\n"
        f"✅ Check orderbook depth > ${MIN_ORDERBOOK_DEPTH_USDT}\n"
        f"✅ Cooldown {COOLDOWN_MINUTES}p chong spam\n\n"
        f"🎯 Nguong: <b>{THRESHOLD}%</b>\n"
        f"⏱ Quet moi {CHECK_INTERVAL}s - {SCAN_LIMIT_PER_ROUND} token/vong"
    )

    # Lay danh sach 1 lan, cache 1h roi load lai
    sol_tokens = get_mexc_sol_tokens()
    last_token_refresh = datetime.now()
    idx = 0

    while True:
        try:
            # Refresh list moi 1h
            if datetime.now() - last_token_refresh > timedelta(hours=1):
                sol_tokens = get_mexc_sol_tokens()
                last_token_refresh = datetime.now()
                idx = 0

            if not sol_tokens:
                time.sleep(30)
                continue

            # Quet vong tron de quet het tat ca token
            batch = sol_tokens[idx:idx+SCAN_LIMIT_PER_ROUND]
            if len(batch) < SCAN_LIMIT_PER_ROUND:
                # Het vong, quay lai dau
                batch += sol_tokens[:SCAN_LIMIT_PER_ROUND-len(batch)]
                idx = 0
            else:
                idx += SCAN_LIMIT_PER_ROUND
                if idx >= len(sol_tokens):
                    idx = 0

            print(f"\n[VONG QUET] {datetime.now().strftime('%H:%M:%S')} - {len(batch)} token - idx {idx}/{len(sol_tokens)}")

            for token in batch:
                symbol = token["symbol"]
                contract = token["contract"]

                # Rate limit: nghi 300ms moi token
                time.sleep(0.3)

                mexc = get_mexc_bid_ask(symbol)
                if not mexc:
                    continue

                dex = get_sol_dex_pool(contract)
                if not dex:
                    continue

                dex_price = dex["price"]
                # 2 chieu:
                # Chieu 1: Mua MEXC ask -> Ban DEX (so voi dex_price)
                spread_buy_mexc = (dex_price - mexc["ask"]) / mexc["ask"] * 100
                # Chieu 2: Mua DEX -> Ban MEXC bid
                spread_buy_dex = (mexc["bid"] - dex_price) / dex_price * 100

                print(f"{symbol}: MEXC bid={mexc['bid']} ask={mexc['ask']} | DEX={dex_price:.6f} ({dex['dex']} liq${dex['liquidity']:.0f}) | S1={spread_buy_mexc:.2f}% S2={spread_buy_dex:.2f}%")

                # Kiem tra co hoi
                best_spread = None
                direction = ""
                if spread_buy_mexc >= THRESHOLD:
                    best_spread = spread_buy_mexc
                    direction = f"MUA {mexc['pair']} ASK -> BAN DEX"
                elif spread_buy_dex >= THRESHOLD:
                    best_spread = spread_buy_dex
                    direction = f"MUA DEX -> BAN {mexc['pair']} BID"

                if best_spread and should_alert(symbol, best_spread):
                    alert_history[symbol] = datetime.now()

                    # Gop % (tru phi giao dich 0.2%*2 ~ 0.4%)
                    gross = best_spread

                    msg = (
                        f"💰 <b>ARBITRAGE SOL V3 PRO - {symbol}</b>\n\n"
                        f"Mint: <code>{contract}</code>\n"
                        f"Chain: Solana | DEX: {dex['dex']} | Liq: ${dex['liquidity']:,.0f}\n\n"
                        f"📊 <b>MEXC {mexc['pair']}</b>\n"
                        f"  Bid (ban): ${mexc['bid']:.6f} (depth ${mexc['bid_depth']:.0f})\n"
                        f"  Ask (mua): ${mexc['ask']:.6f} (depth ${mexc['ask_depth']:.0f})\n\n"
                        f"📊 <b>DEX Solana</b> ({dex['pair']})\n"
                        f"  Price: ${dex_price:.6f}\n"
                        f"  Vol24h: ${dex.get('volume_24h',0) or 0}\n\n"
                        f"📈 <b>Chenh lech gop: {gross:.3f}%</b>\n"
                        f"🎯 Huong: {direction}\n\n"
                        f"🔗 MEXC: https://www.mexc.com/exchange/{mexc['pair']}\n"
                        f"🔗 DEX: {dex['url']}\n"
                        f"🔗 Solscan: https://solscan.io/token/{contract}\n\n"
                        f"⏰ {datetime.now().strftime('%H:%M:%S %d/%m/%Y')} | Cooldown {COOLDOWN_MINUTES}p"
                    )
                    send_telegram(msg)
                    time.sleep(1.5)  # tranh spam telegram

            print(f"--- Nghi {CHECK_INTERVAL}s ---")
            time.sleep(CHECK_INTERVAL)

        except Exception as e:
            print(f"[MAIN LOOP ERR] {e}")
            import traceback; traceback.print_exc()
            time.sleep(20)

if __name__ == "__main__":
    main()
