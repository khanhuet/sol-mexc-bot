"""
BOT ARBITRUM vs MEXC - V1 PRO
Mang Arbitrum One (chainId 42161) - L2 Ethereum
"""
import requests, time, os
from datetime import datetime, timedelta

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "8942050944:AAF6tftI7lTYErDCSnLhQSG1BXkUeU7-m3E")
CHAT_ID = os.getenv("CHAT_ID", "469374705")

THRESHOLD = 0.2
MIN_LIQUIDITY_USD = 3000
MIN_ORDERBOOK_DEPTH_USDT = 100
COOLDOWN_MINUTES = 30
CHECK_INTERVAL = 60
SCAN_LIMIT_PER_ROUND = 100

alert_history = {}

def send_telegram(msg):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = {"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML", "disable_web_page_preview": True}
        requests.post(url, data=data, timeout=15)
    except Exception as e:
        print(f"[TG ARB ERR] {e}")

def get_mexc_arbitrum_tokens():
    try:
        url = "https://api.mexc.com/api/v3/capital/config/getall"
        r = requests.get(url, timeout=20)
        data = r.json()
        tokens = []
        for coin in data:
            coin_name = coin.get("coin")
            for net in coin.get("networkList", []):
                net_upper = net.get("network","").upper()
                if net_upper in ["ARBITRUM","ARB","ARBITRUM ONE","ARBITRUMONE","ARBITRUM (ONE)"] and net.get("depositEnable"):
                    contract = net.get("contract","").strip()
                    if contract and contract.startswith("0x") and len(contract)==42:
                        tokens.append({"symbol": coin_name, "contract": contract, "raw_network": net.get("network")})
                    elif not contract:
                        tokens.append({"symbol": coin_name, "contract": "", "raw_network": net.get("network")})
        print(f"[MEXC ARB] {len(tokens)} token ARBITRUM")
        uniq = {t["symbol"]: t for t in tokens}
        if len(uniq) < 15:
            try:
                url = "https://api.dexscreener.com/latest/dex/search/?q=chainId:arbitrum"
                r = requests.get(url, timeout=15)
                j = r.json()
                for p in j.get("pairs", [])[:120]:
                    if p.get("chainId")!="arbitrum": continue
                    if float(p.get("liquidity",{}).get("usd",0) or 0) < MIN_LIQUIDITY_USD: continue
                    sym = p.get("baseToken",{}).get("symbol","").upper()
                    if not sym or sym in uniq: continue
                    uniq[sym] = {"symbol": sym, "contract": p.get("baseToken",{}).get("address",""), "raw_network": "ARBITRUM_DEX"}
            except Exception as e:
                print(f"[ARB DEX SEARCH ERR] {e}")
        return list(uniq.values())
    except Exception as e:
        print(f"[MEXC ARB ERR] {e}")
        return []

def get_mexc_bid_ask(symbol):
    for quote in ["USDT","USDC"]:
        pair = f"{symbol}{quote}"
        try:
            url = f"https://api.mexc.com/api/v3/depth?symbol={pair}&limit=5"
            r = requests.get(url, timeout=10)
            if r.status_code!=200: continue
            ob = r.json()
            bids = ob.get("bids", []); asks = ob.get("asks", [])
            if not bids or not asks: continue
            best_bid = float(bids[0][0]); best_ask = float(asks[0][0])
            bid_depth = sum(float(p)*float(q) for p,q in bids[:5])
            ask_depth = sum(float(p)*float(q) for p,q in asks[:5])
            if bid_depth < MIN_ORDERBOOK_DEPTH_USDT or ask_depth < MIN_ORDERBOOK_DEPTH_USDT:
                continue
            return {"pair": pair, "bid": best_bid, "ask": best_ask, "bid_depth": bid_depth, "ask_depth": ask_depth}
        except:
            continue
    return None

def get_arb_dex_pool(symbol_or_contract):
    if symbol_or_contract and symbol_or_contract.startswith("0x") and len(symbol_or_contract)==42:
        try:
            url = f"https://api.dexscreener.com/latest/dex/tokens/{symbol_or_contract}"
            r = requests.get(url, timeout=12)
            data = r.json()
            pairs = [p for p in data.get("pairs",[]) if p.get("chainId")=="arbitrum" and float(p.get("liquidity",{}).get("usd",0) or 0)>=MIN_LIQUIDITY_USD]
            if pairs:
                best = max(pairs, key=lambda x: float(x.get("liquidity",{}).get("usd",0)))
                return {"price": float(best.get("priceUsd")), "dex": best.get("dexId"), "liquidity": float(best.get("liquidity",{}).get("usd",0)), "url": best.get("url"), "pair_name": f"{best.get('baseToken',{}).get('symbol')}/{best.get('quoteToken',{}).get('symbol')}", "volume": best.get("volume",{}).get("h24"), "contract": best.get("baseToken",{}).get("address")}
        except Exception as e:
            print(f"[ARB DEX contract ERR] {e}")
    try:
        url = f"https://api.dexscreener.com/latest/dex/search/?q={symbol_or_contract} chain:arbitrum"
        r = requests.get(url, timeout=12)
        data = r.json()
        pairs = [p for p in data.get("pairs",[]) if p.get("chainId")=="arbitrum" and float(p.get("liquidity",{}).get("usd",0) or 0)>=MIN_LIQUIDITY_USD]
        if pairs:
            exact = [p for p in pairs if p.get("baseToken",{}).get("symbol","").upper()==symbol_or_contract.upper()]
            pool_list = exact if exact else pairs
            best = max(pool_list, key=lambda x: float(x.get("liquidity",{}).get("usd",0)))
            return {"price": float(best.get("priceUsd")), "dex": best.get("dexId"), "liquidity": float(best.get("liquidity",{}).get("usd",0)), "url": best.get("url"), "pair_name": f"{best.get('baseToken',{}).get('symbol')}/{best.get('quoteToken',{}).get('symbol')}", "volume": best.get("volume",{}).get("h24"), "contract": best.get("baseToken",{}).get("address")}
    except Exception as e:
        print(f"[ARB DEX search ERR {symbol_or_contract}] {e}")
    return None

