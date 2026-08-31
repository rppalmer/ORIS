import json, time, urllib.request
BASE="http://172.16.2.5:8000/v1"; KEY="sk-omlx-xneeSD7uRgINS5ni39ZB1XKx"; MODEL="gpt-oss-20b-MXFP4-Q8"
SCHEMA={"type":"object","properties":{"findings":{"type":"string"},"bears_on_topic":{"type":"boolean"}},
        "required":["findings","bears_on_topic"],"additionalProperties":False}
ITEM=("Title: Show HN: I built an agent framework in 400 lines\n"
 "Source: Hacker News, 214 points, 88 comments\n"
 "Text: The author argues most agent frameworks are wrappers over a while-loop. "
 "Benchmarks show 3.2x lower latency than LangGraph on a 12-step tool chain, "
 "measured over 500 runs. Several commenters said the comparison omits retries.")
DEC=json.JSONDecoder()
def post(p):
    r=urllib.request.Request(BASE+"/chat/completions",data=json.dumps(p).encode(),
      headers={"Content-Type":"application/json","Authorization":"Bearer "+KEY})
    t=time.time()
    with urllib.request.urlopen(r,timeout=300) as x: return json.loads(x.read()), time.time()-t
def go(label, **over):
    p={"model":MODEL,"temperature":0,"max_tokens":1024,
       "messages":[{"role":"system","content":"You record what one item said about a research topic."},
                   {"role":"user","content":"Topic: AI agent frameworks\n\nItem:\n"+ITEM}],
       "response_format":{"type":"json_schema","json_schema":{"name":"f","schema":SCHEMA}}}
    p.update(over)
    try: b,s=post(p)
    except Exception as e: print(f"{label:34} ERROR {str(e)[:70]}"); return
    ch=b["choices"][0]; c=ch["message"].get("content") or ""
    # take the first complete object, however the reply ended
    ok=None
    for cand in (c, c+"}", c+'"}'):
        try: ok=DEC.raw_decode(cand.strip())[0]; break
        except Exception: pass
    print(f"{label:34} {s:6.1f}s tok={ch and b['usage']['completion_tokens']:>5} "
          f"finish={ch['finish_reason']:<6} chars={len(c):>5} first_object={'OK' if ok else 'FAIL'}")
    if ok: print("        ", json.dumps(ok)[:150])

go("plain grammar, 1024 budget")
go("grammar + stop ['}{']",              stop=["}{"])
go("grammar + stop ['}{','}\\n{']",      stop=["}{", "}\n{"])
go("grammar + max_tokens=400",           max_tokens=400)
go("grammar + stop + effort low",        stop=["}{", "}\n{"], reasoning_effort="low")
