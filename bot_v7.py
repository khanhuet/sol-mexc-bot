#!/usr/bin/env python3
"""
BOT V7.1 - REAL PRICE - MEXC <-> DEX with Jupiter + Paraswap
"""
import hashlib, hmac, html, json, logging, os, re, sqlite3, sys, threading, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from urllib.parse import urlencode
import requests

def require_env(name):
    v=os.environ.get(name)
    if not v:
        sys.exit(f"Thieu bien moi truong: {name}")
    return v
def env_float(name, default):
    return float(os.getenv(name, default))

TELEGRAM_TOKEN=require_env("TELEGRAM_TOKEN")
CHAT_ID=require_env("CHAT_ID")
MEXC_API_KEY=require_env("MEXC_API_KEY")
MEXC_SECRET=require_env("MEXC_SECRET")
PORT=int(os.getenv("PORT",10000))
DB_PATH=os.getenv("DB_PATH","paper_trades.db")
ORDER_SIZE_USD=env_float("ORDER_SIZE_USD",500)
THRESHOLD_NET=env_float("THRESHOLD_NET",0.8)
PREFILTER_GROSS=env_float("PREFILTER_GROSS",0.8)
MAX_GROSS=env_float("MAX_GROSS",7.0)
MAX_IMPACT_PCT=env_float("MAX_IMPACT_PCT",0.8)
MIN_LIQ_USD=env_float("MIN_LIQ_USD",80000)
MIN_VOL_24H=env_float("MIN_VOL_24H",40000)
MIN_TXNS_H1=int(env_float("MIN_TXNS_H1",15))
MEXC_TAKER_FEE=env_float("MEXC_TAKER_FEE",0.002)
COOLDOWN_MIN=env_float("COOLDOWN_MIN",25)
SCAN_PAUSE_SEC=env_float("SCAN_PAUSE_SEC",20)
STUCK_SEC=env_float("STUCK_SEC",900)
STATIC_TTL=1800
BOOK_TTL=10
DEX_BATCH=20
FOLLOWUP_MIN=(5,10)
REAL_PRICE_CHECK=os.getenv("REAL_PRICE_CHECK","1")=="1"

MEXC_BASE="https://api.mexc.com"
SOL_USDC="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
SOL_USDT="Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
EVM_USDC={56:"0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d",8453:"0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",1:"0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",137:"0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359",42161:"0xaf88d065e77c8cC2239327C5EDb3A432268e5831"}
EVM_USDT={56:"0x55d398326f99059fF775485246999027B3197955",8453:"0x50c5725949A6F0c72E6F4a641F24049A917DB138",1:"0xdAC17F958D2ee523a2206206994597C13D831ec7",137:"0xc2132D05D31c914a87C6611C10748AEb04B58e8F",42161:"0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9"}

CHAINS={
    "SOL":{"emoji":"🟣","dex_chain":"solana","evm":False,"gas_usd":0.02,"explorer":"https://solscan.io/token/","chain_id":None,"aliases":{"SOL","SOLANA","SOLANASOL"}},
    "BNB":{"emoji":"🟡","dex_chain":"bsc","evm":True,"gas_usd":0.15,"explorer":"https://bscscan.com/token/","chain_id":56,"aliases":{"BSC","BEP20","BSCBEP20","BEP20BSC","BNBSMARTCHAIN"}},
    "BASE":{"emoji":"🔵","dex_chain":"base","evm":True,"gas_usd":0.05,"explorer":"https://basescan.org/token/","chain_id":8453,"aliases":{"BASE","BASECHAIN"}},
    "ETH":{"emoji":"⚫","dex_chain":"ethereum","evm":True,"gas_usd":2.5,"explorer":"https://etherscan.io/token/","chain_id":1,"aliases":{"ERC20","ETH","ETHEREUM"}},
    "POLYGON":{"emoji":"💜","dex_chain":"polygon","evm":True,"gas_usd":0.02,"explorer":"https://polygonscan.com/token/","chain_id":137,"aliases":{"MATIC","POLYGON"}},
    "ARBITRUM":{"emoji":"🔷","dex_chain":"arbitrum","evm":True,"gas_usd":0.15,"explorer":"https://arbiscan.io/token/","chain_id":42161,"aliases":{"ARB","ARBITRUM","ARBITRUMONE"}},
    "ROBINHOOD":{"emoji":"🔶","dex_chain":"robinhood","evm":True,"gas_usd":0.01,"explorer":"https://robinhoodchain.blockscout.com/token/","chain_id":None,"aliases":{"ROBINHOOD","ROBINHOODCHAIN"}},
    "ARC":{"emoji":"🟪","dex_chain":"arc","evm":True,"gas_usd":0.02,"explorer":"https://explorer.arc.io/token/","chain_id":None,"aliases":{"ARC","ARCCHAIN"}},
    "NEAR":{"emoji":"🟢","dex_chain":"near","evm":False,"near":True,"gas_usd":0.01,"explorer":"https://nearblocks.io/token/","chain_id":None,"aliases":{"NEAR","NEARPROTOCOL"}},
}
for _k,_c in CHAINS.items():
    _c["dex_chain"]=os.getenv(f"DEX_CHAIN_{_k}",_c["dex_chain"])
