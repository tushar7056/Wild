# server.py
# -*- coding: utf-8 -*-
"""
Mobile-first WinGo predictor server (FastAPI).
Runs the Python auto-learning predictor in background, serves static UI and WebSocket updates.
Uses INITIAL_HISTORY_URL env var; default = /mnt/data/win1m_results.json
"""
import os, json, time, threading, asyncio, requests
from datetime import datetime
from collections import Counter
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

# Config (env override)
TRAINING_FILE = "predictor_state.json"
INITIAL_HISTORY_SOURCE = os.environ.get("INITIAL_HISTORY_URL", "/mnt/data/win1m_results.json")
API_ENDPOINTS = [
    "https://draw.ar-lottery01.com/WinGo/WinGo_1M/GetHistoryIssuePage.json",
    "https://api.daman.games/WinGo/WinGo_1M/GetHistoryIssuePage.json",
    "https://api.tc-lottery.com/WinGo/WinGo_1M/GetHistoryIssuePage.json"
]
HISTORY_LOAD = int(os.environ.get("HISTORY_LOAD", "200"))
RECENT_WINDOW = int(os.environ.get("RECENT_WINDOW", "20"))
MARKOV_MIN_TRANSITIONS = int(os.environ.get("MARKOV_MIN_TRANSITIONS", "8"))
CONFIDENCE_MIN = float(os.environ.get("CONFIDENCE_MIN", "0.015"))
SIZE_CONF_MIN = float(os.environ.get("SIZE_CONF_MIN", "0.03"))
WAIT_AFTER_RESULT = int(os.environ.get("WAIT_AFTER_RESULT", "10"))
RUN_INTERVAL = int(os.environ.get("RUN_INTERVAL", "15"))  # mobile demo fast checking

# Thread-safe
state_lock = threading.Lock()
state = {
    "correct_number": 0,
    "correct_size": 0,
    "total": 0,
    "history": [],        # (pred, actual)
    "transitions": [],    # (prev, curr)
    "freq": {},
    "pending": None
}

# WebSocket manager
class Manager:
    def __init__(self):
        self.ws = set()
        self.lock = asyncio.Lock()
    async def connect(self, s):
        await s.accept(); async with self.lock: self.ws.add(s)
    async def disconnect(self, s):
        async with self.lock:
            if s in self.ws: self.ws.remove(s)
    async def broadcast(self, msg):
        async with self.lock:
            sockets = list(self.ws)
        for s in sockets:
            try: await s.send_json(msg)
            except: pass

manager = Manager()

# Persistence
def save_state():
    try:
        with state_lock:
            with open(TRAINING_FILE, "w") as f:
                json.dump(state, f, indent=2)
    except Exception as e:
        print("save error", e)

def load_state():
    try:
        if os.path.exists(TRAINING_FILE):
            with open(TRAINING_FILE, "r") as f:
                data = json.load(f)
            with state_lock:
                state.update(data)
            print("[INFO] Loaded state")
    except Exception as e:
        print("load error", e)

# Helpers
def safe_last_digit(n):
    try: return int(n) % 10
    except: 
        try: return int(str(n)[-1])
        except: return 0

def fetch_full_history_from_local(path):
    try:
        with open(path, "r") as f:
            data = json.load(f)
        nums=[]
        if isinstance(data, list):
            for it in data:
                if isinstance(it, dict):
                    n = it.get("premium") or it.get("number") or it.get("result")
                    if n is not None: nums.append(safe_last_digit(n))
                else:
                    nums.append(safe_last_digit(it))
        return nums
    except Exception as e:
        print("local history load failed", e)
        return []

def fetch_full_history_from_api(limit=HISTORY_LOAD):
    ts=int(time.time()*1000)
    for api in API_ENDPOINTS:
        try:
            url=f"{api}?ts={ts}&page=1&pageSize={limit}"
            r=requests.get(url, timeout=8)
            data=r.json().get("data",{}).get("list",[])
            if not data: continue
            nums=[ safe_last_digit(d.get("premium") or d.get("number")) for d in data ]
            print("[INFO] loaded", len(nums), "from", api)
            return nums
        except Exception as e:
            print("history api fail", e)
            continue
    print("no history API responded")
    return []

