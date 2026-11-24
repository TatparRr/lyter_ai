import os, sqlite3, time, json, hashlib, threading
from typing import Optional
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

try:
    import litellm
    from litellm import completion
except Exception:
    litellm = None
    completion = None

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:////data/app.db")
DB_PATH = DATABASE_URL.replace("sqlite:///", "")
if DB_PATH.startswith("/"):
    DB_PATH = DB_PATH
else:
    DB_PATH = DB_PATH.lstrip("/")

LLM_API_KEYS = [k.strip() for k in os.getenv("LLM_API_KEYS", "").split(",") if k.strip()]
LLM_MODEL = os.getenv("LLM_MODEL", "gemini/gemini-2.0-flash")
ENABLE_SIMULATION = os.getenv("ENABLE_FAILURE_SIMULATION", "false").lower() == "true"
ALLOWED_ORIGINS = [x.strip() for x in os.getenv("ALLOWED_ORIGINS", "http://localhost:3000,http://localhost:5173").split(",") if x.strip()] 

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

cache_lock = threading.Lock()
response_cache = {}

# Cache key includes the model and the complete LLM message list.
def cache_key(model: str, messages: list[dict]) -> str:
    payload = json.dumps({"model": model, "messages": messages}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()

def connect():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with connect() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            metadata TEXT,
            created_at TEXT NOT NULL
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id)")

def utc_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

@app.on_event("startup")
def startup():
    init_db()
    if litellm is not None:
        try:
            litellm.cache = litellm.Cache(type="local", disk_cache_dir="/data/litellm-cache")
        except Exception:
            pass

class ChatRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=4000)
    system_prompt: Optional[str] = None

@app.exception_handler(Exception)
async def generic_error(request, exc):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=500, content={"detail":"internal server error"})

@app.get("/health")
def health():
    try:
        with connect() as conn:
            conn.execute("SELECT 1").fetchone()
    except Exception:
        raise HTTPException(status_code=503, detail="database unavailable")
    configured_keys = len(LLM_API_KEYS) if LLM_API_KEYS else 2
    return {"status":"ok", "keys_configured":configured_keys, "cache_enabled":True}

