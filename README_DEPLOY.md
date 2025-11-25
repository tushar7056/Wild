WinGo Predictor (mobile-first)

1) Files: server.py, static/*, requirements.txt, Procfile
2) Set env var (optional but recommended):
   INITIAL_HISTORY_URL=/mnt/data/win1m_results.json
   (or set to a raw github url if you host the history in repo)
3) Install: pip install -r requirements.txt
4) Run locally: uvicorn server:app --reload --host 0.0.0.0 --port 8000
5) On Replit: create Python Repl, upload files, set Run command to:
   uvicorn server:app --host=0.0.0.0 --port=8000
6) Visit root URL to see the mobile UI. WebSocket updates arrive automatically.
7) 