ENABLED=[c.strip().upper() for c in os.getenv("ENABLED_CHAINS",",".join(CHAINS)).split(",") if c.strip()]
CHAINS={k:v for k,v in CHAINS.items() if k in ENABLED}

DEX_FEES={"uniswap":0.003,"pancakeswap":0.0025,"sushiswap":0.003,"quickswap":0.003,"aerodrome":0.003,"camelot":0.003,"raydium":0.0025,"orca":0.003,"meteora":0.003}
DEFAULT_DEX_FEE=0.003

logging.basicConfig(level=os.getenv("LOG_LEVEL","INFO"),format="%(asctime)s %(levelname)s [%(threadName)s] %(message)s")
log=logging.getLogger("bot")
GLOBAL_SEM=threading.Semaphore(12)
_local=threading.local()
def session():
    s=getattr(_local,"s",None)
    if s is None:
        s=_local.s=requests.Session()
    return s
class RateLimiter:
    def __init__(self,mi):
        self.mi=mi;self.next=0.0;self.lock=threading.Lock()
    def wait(self):
        with self.lock:
            now=time.monotonic()
            slot=max(now,self.next)
            self.next=slot+self.mi
        d=slot-now
        if d>0:
            time.sleep(d)
MEXC_LIMITER=RateLimiter(0.05)
DEX_LIMITER=RateLimiter(0.35)
JUP_LIMITER=RateLimiter(0.2)
PARA_LIMITER=RateLimiter(0.3)

def http_get(url,params=None,headers=None,limiter=None,timeout=15,retries=2):
    for attempt in range(retries+1):
        try:
            if limiter:
                limiter.wait()
            with GLOBAL_SEM:
                r=session().get(url,params=params,headers=headers,timeout=timeout)
            if r.status_code==429:
                try:
                    wait=float(r.headers.get("Retry-After",2*(attempt+1)))
                except:
                    wait=2*(attempt+1)
                log.warning("429 tu %s, ngu %.0fs",url.split("?")[0],wait)
                time.sleep(min(wait,30))
                continue
            return r
        except requests.RequestException as e:
            log.warning("HTTP loi %s: %s",url.split("?")[0],e)
            time.sleep(1+attempt)
    return None

def mexc_signed_get(path,params=None):
    params=params or {}
    params["timestamp"]=int(time.time()*1000)
    qs=urlencode(params)
    sig=hmac.new(MEXC_SECRET.encode(),qs.encode(),hashlib.sha256).hexdigest()
    headers={"X-MEXC-APIKEY":MEXC_API_KEY}
    return http_get(f"{MEXC_BASE}{path}?{qs}&signature={sig}",headers=headers,limiter=MEXC_LIMITER)

def esc(s):
    return html.escape(str(s),quote=False)
def fnum(x):
    try:
        return float(x)
    except:
        return 0.0
def norm_addr(chain_key,addr):
    if not addr:
        return ""
    a=addr.strip()
    if CHAINS[chain_key].get("evm"):
        return a.lower()
    return a

class Stat:
    def __init__(self):
        self.lock=threading.Lock()
        self.c={"attempted":0,"fetched":0,"opportunity":0,"signals":0,"suspect":0,"errors":0,"dex_pairs":0,"real_price_ok":0,"real_price_fail":0}
        self.last_beat=datetime.now()
        self.last_success=None
    def inc(self,k,n=1):
        with self.lock:
            self.c[k]=self.c.get(k,0)+n
    def beat(self):
        with self.lock:
            self.last_beat=datetime.now()
    def success(self):
        with self.lock:
            self.last_success=datetime.now()
    def scanned(self):
        pass
    def snapshot(self):
        with self.lock:
            d=dict(self.c)
            d["last_beat"]=self.last_beat
            d["last_success"]=self.last_success
            return d

STATS={k:Stat() for k in CHAINS}

