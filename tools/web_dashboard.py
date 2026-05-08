import os
import sqlite3
import json
from pathlib import Path
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# Configuration
try:
    from src.core.config import config
except ImportError:
    # fallback if not in path
    import sys
    sys.path.append(os.getcwd())
    from src.core.config import config

SANDBOX_PATH = Path(config.actor_sandbox_root).resolve()
INPUT_FILE = (SANDBOX_PATH / "user_input.txt").resolve()
PORT = 8501

COLORS = {
    'bg': '#FFFFFF',
    'surface': '#F8FAFC',
    'border': '#E2E8F0',
    'text': '#111827',
    'muted': '#64748B',
    'accent': '#2563EB',
    'agent_bg': '#F1F5F9',
    'err_bg': '#FEF2F2',
    'err_fg': '#DC2626'
}

def decode_payload(val):
    if not val:
        return {}
    if isinstance(val, (dict, list)):
        return val # 文字列化せず dict のまま返す
    if isinstance(val, str):
        try:
            parsed = json.loads(val)
            return parsed if isinstance(parsed, (dict, list)) else val
        except ValueError:
            return val
    return str(val)

def load_from_redis(limit=100):
    """Redis(SessionMemory)からデータをロードする"""
    try:
        import redis
        client = redis.Redis.from_url(config.memory_redis_url, decode_responses=True)
        # 最新のIDを取得
        ids = client.zrevrange("session:timeline", 0, limit - 1)
        results = []
        for eid in ids:
            raw = client.get(f"session:{eid}")
            if raw:
                entry = json.loads(raw)
                entry['payload'] = decode_payload(entry.get('payload'))
                results.append(entry)
        return results
    except Exception as e:
        print(f"Redis Error: {e}")
        return []

def load_from_chroma(limit=50):
    """ChromaDB(LongTermMemory)からデータをロードする"""
    try:
        import chromadb
        client = chromadb.PersistentClient(path=config.memory_chroma_persist_dir)
        collection = client.get_or_create_collection(name="narv_long_term_memory")
        
        # limit を get() に渡すと挿入順で古いデータが返るため、全件取得後にソートしてから絞る
        data = collection.get(include=["metadatas", "documents"])
        results = []
        if data and data['ids']:
            for i in range(len(data['ids'])):
                meta = data['metadatas'][i] if data['metadatas'] else {}
                doc = data['documents'][i] if data['documents'] else "{}"
                results.append({
                    "id": data['ids'][i],
                    "event_type": meta.get("event_type", "LTM"),
                    "created_at": meta.get("created_at", "N/A"),
                    "payload": decode_payload(doc)
                })
        # 作成日時で降順ソートして最新 limit 件を返す
        results.sort(key=lambda x: x['created_at'], reverse=True)
        return results[:limit]
    except Exception as e:
        print(f"ChromaDB Error: {e}")
        return []


def load_graph_data(limit=200):
    """Neo4j からグラフデータ（ノード＋エッジ）を取得する"""
    try:
        from src.memory.adapters.graph_adapter import GraphAdapter
        adapter = GraphAdapter(
            uri=config.memory_neo4j_uri,
            user=config.memory_neo4j_user,
            password=config.memory_neo4j_password,
        )
        data = adapter.get_all_graph_data(limit=limit)
        adapter.close()
        return data
    except Exception as e:
        print(f"Neo4j Error: {e}")
        return {"nodes": [], "links": []}

class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == '/':
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(self.get_html().encode('utf-8'))
        elif parsed.path == '/api/data':
            # データのロード
            session_data = load_from_redis(100)
            
            # Chat用（全てのイベントをフロントエンドに送り、表示形式の判定はJS側に委ねる）
            chat_data = session_data[:]
            # 表示用に昇順へ戻す
            chat_data.reverse()
            
            ltm_data = load_from_chroma(50)
            
            # Read kernel state
            kernel_state = {}
            state_file = Path("data/kernel_state.json")
            if state_file.exists():
                try:
                    with open(state_file, "r", encoding="utf-8") as f:
                        kernel_state = json.load(f)
                except Exception:
                    pass
            
            response = {
                "chat": chat_data,
                "session": session_data,
                "ltm": ltm_data,
                "state": kernel_state,
                "time": datetime.now().strftime("%H:%M:%S")
            }
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(response, ensure_ascii=False).encode('utf-8'))
        elif parsed.path == '/api/graph':
            graph_data = load_graph_data(200)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(graph_data, ensure_ascii=False).encode('utf-8'))
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == '/api/input':
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            try:
                data = json.loads(post_data.decode('utf-8'))
                user_text = data.get("text", "").strip()
                print(f"[Dashboard] Received /api/input: '{user_text}'")
                if user_text:
                    SANDBOX_PATH.mkdir(parents=True, exist_ok=True)
                    with open(INPUT_FILE, "a", encoding="utf-8") as f:
                        f.write(user_text + "\n")
                    print(f"[Dashboard] Successfully wrote to {INPUT_FILE}")
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({"status": "ok"}).encode('utf-8'))
                    return
            except Exception as e:
                print(f"[Dashboard] Error processing input: {e}")
            
            self.send_response(400)
            self.end_headers()

    def get_html(self):
        html = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Narv Monitor</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<script src="https://d3js.org/d3.v7.min.js"></script>