def fetch_full_history(limit=HISTORY_LOAD):
    src = (INITIAL_HISTORY_SOURCE or "").strip()
    if src:
        if os.path.exists(src):
            nums = fetch_full_history_from_local(src)
            if nums: return nums
        if src.startswith("http"):
            try:
                r = requests.get(src, timeout=12); data=r.json()
                nums=[]
                for d in data:
                    if isinstance(d, dict):
                        n=d.get("premium") or d.get("number") or d.get("result")
                        if n is not None: nums.append(safe_last_digit(n))
                    else: nums.append(safe_last_digit(d))
                if nums: return nums
            except Exception as e:
                print("download history failed", e)
    return fetch_full_history_from_api(limit)

def fetch_latest():
    ts=int(time.time()*1000)
    for api in API_ENDPOINTS:
        try:
            url=f"{api}?ts={ts}&page=1&pageSize=1"
            r=requests.get(url, timeout=6)
            dlist=r.json().get("data",{}).get("list",[])
            if not dlist: continue
            d=dlist[0]; n=d.get("premium") or d.get("number")
            return {"issue": d.get("issueNumber",""), "number": safe_last_digit(n), "raw": d}
        except Exception:
            continue
    return None

# Prediction engine (same logic as earlier improved)
def markov_distribution(last):
    with state_lock:
        trans=list(state["transitions"]); freq=dict(state["freq"])
    counts=[0]*10; total=0
    for a,b in trans:
        if a==last:
            counts[b]+=1; total+=1
    if total < MARKOV_MIN_TRANSITIONS:
        s=sum(freq.values())
        if s>0: return {i: freq.get(i,0)/s for i in range(10)}
        return {i: 1/10 for i in range(10)}
    return {i: counts[i]/total for i in range(10)}

def trend_distribution(window=RECENT_WINDOW):
    with state_lock:
        recent=[a for _,a in state["history"][-window:]]
    if not recent: return {i:1/10 for i in range(10)}
    L=len(recent); weights=list(range(1,L+1)); counts=[0]*10
    for w,v in zip(weights,recent): counts[v]+=w
    s=sum(counts)
    if s==0: return {i:1/10 for i in range(10)}
    return {i: counts[i]/s for i in range(10)}

def frequency_distribution():
    with state_lock:
        freq=dict(state["freq"])
    s=sum(freq.values())
    if s==0: return {i:1/10 for i in range(10)}
    return {i: freq.get(i,0)/s for i in range(10)}

def auto_learn_weights():
    with state_lock:
        transitions=list(state["transitions"]); history_vals=[x[1] for x in state["history"]]
    repeats = sum(1 for a,b in transitions if a==b)
    markov_strength = repeats / max(1, len(transitions) or 1)
    recent = history_vals[-RECENT_WINDOW:]
    streaks = sum(1 for i in range(1,len(recent)) if recent[i]==recent[i-1])
    trend_strength = streaks / max(1, len(recent) or 1)
    freq = Counter(history_vals)
    total = sum(freq.values())
    even = sum(freq[n] for n in [0,2,4,6,8]) if total else 0
    freq_imbalance = abs(even - (total-even)) / max(1,total)
    w_markov = 0.40 + markov_strength * 0.40
    w_trend  = 0.25 + trend_strength  * 0.30
    w_freq   = 0.10 + freq_imbalance  * 0.35
    s = w_markov + w_trend + w_freq
    w_markov/=s; w_trend/=s; w_freq/=s
    return w_markov,w_trend,w_freq

def combine_distributions():
    w_markov,w_trend,w_freq = auto_learn_weights()
    last = state["history"][-1][1] if state["history"] else None
    markov = markov_distribution(last) if last is not None else {i:1/10 for i in range(10)}
    trend = trend_distribution(); freq = frequency_distribution()
    combined={}
    for i in range(10):
        combined[i] = markov[i]*w_markov + trend[i]*w_trend + freq[i]*w_freq
    s=sum(combined.values())
    if s<=0: return {i:1/10 for i in range(10)}
    for i in combined: combined[i]/=s
    return combined

def predict_number_and_size():
    dist = combine_distributions()
    sorted_nums = sorted(dist.items(), key=lambda kv: kv[1], reverse=True)
    top_num, top_prob = sorted_nums[0]
    second_prob = sorted_nums[1][1] if len(sorted_nums)>1 else 0.0
    gap = top_prob - second_prob
    number_conf = (top_prob*0.55) + (gap*0.45) + 0.02
    size_probs={"Big":0,"Small":0}
    for n,p in dist.items(): size_probs["Big" if n>=5 else "Small"] += p
    pred_size = "Big" if size_probs["Big"]>=size_probs["Small"] else "Small"
    size_conf = abs(size_probs["Big"] - size_probs["Small"])
    if size_conf < SIZE_CONF_MIN: return None,0.0,None,0.0
    return int(top_num), float(number_conf), pred_size, float(size_conf)