class MexcData:
    def __init__(self):
        self.lock=threading.Lock()
        self.book_lock=threading.Lock()
        self.pairs={}
        self.tokens={}
        self.ts=0
        self.book={}
        self.book_ts=0
        self.retry_after=0
    def _fetch_pairs(self):
        log.info("Dang lay exchangeInfo MEXC...")
        r=http_get(f"{MEXC_BASE}/api/v3/exchangeInfo",limiter=MEXC_LIMITER,timeout=20)
        if not r:
            raise RuntimeError("exchangeInfo no response")
        if r.status_code!=200:
            raise RuntimeError(f"exchangeInfo fail {r.status_code} {r.text[:200]}")
        data=r.json()
        out={}
        for s in data.get("symbols",[]):
            if s.get("quoteAsset")=="USDT" and s.get("status")=="1":
                out[s["symbol"]]=s["baseAsset"]
        log.info("Lay duoc %d pairs", len(out))
        return out
    def _fetch_tokens(self,pairs):
        log.info("Dang lay capital/config...")
        r=mexc_signed_get("/api/v3/capital/config/getall")
        if not r:
            raise RuntimeError("capital/config no response")
        if r.status_code!=200:
            raise RuntimeError(f"capital/config fail {r.status_code} {r.text[:300]}")
        coins=r.json()
        result={k:{} for k in CHAINS}
        unmatched=set()
        for coin in coins:
            sym=coin.get("coin")
            if sym not in pairs.values():
                continue
            for net in coin.get("networkList",[]) or []:
                import re as _re
                net_name=_re.sub(r"[^A-Z0-9]", "", (net.get("network") or "").upper())
                contract=net.get("contract") or ""
                # truoc: yeu cau ca rut va nap deu mo -> de bi 0 token khi MEXC khoa 1 chieu
                # sua: chi can 1 chieu mo la van lay de quet 1 chieu arbitrage
                if not (net.get("withdrawEnable") or net.get("depositEnable")):
                    continue
                found=None
                for ch,cfg in CHAINS.items():
                    if net_name in cfg["aliases"]:
                        found=ch
                        break
                if not found:
                    unmatched.add(net_name)
                    continue
                dedup=norm_addr(found, contract if contract else sym)
                if dedup in result[found]:
                    continue
                result[found][dedup]={
                    "symbol":sym,
                    "contract":contract or sym,
                    "dedup_key":dedup,
                    "chain":found,
                    "pair":next((p for p,s in pairs.items() if s==sym), sym+"USDT"),
                    "raw_network":net.get("network"),
                    "withdraw_fee":fnum(net.get("withdrawFee")),
                    "withdraw_min":fnum(net.get("withdrawMin")),
                }
        final={k:list(v.values()) for k,v in result.items()}
        for k,v in final.items():
            log.info("MEXC %s: %d token hop le",k,len(v))
        return final
    def ensure_static(self):
        with self.lock:
            now=time.time()
            if self.tokens and now-self.ts < 1800:
                return
            if now < self.retry_after:
                log.warning("MEXC retry_after con %.0fs, bo qua", self.retry_after-now)
                return
            try:
                pairs=self._fetch_pairs()
                tokens=self._fetch_tokens(pairs)
                self.pairs,self.tokens,self.ts=pairs,tokens,now
            except Exception as e:
                log.error("Lam moi du lieu MEXC loi: %s", e)
                self.retry_after=now+60
    def get_book(self):
        with self.book_lock:
            if self.book and time.time()-self.book_ts < 10:
                return self.book
            r=http_get(f"{MEXC_BASE}/api/v3/ticker/bookTicker",limiter=MEXC_LIMITER,timeout=20)
            if r and r.status_code==200:
                try:
                    book={}
                    for t in r.json():
                        bid,ask=fnum(t.get("bidPrice")),fnum(t.get("askPrice"))
                        if 0<bid<ask:
                            book[t["symbol"]]=(bid,ask)
                    self.book,self.book_ts=book,time.time()
                except Exception as e:
                    log.warning("bookTicker parse loi: %s",e)
            return self.book

MEXC=MexcData()

def mexc_depth(pair):
    r=http_get(f"{MEXC_BASE}/api/v3/depth",params={"symbol":pair,"limit":100},limiter=MEXC_LIMITER,timeout=12)
    if not r or r.status_code!=200:
        return None
    try:
        j=r.json()
        bids=[(float(p),float(q)) for p,q in j.get("bids",[]) if float(p)>0 and float(q)>0]
        asks=[(float(p),float(q)) for p,q in j.get("asks",[]) if float(p)>0 and float(q)>0]
    except:
        return None
    if len(bids)<5 or len(asks)<5:
        return None
    if asks[0][0]<=bids[0][0] or (asks[0][0]-bids[0][0])/asks[0][0]>0.05:
        return None
    return {"bids":bids,"asks":asks}

def walk_usd(levels, usd):
    left,tok=usd,0.0
    for p,q in levels:
        take=min(left,p*q)
        tok+=take/p
        left-=take
        if left<=1e-9:
            break
    return (usd/tok,tok) if left<=0.01 and tok>0 else None

def walk_tokens(levels, tokens):
    left,usd=tokens,0.0
    for p,q in levels:
        take=min(left,q)
        usd+=take*p
        left-=take
        if left<=1e-12:
            break
    return usd if left<=tokens*1e-6 else None

def fetch_dex_pools(chain_key, addrs):
    cid=CHAINS[chain_key]["dex_chain"]
    r=http_get(f"https://api.dexscreener.com/tokens/v1/{cid}/{','.join(addrs)}",limiter=DEX_LIMITER)
    if not r or r.status_code!=200:
        return None
    try:
        pairs=r.json()
    except:
        return None
    if not isinstance(pairs,list):
        return None
    STATS[chain_key].inc("dex_pairs", sum(1 for p in pairs if p.get("chainId")==cid))
    best={}
    for p in pairs:
        if p.get("chainId")!=cid:
            continue
        base=p.get("baseToken") or {}
        key=norm_addr(chain_key, base.get("address"))
        price=fnum(p.get("priceUsd"))
        liq=fnum((p.get("liquidity") or {}).get("usd"))
        vol=fnum((p.get("volume") or {}).get("h24"))
        h1=(p.get("txns") or {}).get("h1") or {}
        txns=int(fnum(h1.get("buys"))+fnum(h1.get("sells")))
        fdv=fnum(p.get("fdv"))
        if price<=0 or liq < MIN_LIQ_USD or vol < MIN_VOL_24H or txns < MIN_TXNS_H1:
            continue
        if liq>0 and vol/liq < 0.05:
            continue
        if liq>0 and fdv>0 and fdv/liq > 100:
            continue
        if key in best and best[key]["liq"]>=liq:
            continue
        dex_id=(p.get("dexId") or "").lower()
        best[key]={"price":price,"liq":liq,"vol24":vol,"txns_h1":txns,"dex":dex_id,"fee":DEX_FEES.get(dex_id,DEFAULT_DEX_FEE),"url":p.get("url") or "","pair_name":f"{base.get('symbol')}/{(p.get('quoteToken') or {}).get('symbol')}","base_symbol":base.get("symbol") or "","quote_addr":(p.get("quoteToken") or {}).get("address") or "","fdv":fdv}
    return best