<style>
* { box-sizing: border-box; }
body, html { margin: 0; padding: 0; width: 100%; height: 100vh; overflow: hidden; background: __COLOR_BG__; color: __COLOR_TEXT__; font-family: 'Inter', -apple-system, sans-serif; }
.app { display: flex; flex-direction: column; width: 100%; height: 100%; }

/* Header / Nav */
.nav {
    display: flex; justify-content: space-between; align-items: center;
    background: rgba(255, 255, 255, 0.95); backdrop-filter: blur(8px);
    border-bottom: 1px solid __COLOR_BORDER__; padding: 0 16px; height: 50px; flex-shrink: 0; z-index: 10;
}
.tabs { display: flex; gap: 8px; height: 100%; }
.nav-btn {
    background: none; border: none; font-size: 0.82rem; font-weight: 500;
    color: __COLOR_MUTED__; cursor: pointer; padding: 0 16px; height: 100%;
    position: relative; transition: color 0.2s; white-space: nowrap; outline: none; font-family: 'Inter', sans-serif;
}
.nav-btn:hover { color: __COLOR_TEXT__; }
.nav-btn.active { color: __COLOR_ACCENT__; }
.nav-btn.active::after {
    content: ''; position: absolute; bottom: -1px; left: 0; right: 0; height: 2px;
    background: __COLOR_ACCENT__; border-radius: 2px 2px 0 0;
}
.stats { display: flex; align-items: center; gap: 12px; }
.stat { font-size: 0.72rem; color: __COLOR_MUTED__; font-weight: 400; }

/* Panels */
.panel { flex: 1; overflow-y: auto; display: none; padding-bottom: 80px; position: relative; }
.panel.active { display: flex; flex-direction: column; }

