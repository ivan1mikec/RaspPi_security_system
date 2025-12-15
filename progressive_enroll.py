# progressive_enroll.py
import os, json
from threading import Lock
from datetime import datetime
from typing import Dict, Any, List

DATA_DIR = "data"
STATE_FILE = os.path.join(DATA_DIR, "progressive_enroll.json")
TARGET_SAMPLES = 20
MIN_QUALITY = 0.80

_lock = Lock()

def _now(): return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _ensure():
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(STATE_FILE):
        with open(STATE_FILE,"w") as f:
            json.dump({"target":TARGET_SAMPLES,"min_quality":MIN_QUALITY,"users":{}}, f, indent=2)

def get_policy():
    _ensure()
    with open(STATE_FILE,"r") as f: st = json.load(f)
    return int(st.get("target",TARGET_SAMPLES)), float(st.get("min_quality",MIN_QUALITY))

def configure(target:int=None, min_quality:float=None):
    _ensure()
    with _lock, open(STATE_FILE,"r") as f:
        st = json.load(f)
    if target is not None: st["target"]=int(target)
    if min_quality is not None: st["min_quality"]=float(min_quality)
    with open(STATE_FILE,"w") as f: json.dump(st,f,indent=2)

def should_collect(user_id:int)->bool:
    _ensure()
    with _lock, open(STATE_FILE,"r") as f:
        st = json.load(f)
    u = st["users"].get(str(user_id), {"count":0,"ready":False})
    return not u.get("ready",False)

def record_accepted_clip(user_id:int, video_path:str, score:float, details:Dict[str,Any]):
    _ensure()
    with _lock, open(STATE_FILE,"r") as f:
        st = json.load(f)
    u = st["users"].setdefault(str(user_id), {"count":0,"ready":False,"last":None})
    u["count"] = int(u.get("count",0))+1
    target = int(st.get("target",TARGET_SAMPLES))
    if u["count"] >= target: u["ready"]=True
    u["last"] = {"ts":_now(), "path":video_path, "score":round(score,3)}
    with open(STATE_FILE,"w") as f: json.dump(st,f,indent=2)

def get_progress()->List[Dict[str,Any]]:
    _ensure()
    with open(STATE_FILE,"r") as f:
        st = json.load(f)
    target = int(st.get("target",TARGET_SAMPLES))
    rows=[]
    for k,u in st.get("users",{}).items():
        rows.append({
            "user_id": int(k),
            "count": int(u.get("count",0)),
            "target": target,
            "ready": bool(u.get("ready",False)),
            "last_update": u.get("last",{}).get("ts","")
        })
    rows.sort(key=lambda r: r["user_id"])
    return rows
def remove_user(user_id: int):
    _ensure()
    with _lock, open(STATE_FILE, "r") as f:
        st = json.load(f)
    st.get("users", {}).pop(str(user_id), None)
    with open(STATE_FILE, "w") as f:
        json.dump(st, f, indent=2)

def clear_all_users():
    _ensure()
    with _lock, open(STATE_FILE, "r") as f:
        st = json.load(f)
    st["users"] = {}
    with open(STATE_FILE, "w") as f:
        json.dump(st, f, indent=2)

def get_progress_for_ids(id_list):
    _ensure()
    with open(STATE_FILE, "r") as f:
        st = json.load(f)
    target = int(st.get("target", TARGET_SAMPLES))
    users = st.get("users", {})
    rows = []
    for uid in sorted(id_list):
        u = users.get(str(uid), {"count": 0, "ready": False})
        rows.append({
            "user_id": int(uid),
            "count": int(u.get("count", 0)),
            "target": target,
            "ready": bool(u.get("ready", False)),
        })
    return rows