def symbol_ok(a,b):
    a,b=re.sub(r"[^A-Z0-9]","",a.upper()),re.sub(r"[^A-Z0-9]","",b.upper())
    return bool(a) and (a==b or (len(a)>=3 and (a in b or b in a)))

def get_jupiter_quote(input_mint, output_mint, amount):
    try:
        url=f"https://quote-api.jup.ag/v6/quote?inputMint={input_mint}&outputMint={output_mint}&amount={amount}&slippageBps=50"
        r=http_get(url,limiter=JUP_LIMITER,timeout=10)
        if not r or r.status_code!=200:
            return None
        j=r.json()
        out=fnum(j.get("outAmount"))
        impact=fnum(j.get("priceImpactPct"))
        if out<=0:
            return None
        return {"outAmount":out,"impact":impact}
    except Exception as e:
        log.warning("Jupiter quote loi: %s",e)
        return None

def get_paraswap_quote(chain_id, src_token, dest_token, src_amount_wei):
    try:
        url="https://apiv5.paraswap.io/prices"
        is_src_stable = src_token.lower() in [v.lower() for v in list(EVM_USDC.values())+list(EVM_USDT.values())]
        is_dest_stable = dest_token.lower() in [v.lower() for v in list(EVM_USDC.values())+list(EVM_USDT.values())]
        params={"srcToken":src_token,"destToken":dest_token,"amount":str(int(src_amount_wei)),"srcDecimals":6 if is_src_stable else 18,"destDecimals":6 if is_dest_stable else 18,"side":"SELL","network":chain_id}
        r=http_get(url,params=params,limiter=PARA_LIMITER,timeout=10)
        if not r or r.status_code!=200:
            return None
        j=r.json()
        pr=j.get("priceRoute") or {}
        dest_amount=fnum(pr.get("destAmount"))
        if dest_amount<=0:
            return None
        return {"destAmount":dest_amount,"priceRoute":pr}
    except Exception as e:
        log.warning("Paraswap quote loi chain %s: %s",chain_id,e)
        return None

def get_real_dex_exec(chain_key, pool, token_contract, direction):
    if not REAL_PRICE_CHECK:
        return None
    cfg=CHAINS[chain_key]
    try:
        if chain_key=="SOL":
            amount_usdc=int(ORDER_SIZE_USD*1_000_000)
            if direction=="DEX_TO_MEXC":
                q=get_jupiter_quote(SOL_USDC, token_contract, amount_usdc)
                if not q:
                    q=get_jupiter_quote(SOL_USDT, token_contract, amount_usdc)
                if not q or q["outAmount"]<=0:
                    return None
                best_price=None
                for dec in (6,9,8):
                    tokens=q["outAmount"]/(10**dec)
                    if tokens<=0:
                        continue
                    price=ORDER_SIZE_USD/tokens
                    if pool["price"]>0 and abs(price-pool["price"])/pool["price"]>0.5:
                        continue
                    best_price=price
                    break
                if best_price is None:
                    tokens=q["outAmount"]/1_000_000
                    best_price=ORDER_SIZE_USD/tokens if tokens>0 else None
                if not best_price:
                    return None
                return {"buy_px":best_price*1.003,"sell_px":None,"impact":q["impact"],"source":"jupiter"}
            else:
                est_tokens=ORDER_SIZE_USD/max(pool["price"],1e-12)
                raw=int(est_tokens*1_000_000)
                q=get_jupiter_quote(token_contract, SOL_USDC, raw)
                if not q or q["outAmount"]<=0:
                    return None
                usdc_out=q["outAmount"]/1_000_000
                sell_price=usdc_out/est_tokens if est_tokens>0 else 0
                return {"buy_px":None,"sell_px":sell_price*0.997,"impact":q["impact"],"source":"jupiter"}
        elif cfg.get("evm") and cfg.get("chain_id") in EVM_USDC:
            chain_id=cfg["chain_id"]
            usdc=EVM_USDC.get(chain_id)
            stable=usdc or EVM_USDT.get(chain_id)
            if not stable:
                return None
            amount_wei=int(ORDER_SIZE_USD*1_000_000)
            if direction=="DEX_TO_MEXC":
                q=get_paraswap_quote(chain_id, stable, token_contract, amount_wei)
                if not q:
                    return None
                best=None
                for dec in (18,6,9,8):
                    tokens=q["destAmount"]/(10**dec)
                    if tokens<=0:
                        continue
                    price=ORDER_SIZE_USD/tokens
                    if pool["price"]>0 and abs(price-pool["price"])/pool["price"]>0.6:
                        continue
                    best=price
                    break
                if best is None:
                    tokens=q["destAmount"]/1e18
                    best=ORDER_SIZE_USD/tokens if tokens>0 else None
                if not best:
                    return None
                return {"buy_px":best*1.003,"sell_px":None,"impact":0.5,"source":"paraswap"}
            else:
                est_tokens=ORDER_SIZE_USD/max(pool["price"],1e-12)
                raw=int(est_tokens*1e18)
                q=get_paraswap_quote(chain_id, token_contract, stable, raw)
                if not q:
                    return None
                usdc_out=q["destAmount"]/1_000_000
                sell_price=usdc_out/est_tokens if est_tokens>0 else 0
                return {"buy_px":None,"sell_px":sell_price*0.997,"impact":0.5,"source":"paraswap"}
    except Exception as e:
        log.warning("Real price check loi %s %s: %s",chain_key, token_contract[:8], e)
    return None