/* Chat */
#p-chat { padding: 20px 24px; gap: 8px; }
.row { display: flex; align-items: flex-end; gap: 10px; margin-bottom: 4px; }
.row.u { flex-direction: row-reverse; }
.b { max-width: 72%; padding: 12px 16px; border-radius: 18px; line-height: 1.5; white-space: pre-wrap; font-size: 0.9rem; }
.u .b { background: __COLOR_ACCENT__; color: #ffffff; border-bottom-right-radius: 4px; box-shadow: 0 1px 2px rgba(0,0,0,0.05); }
.a .b { background: __COLOR_AGENT_BG__; border: 1px solid __COLOR_BORDER__; border-bottom-left-radius: 4px; }
.e .b { background: __COLOR_ERR_BG__; color: __COLOR_ERR_FG__; border: 1px solid rgba(220,38,38,0.15); border-bottom-left-radius: 4px; }
.ts { font-size: 0.68rem; color: #A1A1AA; margin-top: 6px; }
.u .ts { text-align: right; }
.av { font-size: 1.15rem; margin-bottom: 2px; flex-shrink: 0; }

/* Thoughts */
.th { background: __COLOR_SURFACE__; margin: 4px 0; border: 1px solid __COLOR_BORDER__; border-radius: 12px; padding: 10px 16px; color: __COLOR_MUTED__; box-shadow: 0 1px 2px rgba(0,0,0,0.02); }
details summary { cursor: pointer; user-select: none; list-style: none; outline: none; font-size: 0.8rem; font-weight: 500; color: #52525B; }
details summary::-webkit-details-marker { display: none; }
.tp { margin-top: 10px; font-size: 0.7rem; color: #3F3F46; font-family: ui-monospace, 'Fira Mono', monospace; white-space: pre-wrap; overflow-x: auto; background: #F4F4F5; padding: 8px; border-radius: 6px; border: 1px solid #E5E7EB; }

/* DB View */
#p-db { padding: 20px 24px; }
.lbl { font-size: 0.72rem; font-weight: 600; text-transform: uppercase; color: __COLOR_MUTED__; letter-spacing: 0.08em; margin: 20px 0 8px; }
.lbl:first-child { margin-top: 0; }
table { width: 100%; border-collapse: collapse; background: __COLOR_BG__; border-radius: 8px; overflow: hidden; border: 1px solid __COLOR_BORDER__; table-layout: fixed; }
th, td { padding: 10px 12px; text-align: left; border-bottom: 1px solid __COLOR_BORDER__; font-size: 0.78rem; }
th { color: __COLOR_MUTED__; font-weight: 500; font-size: 0.72rem; background: __COLOR_SURFACE__; width: 25%; }
td { width: 75%; overflow: hidden; text-overflow: ellipsis; }
tr:last-child td { border-bottom: none; }
tr:hover td { background: __COLOR_SURFACE__; }

/* DB Cells Expandable */
.expandable { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; cursor: pointer; color: __COLOR_ACCENT__; display: block; }
.expandable.expanded { white-space: pre-wrap; word-break: break-all; color: __COLOR_TEXT__; background: #F1F5F9; border-radius: 4px; padding: 8px; max-height: 400px; overflow-y: auto; cursor: text; }

/* Input Area */
.input-container {
    flex-shrink: 0; padding: 12px 24px 24px; background: __COLOR_BG__;
    display: none; border-top: 1px solid __COLOR_BORDER__;
    position: relative; bottom: 0;
}
.input-box {
    flex: 1; min-height: 48px; max-height: 120px; border: 1px solid __COLOR_BORDER__; border-radius: 24px;
    padding: 12px 20px; outline: none; font-family: inherit; font-size: 0.95rem; line-height: 1.4;
    transition: 0.2s; background: __COLOR_SURFACE__; resize: none; overflow-y: auto;
    width: 100%; box-shadow: inset 0 1px 2px rgba(0,0,0,0.02);
}
.input-box:focus { border-color: __COLOR_ACCENT__; background: __COLOR_BG__; }

/* Graph Explorer */
#p-graph { padding: 0; overflow: hidden; position: relative; }
#p-graph.active { display: flex; flex-direction: row; }
.graph-main { flex: 1; position: relative; background: #FAFBFC; }
.graph-main svg { width: 100%; height: 100%; }
.graph-detail {
    width: 320px; flex-shrink: 0; border-left: 1px solid __COLOR_BORDER__;
    background: __COLOR_BG__; overflow-y: auto; padding: 20px;
    font-size: 0.82rem; display: flex; flex-direction: column; gap: 12px;
}
.graph-detail h3 { margin: 0; font-size: 0.9rem; font-weight: 600; color: __COLOR_TEXT__; }
.graph-detail .gd-label { font-size: 0.7rem; font-weight: 600; text-transform: uppercase; color: __COLOR_MUTED__; letter-spacing: 0.06em; margin-top: 8px; }
.graph-detail .gd-value { font-size: 0.82rem; color: __COLOR_TEXT__; word-break: break-all; }
.graph-detail .gd-payload { font-size: 0.7rem; font-family: ui-monospace, monospace; background: #F4F4F5; border: 1px solid #E5E7EB; border-radius: 6px; padding: 8px; white-space: pre-wrap; max-height: 300px; overflow-y: auto; color: #3F3F46; }
.graph-detail .gd-empty { color: __COLOR_MUTED__; font-style: italic; text-align: center; margin-top: 40px; }
.graph-legend {
    position: absolute; bottom: 16px; left: 16px; background: rgba(255,255,255,0.92);
    backdrop-filter: blur(6px); border: 1px solid __COLOR_BORDER__; border-radius: 10px;
    padding: 10px 14px; display: flex; flex-wrap: wrap; gap: 10px; z-index: 5;
    font-size: 0.7rem; color: __COLOR_MUTED__; box-shadow: 0 1px 4px rgba(0,0,0,0.05);
}
.graph-legend-item { display: flex; align-items: center; gap: 5px; }
.graph-legend-dot { width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }
.graph-stats {
    position: absolute; top: 16px; right: 340px; background: rgba(255,255,255,0.92);
    backdrop-filter: blur(6px); border: 1px solid __COLOR_BORDER__; border-radius: 8px;
    padding: 6px 12px; font-size: 0.72rem; color: __COLOR_MUTED__; z-index: 5;
    box-shadow: 0 1px 4px rgba(0,0,0,0.05);
}
.graph-tooltip {
    position: absolute; padding: 8px 12px; background: rgba(17,24,39,0.92);
    color: #fff; border-radius: 8px; font-size: 0.75rem; pointer-events: none;
    z-index: 20; max-width: 280px; line-height: 1.4; box-shadow: 0 4px 12px rgba(0,0,0,0.15);
    opacity: 0; transition: opacity 0.15s;
}

/* Skeleton loader for transition */
.skel { opacity: 0.5; transition: opacity 0.3s; }
</style>
</head>
<body>
<div class="app">
  <div class="nav">
    <div class="tabs">
      <button class="nav-btn active" id="btn-chat" onclick="show('chat')">💬 Chat & Thoughts</button>
      <button class="nav-btn" id="btn-db" onclick="show('db')">🗄️ Database Explorer</button>
      <button class="nav-btn" id="btn-graph" onclick="show('graph')">🔗 Graph Explorer</button>
    </div>
    <div class="stats">
      <span class="stat">Narv · 🔄 <span id="clock">00:00:00</span></span>
      <span class="stat" style="margin-left:8px; border: 1px solid #E2E8F0; padding: 2px 6px; border-radius: 4px;">CogL: <b id="st-cog">0.00</b></span>
      <span class="stat" style="border: 1px solid #E2E8F0; padding: 2px 6px; border-radius: 4px;">Urg: <b id="st-urg">0.00</b></span>
      <span class="stat" style="border: 1px solid #E2E8F0; padding: 2px 6px; border-radius: 4px;">Emo: <b id="st-emo">0.00</b></span>
      <span class="stat" style="border: 1px solid #E2E8F0; padding: 2px 6px; border-radius: 4px;">Val: <b id="st-val">0.50</b></span>
    </div>
  </div>

  <!-- Chat View -->
  <div id="p-chat" class="panel active">
     <div id="chat-content"></div>
  </div>

  <!-- DB Explorer View -->
  <div id="p-db" class="panel">
      <div class="lbl">Session Memory (Recent Context)</div>
      <table id="tbl-session"><tbody></tbody></table>
      
      <div class="lbl" style="margin-top: 24px;">Long Term Memory (Core Rules & Summaries)</div>
      <table id="tbl-ltm"><tbody></tbody></table>
  </div>

  <!-- Graph Explorer View -->
  <div id="p-graph" class="panel">
      <div class="graph-main">
          <svg id="graph-svg"></svg>
          <div class="graph-legend" id="graph-legend"></div>
          <div class="graph-stats" id="graph-stats">Nodes: 0 | Edges: 0</div>
      </div>
      <div class="graph-detail" id="graph-detail">
          <h3>🔗 Graph Explorer</h3>
          <div class="gd-empty">Click a node to inspect its details</div>
      </div>
      <div class="graph-tooltip" id="graph-tooltip"></div>
  </div>

  <!-- Unified Input Bar -->
  <div class="input-container">
      <textarea class="input-box" id="userInput" rows="1" placeholder="メッセージを入力... (Enterで送信)" oninput="this.style.height='';this.style.height=this.scrollHeight+'px'"></textarea>
  </div>
</div>

<script>
  window.onerror = function(msg, url, lineNo, columnNo, error) {
      var err = "JS Error: " + msg + "\\nLine: " + lineNo + (error ? "\\nStack: " + error.stack : "");
      console.error(err);
      alert(err);
      return false;
  };

  // Safe LocalStorage
  const storage = {
      get: (k) => { try { return localStorage.getItem(k); } catch(e) { return null; } },
      set: (k, v) => { try { localStorage.setItem(k, v); } catch(e) {} }
  };

  // UI State and Definitions (Moved up for availability)
  let activeTab = storage.get('narv_active_tab') || 'chat';
  const openDetails = new Set();
  
  function show(name) {
    try {
        console.log("Switching to tab:", name);
        activeTab = name;
        storage.set('narv_active_tab', name);
        document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
        document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
        
        const p = document.getElementById('p-' + name);
        const b = document.getElementById('btn-' + name);
        if (p) p.classList.add('active');
        if (b) b.classList.add('active');
        
        const ic = document.querySelector('.input-container');
        if(ic) ic.style.display = (name === 'chat') ? 'flex' : 'none';
        
        if(name === 'chat') scrollChat();
        if(name === 'graph') loadGraph();
    } catch(e) { console.error("Tab switch error:", e); }
  }

  function scrollChat() {
      const pChat = document.getElementById('p-chat');
      if (!pChat) return;
      const isScrolledUp = storage.get('narv_chat_scrolled_up') === 'true';
      if (!isScrolledUp) {
          pChat.scrollTop = pChat.scrollHeight;
      }
  }

  // Setup UI elements after definition
  document.addEventListener('DOMContentLoaded', () => {
      const pChat = document.getElementById('p-chat');
      if(pChat) {
          pChat.addEventListener('toggle', (e) => {
              if(e.target.tagName === 'DETAILS') {
                  const id = e.target.getAttribute('data-id');
                  if(id) {
                      if(e.target.open) openDetails.add(id);
                      else openDetails.delete(id);
                  }
              }
          }, true);
          
          pChat.addEventListener('scroll', () => {
            const isAtBottom = (pChat.scrollTop + pChat.clientHeight >= pChat.scrollHeight - 20);
            storage.set('narv_chat_scrolled_up', isAtBottom ? 'false' : 'true');
            storage.set('narv_chat_scroll_top', pChat.scrollTop);
          });
      }
      
      const userInput = document.getElementById('userInput');
      if(userInput) {
          userInput.addEventListener('keydown', handleInputKey);
      }
      
      show(activeTab);
      fetchData();
      setInterval(fetchData, 5000);
  });

  // Send Input to Python Backend
  async function handleInputKey(e) {
      if (e.isComposing || e.keyCode === 229) return;
      if(e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault();
          const inputBox = e.target;
          const text = inputBox.value.trim();
          if(!text) return;
          
          inputBox.value = '';
          inputBox.style.height = ''; 
          inputBox.disabled = true; 
          
          try {
              const res = await fetch('/api/input', {
                  method: 'POST',
                  headers: {'Content-Type': 'application/json'},
                  body: JSON.stringify({text: text})
              });
              if (!res.ok) {
                  const errText = await res.text();
                  alert('送信失敗 (HTTP ' + res.status + '): ' + errText);
                  throw new Error(errText);
              }
              console.log('Successfully sent input:', text);
              fetchData(); // Instant poll to see results
          } catch(err) {
              console.error('Fetch Error:', err);
              alert("送信エラー: " + err.message + "\\nネットワークまたはサーバーの状態を確認してください。");
          } finally {
              inputBox.disabled = false;
              inputBox.focus();
          }
      }
  }

  function toggleSpan(id) {
      const span = document.getElementById(id);
      if(!span) return;
      if (span.classList.contains('expanded')) {
          span.classList.remove('expanded');
          localStorage.removeItem('narv_expanded_' + id);
      } else {
          span.classList.add('expanded');
          localStorage.setItem('narv_expanded_' + id, 'true');
      }
  }

  // Data Rendering Logic
  function escape(str) {
      if(!str) return "";
      return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function renderTableRows(data, tablePrefix) {
      return (data || []).map((rt, idx) => {
          if (!rt) return "";
          const eventType = rt.event_type || "UNKNOWN";
          const createdAt = rt.created_at || "N/A";
          const isErr = eventType.includes('ERROR');
          const isSys = eventType.includes('SYS');
          const rowBadge = isErr ? '🔴' : (isSys ? '⚙️' : '📝');
          
          let payloadStr = "";
          if (typeof rt.payload === 'object' && rt.payload !== null) {
              payloadStr = JSON.stringify(rt.payload, null, 2);
          } else {
              payloadStr = String(rt.payload || "None");
          }
          
          const payload = escape(payloadStr);
          const uid = tablePrefix + '-' + idx;
          const isExpanded = localStorage.getItem('narv_expanded_' + uid) === 'true';
          const expClass = isExpanded ? 'expandable expanded' : 'expandable';
          
          return `<tr>
              <td><strong>${rowBadge} ${escape(eventType)}</strong><br><span style="font-size:0.65rem;color:#94A3B8">${escape(createdAt)}</span></td>
              <td><span id="${uid}" class="${expClass}" onclick="toggleSpan('${uid}')">${payload}</span></td>
          </tr>`;
      }).join("");
  }

  function extractChatInfo(payloadString) {
      if(!payloadString) return { msg: null, isPublic: false, recipient: null };
      
      let obj = null;
      if (typeof payloadString === 'object' && payloadString !== null) {
          obj = payloadString;
      } else {
          const trimmed = String(payloadString).trim();
          if(trimmed.startsWith('{') || trimmed.startsWith('[')) {
              try {
                  obj = JSON.parse(payloadString);
              } catch(e) {}
          }
      }
      
      if (!obj) return { msg: String(payloadString), isPublic: true, recipient: null };

      // ユーザーへの直接の回答・発言
      const publicKeys = ["response_to_user", "OutputMsg", "output_msg", "message", "response", "user_input", "text"];
      // 内部的な思考・処理結果
      const internalKeys = ["thought", "internal_thought", "rationale", "content", "summary", "result", "action_result"];
      
      function findRecursive(o, parentRecipient = null) {
          if (typeof o !== 'object' || o === null) return null;
          const currentRecipient = o.recipient || parentRecipient;
          
          // publicKeys を優先的に探す
          for (const k of publicKeys) {
              if (k in o && typeof o[k] === 'string' && o[k].trim() !== '') {
                  return { msg: o[k], isPublic: true, recipient: currentRecipient };
              }
          }
          // なければ internalKeys を探す
          for (const k of internalKeys) {
              if (k in o && typeof o[k] === 'string' && o[k].trim() !== '') {
                  return { msg: o[k], isPublic: false, recipient: currentRecipient };
              }
          }
          
          for (const k in o) {
              const res = findRecursive(o[k], currentRecipient);
              if (res) return res;
          }
          return null;
      }
      
      const found = findRecursive(obj, obj.recipient);
      return found || { msg: null, isPublic: false, recipient: obj.recipient || null };
  }

  function renderChatData(data) {
      let html = "";
      let lastMsg = null;
      
      data.forEach(rt => {
        const eventTypeLower = (rt.event_type || "").toLowerCase();
        const isUser = eventTypeLower.includes('user_input') || eventTypeLower === 'user';
        const isNotify = eventTypeLower.includes('notify');
        const isError = eventTypeLower.includes('error');
        
        // 特定のイベントタイプは無条件で内部処理扱いにする候補
        const forceInternalTypes = [
            'dmn_thought', 'reflection_result', 'cognition_result', 'action_result', 
            'dmn', 'state_update', 'memory_query', 'gather_perceptions'
        ];
        const isForcedInternal = forceInternalTypes.some(t => eventTypeLower.includes(t));

        const info = extractChatInfo(rt.payload);
        const msg = info.msg;
        const recipient = info.recipient;
        const isPublicMsg = info.isPublic;
        
        // 重複チェック
        let isDuplicate = false;
        if (msg && msg === lastMsg) {
            isDuplicate = true;
        } else if (msg) {
            lastMsg = msg;
        }
        
        // 【表示判定ロジック】
        // 以下のいずれかに当てはまる場合はアコーディオン（Internal Processing）とする
        // 1. メッセージが抽出できなかった (msg === null)
        // 2. 重複したメッセージである (isDuplicate)
        // 3. 強制的に内部処理とされるイベント種別である (isForcedInternal) かつ
        //    パブリックな回答 (response_to_user 等) を含んでいない (!isPublicMsg)
        // 4. ユーザー以外からの発信で、かつ宛先が USER ではない (!isUser && recipient != USER)
        
        const isNotForUser = recipient && recipient.toUpperCase() !== 'USER' && recipient.toUpperCase() !== 'ALL';
        
        let shouldShowBubble = false;
        if (isUser) {
            shouldShowBubble = true;
        } else if (isPublicMsg && !isDuplicate && !isNotForUser) {
            // 公開メッセージがあり、重複しておらず、宛先がUSER/不明の場合のみバブル
            shouldShowBubble = true;
        }

        if (!shouldShowBubble) {
            const safeTime = (rt.created_at || "").replace(/[^0-9a-zA-Z]/g, '');
            const safeType = (rt.event_type || "internal").replace(/[^a-zA-Z0-9]/g, '');
            const rowId = 'th-' + safeTime + '-' + safeType;
            const isOpen = openDetails.has(rowId) ? 'open' : '';
            html += `<div class="th">
              <details data-id="${rowId}" ${isOpen}>
                <summary>🧠 Internal Processing: ${escape(rt.event_type || "UNKNOWN")} (Click to expand)</summary>
                <div class="tp">${escape(JSON.stringify(rt.payload, null, 2))}</div>
              </details>
            </div>`;
        } else {
            // 吹き出し表示
            if (isUser) {
                html += `
                <div class="row u">
                   <div class="av">👽</div>
                   <div>
                      <div class="b">${escape(msg || JSON.stringify(rt.payload))}</div>
                      <div class="ts">${escape(rt.created_at)}</div>
                   </div>
                </div>`;
            } else if (isNotify) {
                html += `
                <div class="row a">
                   <div class="av">⚙️</div>
                   <div>
                      <div class="b">${escape(msg)}</div>
                      <div class="ts">${escape(rt.created_at)}</div>
                   </div>
                </div>`;
            } else {
                const rowClass = isError ? "e" : "a";
                html += `
                <div class="row ${rowClass}">
                   <div class="av">🤖</div>
                   <div>
                      <div class="b">${escape(msg)}</div>
                      <div class="ts">${escape(rt.created_at)}</div>
                   </div>
                </div>`;
            }
        }
      });
      return html;
  }


  // Polling Loop
  async function fetchData() {
      try {
          const res = await fetch('/api/data');
          if(!res.ok) return;
          const json = await res.json();
          
          const chatContent = document.getElementById('chat-content');
          const tblSession = document.querySelector('#tbl-session tbody');
          const tblLtm = document.querySelector('#tbl-ltm tbody');
          const clock = document.getElementById('clock');

          if (chatContent) chatContent.innerHTML = renderChatData(json.chat);
          if (tblSession) tblSession.innerHTML = renderTableRows(json.session, 'sess');
          if (tblLtm) tblLtm.innerHTML = renderTableRows(json.ltm, 'ltm');
          if (clock) clock.innerText = json.time;

          if (json.state) {
              const s = json.state;
              const stCog = document.getElementById('st-cog');
              const stUrg = document.getElementById('st-urg');
              const stEmo = document.getElementById('st-emo');
              const stVal = document.getElementById('st-val');

              if (stCog && s.cognitive_load != null) stCog.innerText = Number(s.cognitive_load).toFixed(2);
              if (stUrg && s.urgency != null) stUrg.innerText = Number(s.urgency).toFixed(2);
              if (stEmo && s.emotion_mu != null) stEmo.innerText = Number(s.emotion_mu).toFixed(2);
              if (stVal && s.value_v != null) stVal.innerText = Number(s.value_v).toFixed(2);
          }
          
          if(activeTab === 'chat') scrollChat();
      } catch(e) {
          console.error("Fetch Data Error:", e);
      }
  }

  // ===================================================================
  // Graph Explorer (D3.js Force Layout)
  // ===================================================================
  const graphTypeColors = {
      'cognition_result': '#2563EB',
      'action_result': '#059669',
      'dmn_thought': '#7C3AED',
      'reflection_result': '#D97706',
      'user_input': '#EC4899',
      'system_notify': '#6366F1',
      'consolidated': '#0D9488',
      'unknown': '#94A3B8',
  };
  function getNodeColor(eventType) {
      const et = (eventType || '').toLowerCase();
      for (const [key, color] of Object.entries(graphTypeColors)) {
          if (et.includes(key)) return color;
      }
      return graphTypeColors['unknown'];
  }

  let graphSimulation = null;
  let graphInitialized = false;

  async function loadGraph() {
      try {
          const res = await fetch('/api/graph');
          if (!res.ok) return;
          const data = await res.json();
          renderGraph(data);
      } catch(e) {
          console.error("Graph load error:", e);
      }
  }

  function renderGraph(data) {
      if (typeof d3 === 'undefined') {
          console.warn("D3.js not loaded. Skipping graph render.");
          return;
      }
      if (!data) return;
      data.nodes = data.nodes || [];
      data.links = data.links || [];
      
      const container = document.querySelector('.graph-main');
      const svg = d3.select('#graph-svg');
      const tooltip = document.getElementById('graph-tooltip');
      const detail = document.getElementById('graph-detail');
      const statsEl = document.getElementById('graph-stats');
      const legendEl = document.getElementById('graph-legend');

      // Clear previous
      svg.selectAll('*').remove();
      if (!container || !statsEl) return;

      const width = container.clientWidth;
      const height = container.clientHeight;
      svg.attr('viewBox', [0, 0, width, height]);

      statsEl.innerText = `Nodes: ${data.nodes.length} | Edges: ${data.links.length}`;

      if (data.nodes.length === 0) {
          svg.append('text')
              .attr('x', width / 2).attr('y', height / 2)
              .attr('text-anchor', 'middle')
              .attr('fill', '#94A3B8').attr('font-size', '14px')
              .text('No graph data available. Events with importance >= 0.7 or causal links are stored here.');
          return;
      }

      // Build legend
      const typesInData = [...new Set(data.nodes.map(n => n.event_type || 'Unknown'))];
      legendEl.innerHTML = typesInData.map(t => 
          `<div class="graph-legend-item"><div class="graph-legend-dot" style="background:${getNodeColor(t)}"></div>${t}</div>`
      ).join('');

      // Force simulation
      const simulation = d3.forceSimulation(data.nodes)
          .force('link', d3.forceLink(data.links).id(d => d.id).distance(80))
          .force('charge', d3.forceManyBody().strength(-200))
          .force('center', d3.forceCenter(width / 2, height / 2))
          .force('collision', d3.forceCollide().radius(d => getRadius(d) + 4));

      graphSimulation = simulation;

      // Zoom
      const g = svg.append('g');
      const zoom = d3.zoom()
          .scaleExtent([0.2, 5])
          .on('zoom', (event) => g.attr('transform', event.transform));
      svg.call(zoom);

      // Arrow marker
      g.append('defs').append('marker')
          .attr('id', 'arrowhead')
          .attr('viewBox', '0 -5 10 10')
          .attr('refX', 20).attr('refY', 0)
          .attr('markerWidth', 6).attr('markerHeight', 6)
          .attr('orient', 'auto')
          .append('path')
          .attr('d', 'M0,-5L10,0L0,5')
          .attr('fill', '#CBD5E1');

      // Links
      const link = g.append('g')
          .selectAll('line')
          .data(data.links)
          .join('line')
          .attr('stroke', '#CBD5E1')
          .attr('stroke-width', 1.5)
          .attr('stroke-opacity', 0.6)
          .attr('marker-end', 'url(#arrowhead)');

      // Nodes
      function getRadius(d) {
          return Math.max(5, Math.min(18, (d.importance || 0.5) * 16));
      }

      const node = g.append('g')
          .selectAll('circle')
          .data(data.nodes)
          .join('circle')
          .attr('r', d => getRadius(d))
          .attr('fill', d => getNodeColor(d.event_type))
          .attr('stroke', '#fff')
          .attr('stroke-width', 1.5)
          .style('cursor', 'pointer')
          .on('mouseover', function(event, d) {
              d3.select(this).attr('stroke', '#111827').attr('stroke-width', 2.5);
              tooltip.style.opacity = 1;
              tooltip.innerHTML = `<strong>${d.event_type}</strong><br>${d.created_at}<br><em>${d.payload_summary}</em>`;
          })
          .on('mousemove', function(event) {
              const rect = container.getBoundingClientRect();
              tooltip.style.left = (event.clientX - rect.left + 14) + 'px';
              tooltip.style.top = (event.clientY - rect.top - 10) + 'px';
          })
          .on('mouseout', function() {
              d3.select(this).attr('stroke', '#fff').attr('stroke-width', 1.5);
              tooltip.style.opacity = 0;
          })
          .on('click', async function(event, d) {
              // Fetch full payload from /api/graph data
              detail.innerHTML = `
                  <h3>🔗 ${d.event_type}</h3>
                  <div class="gd-label">Event ID</div>
                  <div class="gd-value">${d.id}</div>
                  <div class="gd-label">Created At</div>
                  <div class="gd-value">${d.created_at}</div>
                  <div class="gd-label">Importance</div>
                  <div class="gd-value">${(d.importance || 0).toFixed(2)}</div>
                  <div class="gd-label">Summary</div>
                  <div class="gd-value">${d.payload_summary}</div>
              `;
              // Highlight selected node
              node.attr('stroke', '#fff').attr('stroke-width', 1.5);
              d3.select(this).attr('stroke', '#111827').attr('stroke-width', 3);
          })
          .call(d3.drag()
              .on('start', (event, d) => {
                  if (!event.active) simulation.alphaTarget(0.3).restart();
                  d.fx = d.x; d.fy = d.y;
              })
              .on('drag', (event, d) => {
                  d.fx = event.x; d.fy = event.y;
              })
              .on('end', (event, d) => {
                  if (!event.active) simulation.alphaTarget(0);
                  d.fx = null; d.fy = null;
              })
          );

      // Labels (short id)
      const labels = g.append('g')
          .selectAll('text')
          .data(data.nodes)
          .join('text')
          .text(d => d.event_type.replace(/_/g, ' ').slice(0, 12))
          .attr('font-size', '8px')
          .attr('fill', '#64748B')
          .attr('text-anchor', 'middle')
          .attr('dy', d => getRadius(d) + 12)
          .style('pointer-events', 'none');

      simulation.on('tick', () => {
          link
              .attr('x1', d => d.source.x).attr('y1', d => d.source.y)
              .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
          node
              .attr('cx', d => d.x).attr('cy', d => d.y);
          labels
              .attr('x', d => d.x).attr('y', d => d.y);
      });
  }
</script>
</body>
</html>"""
        for k, v in COLORS.items():
            html = html.replace(f"__COLOR_{k.upper()}__", v)
        return html

def run():
    print(f"Starting Narv Native Web Dashboard at http://localhost:{PORT}")
    print("Press Ctrl+C to stop.")
    server_address = ('', PORT)
    httpd = HTTPServer(server_address, DashboardHandler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    httpd.server_close()
    print("Dashboard stopped.")

if __name__ == '__main__':
    run()