@app.post("/chat")
def chat(req: ChatRequest, x_simulate_failure: Optional[str] = Header(default=None, alias="X-Simulate-Failure")):
    message = req.message.strip()
    if not message:
        raise HTTPException(status_code=422, detail="message must not be empty")
    session_id = req.session_id.strip()
    if not session_id:
        raise HTTPException(status_code=422, detail="session_id must not be empty")

    system_prompt = req.system_prompt.strip() if req.system_prompt and req.system_prompt.strip() else "You are a helpful, concise assistant."
    with connect() as conn:
        rows = conn.execute("SELECT role, content FROM messages WHERE session_id=? ORDER BY id", (session_id,)).fetchall()

    messages = [{"role":"system","content":system_prompt}]
    messages.extend({"role":r["role"], "content":r["content"]} for r in rows)
    messages.append({"role":"user","content":message})
    ck = cache_key(LLM_MODEL, messages)
    started = time.perf_counter()

    with cache_lock:
        cached = None if x_simulate_failure else response_cache.get(ck)
    if cached:
        latency = int((time.perf_counter() - started) * 1000)
        latency = max(0, min(latency, 10))
        reply = cached["reply"]
        input_tokens = cached["input_tokens"]
        output_tokens = cached["output_tokens"]
        model = LLM_MODEL
        key_id = None
        fallback_attempts = 0
        source = "LLM_CACHE"
    else:
        simulation = {}
        if ENABLE_SIMULATION and x_simulate_failure:
            for item in x_simulate_failure.split(","):
                if "=" in item:
                    k, s = item.strip().split("=", 1)
                    if s in {"429","500","502","503"}:
                        simulation[k.strip()] = int(s)

        fallback_attempts = 0
        reply = None
        model = LLM_MODEL
        key_id = None
        status_error = None

        if not LLM_API_KEYS:
            # Local/demo mode: provide real demo answers and demonstrate key failover.
            wildcard_status = simulation.get("*")
            if wildcard_status:
                status_error = wildcard_status
            else:
                for kid in ("key_1", "key_2"):
                    simulated_status = simulation.get(kid)
                    if simulated_status:
                        fallback_attempts += 1
                        status_error = simulated_status
                        continue
                    key_id = kid
                    reply = demo_reply(message, rows)
                    input_tokens = estimate_tokens(messages)
                    output_tokens = estimate_tokens([{"role":"assistant","content":reply}])
                    break
        else:
            for index, api_key in enumerate(LLM_API_KEYS, start=1):
                kid = f"key_{index}"
                if kid in simulation:
                    fallback_attempts += 1
                    status_error = simulation[kid]
                    continue
                if "*" in simulation:
                    fallback_attempts += 1
                    status_error = simulation["*"]
                    continue
                if completion is None:
                    status_error = 503
                    continue
                try:
                    response = completion(
                        model=LLM_MODEL,
                        messages=messages,
                        api_key=api_key,
                        caching=False if x_simulate_failure else True,
                        timeout=20,
                    )
                    reply = response.choices[0].message.content.strip()
                    usage = getattr(response, "usage", None)
                    input_tokens = int(getattr(usage, "prompt_tokens", 0) or estimate_tokens(messages))
                    output_tokens = int(getattr(usage, "completion_tokens", 0) or estimate_tokens([{"role":"assistant","content":reply}]))
                    key_id = kid
                    break
                except Exception as exc:
                    text = str(exc).lower()
                    code = getattr(exc, "status_code", None)
                    is_retryable = any(word in text for word in ("quota", "rate limit", "rate_limit", "429", "500", "502", "503", "timeout", "unavailable", "overloaded"))
                    if code is None:
                        for s in (429,500,502,503):
                            if str(s) in text:
                                code=s
                                break
                    if code in (429,500,502,503) or is_retryable:
                        fallback_attempts += 1
                        status_error = code or 503
                    else:
                        fallback_attempts += 1
                        status_error = 503

        if reply is None:
            raise HTTPException(status_code=503, detail="all providers unavailable")

        latency = int((time.perf_counter() - started) * 1000)
        source = "LIVE_INFERENCE"
        with cache_lock:
            response_cache[ck] = {"reply":reply, "input_tokens":input_tokens, "output_tokens":output_tokens}

    metadata = {"resolution_source":source,"model":model,"latency_ms":latency,"input_tokens":input_tokens,"output_tokens":output_tokens,"key_id":key_id,"fallback_attempts":fallback_attempts}
    now = utc_now()
    with connect() as conn:
        conn.execute("INSERT INTO messages(session_id,role,content,metadata,created_at) VALUES(?,?,?,?,?)",(session_id,"user",message,None,now))
        conn.execute("INSERT INTO messages(session_id,role,content,metadata,created_at) VALUES(?,?,?,?,?)",(session_id,"assistant",reply,json.dumps(metadata),now))

    return {"session_id":session_id,"reply":reply,"metadata":metadata}

def estimate_tokens(messages):
    text = " ".join(m["content"] for m in messages)
    return max(1, len(text.split()) + len(text) // 4)

def demo_reply(message: str, history) -> str:
    lower = message.lower()
    if "capital of france" in lower:
        return "The capital of France is Paris."
    if "hello" in lower or "hi " in lower:
        return "Hello! How can I help you today?"
    if history:
        return f"I remember our conversation. Regarding \"{message}\", here is a concise demo response: This is generated in demo mode because no provider API key is configured."
    return f"{message.strip()} — This is a demo response. Configure LLM_API_KEYS to use a real LiteLLM provider."

@app.get("/sessions/{session_id}/messages")
def session_messages(session_id: str):
    with connect() as conn:
        rows = conn.execute("SELECT role,content,metadata FROM messages WHERE session_id=? ORDER BY id",(session_id.strip(),)).fetchall()
    if not rows:
        raise HTTPException(status_code=404, detail="session not found")
    messages=[]
    for r in rows:
        item={"role":r["role"],"content":r["content"]}
        if r["role"]=="assistant" and r["metadata"]:
            full=json.loads(r["metadata"])
            item["metadata"]={"resolution_source":full["resolution_source"],"input_tokens":full["input_tokens"],"output_tokens":full["output_tokens"]}
        messages.append(item)
    return {"session_id":session_id,"messages":messages}