def dex_prices_estimate(pool):
    impact_pct=2*ORDER_SIZE_USD/max(pool["liq"],1)*100
    f,i=pool["fee"],impact_pct/100
    return impact_pct, pool["price"]*(1+f+i), pool["price"]*(1-f-i)

def evaluate(chain_key, tok, pool, depth):
    usd=ORDER_SIZE_USD
    gas=CHAINS[chain_key]["gas_usd"]
    impact_est,buy_est,sell_est=dex_prices_estimate(pool)
    if impact_est>MAX_IMPACT_PCT*1.5:
        return []
    bids,asks=depth["bids"],depth["asks"]
    if asks and bids:
        spread=(asks[0][0]-bids[0][0])/asks[0][0]*100
        if spread>2.0:
            return []
    out=[]
    w=walk_usd(asks, usd)
    if w:
        avg,tokens=w
        tokens_net=tokens - tok["withdraw_fee"]
        if tokens_net>0 and tokens>=tok["withdraw_min"]:
            real_sell=sell_est
            real_impact=impact_est
            real_source="estimate"
            if REAL_PRICE_CHECK:
                rq=get_real_dex_exec(chain_key, pool, tok["contract"], "MEXC_TO_DEX")
                if rq and rq.get("sell_px"):
                    real_sell=rq["sell_px"]
                    real_impact=rq.get("impact",real_impact)
                    real_source=rq.get("source","real")
                    STATS[chain_key].inc("real_price_ok")
                else:
                    if chain_key in ("SOL","BNB","BASE","ETH","POLYGON","ARBITRUM"):
                        STATS[chain_key].inc("real_price_fail")
            cost=usd*(1+MEXC_TAKER_FEE)
            revenue=tokens_net*real_sell - gas
            net=revenue-cost
            gross=(real_sell/avg-1)*100
            if gross<=MAX_GROSS and real_impact<=MAX_IMPACT_PCT and gross>-5:
                if pool["price"]>0 and abs(real_sell-pool["price"])/pool["price"]>0.15:
                    STATS[chain_key].inc("suspect")
                    log.info("LOAI KEO AO %s %s real %.6f vs dex %.6f diff %.1f%%",chain_key,tok["symbol"],real_sell,pool["price"],abs(real_sell-pool["price"])/pool["price"]*100)
                else:
                    out.append({"direction":"MEXC_TO_DEX","token_amount":tokens,"mexc_avg":avg,"dex_exec":real_sell,"cost":cost,"net_usd":net,"net_pct":net/cost*100,"gross_pct":gross,"gas":gas,"withdraw_fee_usd":tok["withdraw_fee"]*avg,"impact_dex":real_impact,"mexc_best":(bids[0][0],asks[0][0]),"real_source":real_source})
    real_buy=buy_est
    real_impact_buy=impact_est
    real_source_buy="estimate"
    if REAL_PRICE_CHECK:
        rq=get_real_dex_exec(chain_key, pool, tok["contract"], "DEX_TO_MEXC")
        if rq and rq.get("buy_px"):
            real_buy=rq["buy_px"]
            real_impact_buy=rq.get("impact",real_impact_buy)
            real_source_buy=rq.get("source","real")
    tokens_needed=usd/max(real_buy,1e-12)
    proceeds=walk_tokens(bids, tokens_needed)
    if proceeds:
        cost=usd+gas*2
        revenue=proceeds*(1-MEXC_TAKER_FEE)
        net=revenue-cost
        avg=proceeds/tokens_needed if tokens_needed>0 else 0
        gross=(avg/real_buy-1)*100 if real_buy>0 else 0
        if gross<=MAX_GROSS and real_impact_buy<=MAX_IMPACT_PCT and gross>-5:
            if pool["price"]>0 and abs(real_buy-pool["price"])/pool["price"]>0.15:
                STATS[chain_key].inc("suspect")
                log.info("LOAI KEO AO %s %s real buy %.6f vs dex %.6f",chain_key,tok["symbol"],real_buy,pool["price"])
            else:
                out.append({"direction":"DEX_TO_MEXC","token_amount":tokens_needed,"mexc_avg":avg,"dex_exec":real_buy,"cost":cost,"net_usd":net,"net_pct":net/cost*100,"gross_pct":gross,"gas":gas*2,"withdraw_fee_usd":0.0,"impact_dex":real_impact_buy,"mexc_best":(bids[0][0],asks[0][0]),"real_source":real_source_buy})
    return out

