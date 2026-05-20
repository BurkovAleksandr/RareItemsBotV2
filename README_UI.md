# RareItemsBot React + FastAPI UI

## Backend

```powershell
python -m pip install -r requirements.txt
python api_server.py --host 127.0.0.1 --port 8090 --config ./config.json
```

API:

- `GET /api/dashboard`
- `POST /api/bot/start`
- `POST /api/bot/stop`
- `PUT /api/config`
- `PUT /api/items`
- `PUT /api/proxies`

## Frontend

```powershell
cd frontend
npm install
npm run dev
```

Open `http://127.0.0.1:5173`. Vite proxies `/api` to `http://127.0.0.1:8090`.

For production:

```powershell
cd frontend
npm run build
cd ..
python api_server.py --host 127.0.0.1 --port 8090 --config ./config.json
```

When `frontend/dist` exists, FastAPI serves the built React app from the same port.