def should_alert(symbol):
    now = datetime.now()
    last = alert_history.get(symbol)
    if last and now - last < timedelta(minutes=COOLDOWN_MINUTES):
        return False
    return True

def main():
    print("="*70)
    print("BOT ARBITRUM vs MEXC - ChainId 42161 - L2 ETH")
    print("="*70)
    send_telegram(f"🔷 <b>BOT ARBITRUM DA BAT DAU</b>\n\nChainId: 42161 (Arbitrum One)\nDEX: Uniswap V3 / Camelot\nNguong: {THRESHOLD}%\nQuet moi {CHECK_INTERVAL}s")
    tokens = get_mexc_arbitrum_tokens()
    last_refresh = datetime.now()
    idx=0
    while True:
        try:
            if datetime.now() - last_refresh > timedelta(hours=1):
                tokens = get_mexc_arbitrum_tokens()
                last_refresh = datetime.now()
                idx=0
            if not tokens:
                time.sleep(30)
                continue
            batch = tokens[idx:idx+SCAN_LIMIT_PER_ROUND]
            if len(batch)<SCAN_LIMIT_PER_ROUND:
                batch+= tokens[:SCAN_LIMIT_PER_ROUND-len(batch)]
                idx=0
            else:
                idx+=SCAN_LIMIT_PER_ROUND
                if idx>=len(tokens): idx=0
            print(f"\n[ARB VONG] {datetime.now().strftime('%H:%M:%S')} - {len(batch)} token")
            for tok in batch:
                time.sleep(0.3)
                symbol = tok["symbol"]
                contract = tok.get("contract","")
                mexc = get_mexc_bid_ask(symbol)
                if not mexc: continue
                dex = get_arb_dex_pool(contract if contract else symbol)
                if not dex: continue
                dex_price = dex["price"]
                spread_buy_mexc = (dex_price - mexc["ask"]) / mexc["ask"] * 100
                spread_buy_dex = (mexc["bid"] - dex_price) / dex_price * 100
                print(f"{symbol}: MEXC {mexc['bid']}/{mexc['ask']} | ARB {dex_price:.6f} ({dex['dex']} liq${dex['liquidity']:.0f}) | S1={spread_buy_mexc:.2f}% S2={spread_buy_dex:.2f}%")
                best=None; direction=""
                if spread_buy_mexc>=THRESHOLD:
                    best=spread_buy_mexc; direction=f"MUA {mexc['pair']} ASK -> BAN ARBITRUM"
                elif spread_buy_dex>=THRESHOLD:
                    best=spread_buy_dex; direction=f"MUA ARBITRUM -> BAN {mexc['pair']} BID"
                if best and should_alert(symbol):
                    alert_history[symbol]=datetime.now()
                    msg = (
                        f"🔷 <b>ARBITRAGE ARBITRUM - {symbol}</b>\n\n"
                        f"Chain: Arbitrum (42161) | DEX: {dex['dex']} | Liq: ${dex['liquidity']:,.0f}\n\n"
                        f"📊 <b>MEXC {mexc['pair']}</b>\n"
                        f"  Bid: ${mexc['bid']:.6f} (depth ${mexc['bid_depth']:.0f})\n"
                        f"  Ask: ${mexc['ask']:.6f} (depth ${mexc['ask_depth']:.0f})\n\n"
                        f"📊 <b>Arbitrum DEX</b> ({dex['pair_name']})\n"
                        f"  Price: ${dex_price:.6f}\n"
                        f"  Vol24h: ${dex.get('volume',0) or 0}\n\n"
                        f"📈 <b>Chenh lech: {best:.3f}%</b>\n"
                        f"🎯 {direction}\n\n"
                        f"🔗 MEXC: https://www.mexc.com/exchange/{mexc['pair']}\n"
                        f"🔗 DEX: {dex['url']}\n"
                        f"🔗 Arbiscan: https://arbiscan.io/address/{dex.get('contract',contract)}\n\n"
                        f"⏰ {datetime.now().strftime('%H:%M:%S %d/%m')} | Cooldown {COOLDOWN_MINUTES}p"
                    )
                    send_telegram(msg)
                    time.sleep(1.5)
            print(f"--- ARB Nghi {CHECK_INTERVAL}s ---")
            time.sleep(CHECK_INTERVAL)
        except Exception as e:
            print(f"[ARB MAIN ERR] {e}")
            import traceback; traceback.print_exc()
            time.sleep(20)

if __name__=="__main__":
    main()