COOLDOWN={}
CD_LOCK=threading.Lock()
def should_alert(key, net_pct):
    with CD_LOCK:
        last=COOLDOWN.get(key)
    if not last:
        return True
    t,p=last
    age=(datetime.now()-t).total_seconds()
    if age>=COOLDOWN_MIN*60:
        return True
    return age>=300 and net_pct>p*1.3
def mark_alerted(key, net_pct):
    with CD_LOCK:
        COOLDOWN[key]=(datetime.now(),net_pct)

class PaperDB:
    COLS=("net_pct_5m","net_pct_10m")
    def __init__(self, path):
        self.lock=threading.Lock()
        self.conn=sqlite3.connect(path,check_same_thread=False)
        with self.lock:
            self.conn.execute("""CREATE TABLE IF NOT EXISTS signals(
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, chain TEXT, symbol TEXT, contract TEXT,
                direction TEXT, pair TEXT, dex TEXT, order_usd REAL, token_amount REAL, mexc_avg REAL,
                dex_exec REAL, dex_price REAL, liq REAL, gross_pct REAL, net_pct REAL, net_usd REAL,
                net_pct_5m REAL, net_pct_10m REAL, raw TEXT)""")
            self.conn.commit()
    def insert(self, chain, tok, pool, p):
        with self.lock:
            cur=self.conn.execute("INSERT INTO signals(ts,chain,symbol,contract,direction,pair,dex,order_usd,token_amount,mexc_avg,dex_exec,dex_price,liq,gross_pct,net_pct,net_usd,raw) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(datetime.now().isoformat(), chain, tok["symbol"], tok["contract"], p["direction"], tok["pair"],pool["dex"], ORDER_SIZE_USD, p["token_amount"], p["mexc_avg"], p["dex_exec"], pool["price"],pool["liq"], p["gross_pct"], p["net_pct"], p["net_usd"], json.dumps({"profit":p,"pool":pool})))
            self.conn.commit()
            return cur.lastrowid
    def set_followup(self, row_id, minutes, value):
        col=f"net_pct_{minutes}m"
        if col not in self.COLS:
            return
        with self.lock:
            self.conn.execute(f"UPDATE signals SET {col}=? WHERE id=?", (value, row_id))
            self.conn.commit()
    def recent(self, n=50):
        with self.lock:
            cur=self.conn.execute("SELECT id,ts,chain,symbol,direction,gross_pct,net_pct,net_pct_5m,net_pct_10m FROM signals ORDER BY id DESC LIMIT ?", (n,))
            cols=[c[0] for c in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

DB=PaperDB(DB_PATH)
FOLLOWUPS=[]
FU_LOCK=threading.Lock()
def schedule_followups(row_id, chain, tok, direction):
    with FU_LOCK:
        for m in FOLLOWUP_MIN:
            FOLLOWUPS.append({"due":time.time()+m*60,"minutes":m,"id":row_id,"chain":chain,"tok":tok,"direction":direction})
def followup_loop():
    while True:
        time.sleep(10)
        with FU_LOCK:
            now=time.time()
            due=[f for f in FOLLOWUPS if f["due"]<=now]
            FOLLOWUPS[:]=[f for f in FOLLOWUPS if f["due"]>now]
        for f in due:
            value=None
            try:
                tok,chain=f["tok"],f["chain"]
                pools=fetch_dex_pools(chain, [tok["contract"]])
                pool=(pools or {}).get(tok["dedup_key"])
                depth=mexc_depth(tok["pair"]) if pool else None
                if pool and depth:
                    for p in evaluate(chain, tok, pool, depth):
                        if p["direction"]==f["direction"]:
                            value=p["net_pct"]
            except Exception as e:
                log.warning("Follow-up loi: %s",e)
            DB.set_followup(f["id"], f["minutes"], value)

def fmt_price(v):
    try:
        if v is None:
            return "0"
        v=float(v)
        if v==0:
            return "0"
        if abs(v)>=1:
            return f"{v:.6f}"
        elif abs(v)>=0.01:
            return f"{v:.8f}"
        else:
            return f"{v:.12f}"
    except:
        return str(v)
def fmt_token_amount(v):
    try:
        v=float(v)
        if v>=1000:
            return f"{v:,.0f}"
        else:
            return f"{v:.6f}"
    except:
        return str(v)

def send_telegram(text):
    try:
        r=session().post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",data={"chat_id":CHAT_ID,"text":text,"parse_mode":"HTML","disable_web_page_preview":True},timeout=15)
        if r.status_code!=200:
            log.warning("Telegram that bai %s: %s",r.status_code,r.text[:200])
            return False
        return True
    except Exception as e:
        log.warning("Telegram loi: %s",e)
        return False

def send_alert(chain_key, tok, pool, p):
    cfg=CHAINS[chain_key]
    if p["direction"]=="MEXC_TO_DEX":
        action=f"MUA MEXC {tok['pair']} -> rut ve {chain_key} -> BAN tren DEX"
    else:
        action=f"MUA tren {chain_key} DEX -> nap len MEXC -> BAN {tok['pair']}"
    bid,ask=p["mexc_best"]
    src=p.get("real_source","estimate")
    msg=(f"{cfg['emoji']} <b>Abrigate {esc(tok['symbol'])} [{chain_key}]</b> (paper) [{esc(src)}]\n\n"
         f"Contract: <code>{esc(tok['contract'])}</code>\n"
         f"DEX: {esc(pool['dex'])} | {esc(pool['pair_name'])} | fee ~{pool['fee']*100:.2f}%\n"
         f"Liq: ${pool['liq']:,.0f} | Vol24h: ${pool['vol24']:,.0f} | Txns1h: {pool['txns_h1']} | FDV ${pool.get('fdv',0):,.0f}\n\n"
         f"MEXC {esc(tok['pair'])}: bid/ask {fmt_price(bid)}/{fmt_price(ask)} | gia TB ${fmt_price(p['mexc_avg'])}\n"
         f"DEX: gia DS ${fmt_price(pool['price'])} | gia thuc thi ${fmt_price(p['dex_exec'])} | impact ~{p['impact_dex']:.2f}%\n\n"
         f"💰 <b>NET {p['net_pct']:.4f}% (${p['net_usd']:.2f})</b> | Gross {p['gross_pct']:.4f}%\n"
         f"Von ${ORDER_SIZE_USD:.0f} | Token {fmt_token_amount(p['token_amount'])}\n"
         f"Gas ${p['gas']:.2f} | Phi rut ${p['withdraw_fee_usd']:.2f} | Phi MEXC {MEXC_TAKER_FEE*100:.2f}%\n"
         f"🎯 {esc(action)}\n\n"
         f"🔗 MEXC: https://www.mexc.com/exchange/{esc(tok['pair'])}\n"
         f"🔗 DEX: {esc(pool['url'])}\n"
         f"🔗 Explorer: {esc(cfg['explorer'] + tok['contract'])}\n\n"
         f"⚠ Chi la mo phong gia thuc te ({esc(src)}). Gia co the doi.\n"
         f"⏰ {datetime.now().strftime('%H:%M:%S %d/%m')}")
    return send_telegram(msg)

def check_candidate(chain_key, tok, pool):
    st=STATS[chain_key]
    try:
        depth=mexc_depth(tok["pair"])
        if not depth:
            return None
        st.inc("fetched")
        st.success()
        best=None
        for p in evaluate(chain_key, tok, pool, depth):
            if p["gross_pct"]>=PREFILTER_GROSS:
                st.inc("opportunity")
            if p["net_pct"]>=THRESHOLD_NET:
                if p["gross_pct"]>MAX_GROSS:
                    st.inc("suspect")
                    log.info("NGHI VAN %s %s gross %.1f%% - bo qua",chain_key,tok["symbol"],p["gross_pct"])
                    continue
                if not best or p["net_pct"]>best["net_pct"]:
                    best=p
        return (tok, pool, best) if best else None
    except Exception:
        st.inc("errors")
        log.exception("Loi xu ly %s %s",chain_key,tok["symbol"])
        return None

def prefilter(chain_key, tok, pool, book):
    bt=book.get(tok["pair"])
    if not bt:
        return False
    bid,ask=bt
    _,buy_px,sell_px=dex_prices_estimate(pool)
    g1=(sell_px/ask-1)*100
    g2=(bid/buy_px-1)*100
    return max(g1,g2)>=PREFILTER_GROSS

def scan_chain_once(chain_key, stop):
    st=STATS[chain_key]
    MEXC.ensure_static()
    tokens=MEXC.tokens.get(chain_key, [])
    if not tokens:
        return False
    book=MEXC.get_book()
    if not book:
        return False
    for i in range(0, len(tokens), DEX_BATCH):
        if stop.is_set():
            return True
        st.beat()
        chunk=tokens[i:i+DEX_BATCH]
        pools=fetch_dex_pools(chain_key, [t["contract"] for t in chunk])
        if pools is None:
            st.inc("errors")
            continue
        candidates=[]
        for tok in chunk:
            pool=pools.get(tok["dedup_key"])
            if not pool or not symbol_ok(tok["symbol"], pool["base_symbol"]):
                continue
            st.inc("attempted")
            if prefilter(chain_key, tok, pool, book):
                candidates.append((tok, pool))
        if candidates:
            with ThreadPoolExecutor(max_workers=3) as ex:
                results=list(ex.map(lambda c: check_candidate(chain_key, c[0], c[1]), candidates))
            for res in results:
                if not res:
                    continue
                tok,pool,p=res
                key=(chain_key, tok["dedup_key"], p["direction"])
                if not should_alert(key, p["net_pct"]):
                    continue
                if send_alert(chain_key, tok, pool, p):
                    mark_alerted(key, p["net_pct"])
                    st.inc("signals")
                    row_id=DB.insert(chain_key, tok, pool, p)
                    schedule_followups(row_id, chain_key, tok, p["direction"])
        stop.wait(1)
    return True

def run_chain_loop(chain_key, stop):
    log.info("[%s] start",chain_key)
    empty=0
    warned=False
    while not stop.is_set():
        try:
            STATS[chain_key].beat()
            if not scan_chain_once(chain_key, stop):
                empty+=1
                log.info("[%s] chua co du lieu (%d), thu lai sau",chain_key,empty)
                stop.wait(60 if empty<=10 else 300)
                continue
            empty=0
            STATS[chain_key].scanned()
            if STATS[chain_key].snapshot()["dex_pairs"]==0:
                log.warning("[%s] Dexscreener khong tra pair nao (slug '%s' sai?)",chain_key, CHAINS[chain_key]["dex_chain"])
                if not warned:
                    warned=True
                    send_telegram(f"⚠ {esc(chain_key)}: Dexscreener khong tra pair nao cho slug <code>{esc(CHAINS[chain_key]['dex_chain'])}</code>.")
                stop.wait(3600)
                continue
            stop.wait(SCAN_PAUSE_SEC)
        except Exception:
            STATS[chain_key].inc("errors")
            log.exception("[%s] loi vong chinh",chain_key)
            stop.wait(20)
    log.info("[%s] dung (generation cu thoat)",chain_key)

class Worker:
    def __init__(self, chain_key):
        self.chain=chain_key
        self.stop=None
        self.thread=None
    def start(self):
        self.stop=threading.Event()
        STATS[self.chain].beat()
        self.thread=threading.Thread(target=run_chain_loop, args=(self.chain, self.stop),daemon=True, name=f"Bot-{self.chain}")
        self.thread.start()
    def restart(self):
        if self.stop:
            self.stop.set()
        if self.thread:
            self.thread.join(timeout=5)
        self.start()

def heartbeat_loop():
    while True:
        time.sleep(1800)
        try:
            msg=(f"💓 <b>HEARTBEAT {datetime.now().strftime('%H:%M %d/%m')}</b>\nNet ≥ {THRESHOLD_NET}% | Order ${ORDER_SIZE_USD:.0f} | RealCheck={REAL_PRICE_CHECK}\n\n")
            for k,st in STATS.items():
                s=st.snapshot()
                msg+=(f"{CHAINS[k]['emoji']} {k}: try={s['attempted']} fetch={s['fetched']} opp={s['opportunity']} sig={s['signals']} sus={s['suspect']} realOK={s['real_price_ok']} err={s['errors']}\n")
            send_telegram(msg)
        except Exception:
            log.exception("Heartbeat loi")

def start_web():
    from flask import Flask, jsonify
    app=Flask(__name__)
    @app.route("/")
    def home():
        return f"V7.1 REAL PRICE - signals: {sum(s.snapshot()['signals'] for s in STATS.values())}"
    @app.route("/health")
    def health():
        return "OK",200
    @app.route("/stats")
    def stats():
        return jsonify({k:s.snapshot() for k,s in STATS.items()})
    @app.route("/signals")
    def signals():
        return jsonify(DB.recent(50))
    log.info("Web tren 0.0.0.0:%s",PORT)
    try:
        from waitress import serve
        serve(app, host="0.0.0.0", port=PORT)
    except ImportError:
        app.run(host="0.0.0.0", port=PORT)

def main():
    log.info("BOT V7.1 REAL PRICE start | chains=%s | REAL_CHECK=%s", list(CHAINS), REAL_PRICE_CHECK)
    threading.Thread(target=start_web, daemon=True, name="Web").start()
    threading.Thread(target=followup_loop, daemon=True, name="Followup").start()
    threading.Thread(target=heartbeat_loop, daemon=True, name="HB").start()
    send_telegram(f"🚀 <b>BOT V7.1 REAL PRICE START</b> (paper)\nChains: {', '.join(CHAINS)}\nNet ≥ {THRESHOLD_NET}% | Order ${ORDER_SIZE_USD:.0f} | RealCheck={REAL_PRICE_CHECK}\nFilter: Liq≥${MIN_LIQ_USD:.0f} Vol≥${MIN_VOL_24H:.0f} Txns≥{MIN_TXNS_H1} Impact≤{MAX_IMPACT_PCT}% Gross≤{MAX_GROSS}%")
    workers={}
    for k in CHAINS:
        w=Worker(k)
        w.start()
        workers[k]=w
        time.sleep(1.2)
    while True:
        time.sleep(300)
        now=datetime.now()
        for k,w in workers.items():
            st=STATS[k]
            idle=(now-st.last_beat).total_seconds()
            if not w.thread.is_alive() or idle>STUCK_SEC:
                log.warning("[SUPERVISOR] %s %s -> restart",k,"chet" if not w.thread.is_alive() else f"dung {int(idle)}s")
                w.restart()
            else:
                log.info("[SUPERVISOR] %s OK, beat %ds truoc",k,int(idle))

if __name__=="__main__":
    main()
