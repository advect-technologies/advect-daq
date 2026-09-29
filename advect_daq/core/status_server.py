import asyncio
import datetime as dt
import json
from dataclasses import asdict

from aiohttp import WSMsgType, web
from daq_tools.models import DataPoint

from .engine import AdvectEngine, LatestReading
from .logging import log


def _datapoint_to_dict(dp: DataPoint):
    try:
        return asdict(dp)
    except Exception:
        return {}


def _reading_to_dict(reading: LatestReading, include_data: bool) -> dict:
    payload = {
        "type": reading.type,
        "sensor": reading.sensor,
        "written": reading.written,
        "success": reading.success,
        "healthy": reading.healthy,
        "error_type": int(reading.error_type),
        "error_message": reading.error_message,
        "timestamp": dt.datetime.now(dt.UTC).isoformat(),
    }
    if include_data:
        payload["datapoints"] = [_datapoint_to_dict(dp) for dp in reading.datapoints]
    return payload


class StatusServer:
    def __init__(
        self, engine: AdvectEngine, port: int = 8080, expose_data: bool = False
    ):
        self.engine = engine
        self.port = port
        self.runner = None
        self.expose_data = expose_data

    async def health(self, request):
        return web.json_response(
            {
                "status": "healthy",
                "timestamp": dt.datetime.now(dt.UTC).isoformat(),
                "active_sensors": len(self.engine.sensors),
            }
        )

    async def latest_data(self, request):
        """Return latest raw sensor readings as JSON."""
        sensor_name = request.query.get("sensor") or request.match_info.get("sensor")

        if sensor_name:
            reading = self.engine.latest.get(sensor_name)
            if reading is None:
                return web.json_response(
                    {"error": f"Sensor '{sensor_name}' not found or has no data yet"},
                    status=404,
                )
            data = {sensor_name: [_datapoint_to_dict(d) for d in reading.datapoints]}

        else:
            data = {
                name: [_datapoint_to_dict(d) for d in reading.datapoints]
                for name, reading in self.engine.latest.items()
            }

        return web.json_response(
            {
                "status": "ok",
                "timestamp": dt.datetime.now(dt.UTC).isoformat(),
                "data": data,
            }
        )

    def _sensor_status_rows(self) -> list[dict]:
        now = asyncio.get_running_loop().time()
        sensors_status = []

        for name, sensor in self.engine.sensors.items():
            reading = self.engine.latest.get(name)
            age = (now - reading.loop_time) if reading is not None else None
            sensor_type = getattr(getattr(sensor, "config", None), "type", "unknown")
            write_interval = getattr(
                getattr(sensor, "config", None), "write_interval", None
            )

            sensors_status.append(
                {
                    "name": name,
                    "type": sensor_type,
                    "interval": sensor.interval,
                    "write_interval": write_interval,
                    "last_read_seconds_ago": round(age, 1) if age is not None else None,
                    "healthy": sensor.healthy,
                    "error_type": sensor.last_error_type.value,
                    "error_message": sensor.last_error,
                    "consecutive_errors": sensor.consecutive_errors,
                }
            )
        return sensors_status

    async def status(self, request):
        return web.json_response(
            {
                "status": "running",
                "timestamp": dt.datetime.now(dt.UTC).isoformat(),
                "active_sensors": len(self.engine.sensors),
                "sensors": self._sensor_status_rows(),
                "writer_queue_size": getattr(self.engine.writer, "queue", None).qsize()
                if hasattr(self.engine.writer, "queue")
                else 0,
            }
        )

    async def websocket(self, request):
        ws = web.WebSocketResponse(heartbeat=30.0)
        await ws.prepare(request)
        queue = self.engine.subscribe_live()
        log.info("Live WS client connected")

        async def pump():
            while not ws.closed:
                event = await queue.get()
                payload = _reading_to_dict(event, include_data=self.expose_data)
                await ws.send_json(payload)

        pump_task = asyncio.create_task(pump())
        try:
            async for msg in ws:
                if msg.type in {WSMsgType.CLOSE, WSMsgType.ERROR}:
                    break
        finally:
            pump_task.cancel()
            try:
                await pump_task
            except asyncio.CancelledError:
                pass
            self.engine.unsubscribe_live(queue)
            if not ws.closed:
                await ws.close()
            log.info("Live WS client disconnected")
        return ws

    async def html_status(self, request):
        """Live dashboard driven by /ws. JSON routes are unchanged."""
        initial = {
            "sensors": self._sensor_status_rows(),
            "expose_data": self.expose_data,
        }
        bootstrap = json.dumps(initial)
        html = f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <title>Advect-DAQ • Live</title>
            <style>
                :root {{
                    --bg: #0f1117;
                    --card: #1a1f2e;
                    --text: #e0e0e0;
                    --text-muted: #a0a0a0;
                    --border: #2a3347;
                }}
                body {{
                    font-family: 'Segoe UI', Arial, sans-serif;
                    margin: 0;
                    padding: 20px;
                    background: var(--bg);
                    color: var(--text);
                }}
                h1 {{ color: #4fc3f7; }}
                .header {{ margin-bottom: 20px; }}
                table {{
                    border-collapse: collapse;
                    table-layout: fixed;
                    width: 100%;
                    background: var(--card);
                    border-radius: 8px;
                    overflow: hidden;
                    box-shadow: 0 4px 12px rgba(0,0,0,0.3);
                }}
                th, td {{
                    padding: 14px;
                    text-align: left;
                    border-bottom: 1px solid var(--border);
                    vertical-align: top;
                }}
                th {{
                    background: #1f2937;
                    color: #90caf9;
                }}
                th:nth-child(1) {{ width: 16%; }}
                th:nth-child(2) {{ width: 12%; }}
                th:nth-child(3), th:nth-child(4) {{ width: 9%; }}
                th:nth-child(5) {{ width: 8%; }}
                th:nth-child(6) {{ width: 46%; }}
                tr:hover {{ background: #252d3f; }}
                .ok {{ color: #66ff99; font-weight: bold; }}
                .warning {{ color: #ffcc33; font-weight: bold; }}
                .error {{ color: #ff6666; font-weight: bold; }}
                .muted {{ color: var(--text-muted); }}
                .written {{ color: #90caf9; font-size: 0.75em; margin-left: 8px; }}
                .field-grid {{
                    display: grid;
                    grid-template-columns: minmax(10ch, 1fr) 14ch;
                    column-gap: 12px;
                    row-gap: 2px;
                    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
                    font-size: 0.85em;
                    font-variant-numeric: tabular-nums;
                }}
                .field-k {{ color: var(--text-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
                .field-v {{ text-align: right; white-space: nowrap; }}
                .refresh {{ color: var(--text-muted); font-size: 0.9em; }}
            </style>
        </head>
        <body>
            <div class="header">
                <h1>Advect-DAQ Live</h1>
                <p class="refresh">WS: <span id="ws-state">connecting</span> · last event: <span id="last-event">—</span></p>
                <p><strong>Active Sensors:</strong> <span id="active-count">{len(self.engine.sensors)}</span></p>
            </div>
            <table>
                <thead>
                    <tr>
                        <th>Sensor</th>
                        <th>Type</th>
                        <th>Interval</th>
                        <th>Write</th>
                        <th>Status</th>
                        <th>Last sample</th>
                    </tr>
                </thead>
                <tbody id="sensor-rows"></tbody>
            </table>
            <p style="margin-top: 30px;">
                <a href="/status" style="color: #90caf9;">JSON Status</a> |
                <a href="/health" style="color: #90caf9;">Health</a> |
                <a href="/data" style="color: #90caf9;">JSON Data</a>
            </p>
            <script>
                const bootstrap = {bootstrap};
                const rows = {{}};

                function fmtNum(v) {{
                    if (v === null || v === undefined) return '—';
                    if (typeof v === 'number' && Number.isFinite(v)) {{
                        const abs = Math.abs(v);
                        const digits = abs >= 1000 ? 1 : abs >= 10 ? 2 : 4;
                        return v.toFixed(digits);
                    }}
                    return String(v);
                }}

                function fieldGridHtml(datapoints) {{
                    if (!datapoints || !datapoints.length) return '';
                    const rowsHtml = [];
                    datapoints.forEach(dp => {{
                        const fields = dp.fields || {{}};
                        Object.keys(fields).forEach(k => {{
                            rowsHtml.push(
                                '<div class="field-k">' + k + '</div>' +
                                '<div class="field-v">' + fmtNum(fields[k]) + '</div>'
                            );
                        }});
                    }});
                    return '<div class="field-grid">' + rowsHtml.join('') + '</div>';
                }}

                function upsert(sensor) {{
                    let tr = rows[sensor.name];
                    if (!tr) {{
                        tr = document.createElement('tr');
                        tr.innerHTML = '<td class="name"></td><td class="type"></td><td class="interval"></td><td class="write"></td><td class="status"></td><td class="sample"></td>';
                        document.getElementById('sensor-rows').appendChild(tr);
                        rows[sensor.name] = tr;
                    }}
                    tr.querySelector('.name').innerHTML = sensor.name + (sensor.written ? '<span class="written">write</span>' : '');
                    tr.querySelector('.type').textContent = sensor.type || '';
                    tr.querySelector('.interval').textContent = (sensor.interval ?? '') + 's';
                    tr.querySelector('.write').textContent = sensor.write_interval == null ? 'every sample' : (sensor.write_interval + 's');
                    const status = tr.querySelector('.status');
                    status.textContent = sensor.healthy ? 'OK' : (sensor.error_type === 1 ? 'WARNING' : 'ERROR');
                    status.className = 'status ' + (sensor.healthy ? 'ok' : (sensor.error_type === 1 ? 'warning' : 'error'));
                    if (sensor.fieldsHtml !== undefined) {{
                        tr.querySelector('.sample').innerHTML = sensor.fieldsHtml;
                    }}
                }}

                bootstrap.sensors.forEach(upsert);
                document.getElementById('active-count').textContent = bootstrap.sensors.length;

                function connect() {{
                    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
                    const ws = new WebSocket(proto + '://' + location.host + '/ws');
                    const state = document.getElementById('ws-state');
                    ws.onopen = () => {{ state.textContent = 'connected'; }};
                    ws.onclose = () => {{
                        state.textContent = 'disconnected — retrying';
                        setTimeout(connect, 1500);
                    }};
                    ws.onerror = () => {{ state.textContent = 'error'; }};
                    ws.onmessage = (ev) => {{
                        const msg = JSON.parse(ev.data);
                        document.getElementById('last-event').textContent = msg.timestamp || '';
                        const existing = bootstrap.sensors.find(s => s.name === msg.sensor) || {{ name: msg.sensor }};
                        existing.healthy = msg.healthy;
                        existing.error_type = msg.error_type;
                        existing.written = !!msg.written;
                        if (msg.datapoints) existing.fieldsHtml = fieldGridHtml(msg.datapoints);
                        upsert(existing);
                    }};
                }}
                connect();
            </script>
        </body>
        </html>
        """
        return web.Response(text=html, content_type="text/html")

    async def start(self):
        app = web.Application()
        app.router.add_get("/health", self.health)
        app.router.add_get("/status", self.status)
        app.router.add_get("/", self.html_status)
        app.router.add_get("/ws", self.websocket)
        if self.expose_data:
            app.router.add_get("/data", self.latest_data)
            app.router.add_get("/data/{sensor}", self.latest_data)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", self.port)
        await site.start()

        self.runner = runner
        log.success(f"🌐 Status server running on http://0.0.0.0:{self.port}")
        log.info(f"→ Dashboard: http://localhost:{self.port}/")
        log.info(f"→ Live WS:   ws://localhost:{self.port}/ws")

    async def stop(self):
        if self.runner:
            await self.runner.cleanup()