# Broadcasting helper
async def broadcast_snapshot():
    with state_lock:
        payload = {
            "pending": state.get("pending"),
            "stats": {"total": state.get("total",0), "correct_number": state.get("correct_number",0), "correct_size": state.get("correct_size",0)},
            "last_history": state["history"][-30:]
        }
    await manager.broadcast(payload)

# Predictor loop
def predictor_loop(stop_event):
    load_state()
    nums = fetch_full_history(HISTORY_LOAD)
    if nums:
        with state_lock:
            state["history"] = [(None, int(n)) for n in nums]
            state["transitions"] = [(nums[i-1], nums[i]) for i in range(1,len(nums))]
            state["freq"] = dict(Counter(nums))
        save_state()
    last_issue=None; pending=None; pending_size=None
    while not stop_event.is_set():
        try:
            time.sleep(RUN_INTERVAL)
            res = fetch_latest()
            if not res: continue
            issue = res.get("issue","")
            if issue == last_issue: continue
            current_three = issue[-3:] if len(issue)>=3 else issue
            upcoming_serial = str((int(current_three)+1)%1000).zfill(3) if current_three.isdigit() else "000"
            actual_num = int(res["number"]); actual_size = "Big" if actual_num>=5 else "Small"
            if pending is not None:
                with state_lock:
                    state["total"] = state.get("total",0)+1
                    if pending == actual_num: state["correct_number"] = state.get("correct_number",0)+1
                    if pending_size == actual_size: state["correct_size"] = state.get("correct_size",0)+1
                    state["history"].append((pending, actual_num))
                    if len(state["history"])>1000: state["history"].pop(0)
                    if len(state["history"])>=2:
                        prev = state["history"][-2][1]; state["transitions"].append((prev, actual_num))
                    state["freq"][actual_num] = state["freq"].get(actual_num,0)+1
                save_state()
                asyncio.run(broadcast_snapshot())
                pending=None; pending_size=None
            last_issue = issue
            for _ in range(WAIT_AFTER_RESULT):
                if stop_event.is_set(): return
                time.sleep(1)
            pred_number, num_conf, pred_size, size_conf = predict_number_and_size()
            if pred_number is None:
                with state_lock: state["pending"]=None
                asyncio.run(broadcast_snapshot()); continue
            with state_lock:
                pending = pred_number; pending_size = pred_size
                state["pending"] = {"number":pending,"number_conf":num_conf,"size":pending_size,"size_conf":size_conf,"serial":upcoming_serial,"time":datetime.utcnow().isoformat()+"Z"}
            asyncio.run(broadcast_snapshot())
        except Exception as e:
            print("predictor error", e)
            time.sleep(3)

# FastAPI app
app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
def root():
    return FileResponse("static/index.html")

@app.get("/api/stats")
def stats():
    with state_lock:
        return {"total": state.get("total",0), "correct_number": state.get("correct_number",0), "correct_size": state.get("correct_size",0), "pending": state.get("pending"), "last_history": state["history"][-40:]}

@app.websocket("/ws")
async def websocket(ws: WebSocket):
    await manager.connect(ws)
    try:
        with state_lock:
            await ws.send_json({"pending": state.get("pending"), "stats":{"total": state.get("total",0),"correct_number": state.get("correct_number",0),"correct_size": state.get("correct_size",0)} ,"last_history": state["history"][-30:]})
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        await manager.disconnect(ws)
    except Exception:
        await manager.disconnect(ws)

stop_event = threading.Event()
pred_thread = threading.Thread(target=predictor_loop, args=(stop_event,), daemon=True)

@app.on_event("startup")
async def startup():
    if not pred_thread.is_alive():
        pred_thread.start()

@app.on_event("shutdown")
async def shutdown():
    stop_event.set(); pred_thread.join(timeout=5); save_state()

if __name__ == "__main__":
    uvicorn.run("server:app", host="0.0.0.0", port=int(os.environ.get("PORT","8000")))
