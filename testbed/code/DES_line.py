import asyncio
import json
import logging
from opcua import Client, ua
from websockets.server import serve

logging.disable(logging.CRITICAL)

SERVER_URL = "opc.tcp://192.168.3.2:4840"
NODE_CONFIG = {
    "auto":    {"id": "ns=2;i=1536917889", "vt": ua.VariantType.Boolean},
    "initial": {"id": "ns=2;i=60321864",   "vt": ua.VariantType.Boolean},
    "manual":  {"id": "ns=2;i=1177831012", "vt": ua.VariantType.Boolean},
    "start":   {"id": "ns=2;i=1903713966", "vt": ua.VariantType.Boolean},
    "stop":    {"id": "ns=2;i=821685430",  "vt": ua.VariantType.Boolean},
}

opc_client = Client(SERVER_URL)
opc_client.connect()
nodes = {name: opc_client.get_node(cfg['id']) for name, cfg in NODE_CONFIG.items()}

def write_value(name: str, raw: bool):
    cfg = NODE_CONFIG[name]
    node = nodes[name]
    dv = ua.DataValue(ua.Variant(raw, cfg['vt']))
    dv.SourceTimestamp = None
    dv.ServerTimestamp = None
    dv.StatusCode = None
    node.set_value(dv)
    print(f"[OPC UA] {name} ← {int(raw)}")

async def plc_control(websocket):
    print("WebSocket 클라이언트 연결됨")
    try:
        async for msg in websocket:
            data = json.loads(msg)
            node = data.get('node')
            val  = data.get('value')
            if node not in NODE_CONFIG or val not in (0,1):
                await websocket.send(json.dumps({'status':'error','message':'Invalid node or value'}))
                continue

            if node == 'start' and val == 1:
                seq = [
                    ('start', True),
                    ('manual', False),
                    ('auto', True),
                    ('stop', False)
                ]
                for name, v in seq:
                    write_value(name, v)
                await websocket.send(json.dumps({'status':'ok','sequence':'start'}))
                continue

            if node == 'stop' and val == 1:
                for name, v in [('stop', True), ('auto', True), ('start', False)]:
                    write_value(name, v)
                await asyncio.sleep(1)
                for name, v in [('stop', False), ('auto', True), ('initial', True)]:
                    write_value(name, v)
                for name, v in [('manual', True), ('auto', False)]:
                    write_value(name, v)
                await websocket.send(json.dumps({'status':'ok','sequence':'stop'}))
                continue

            write_value(node, bool(val))
            await websocket.send(json.dumps({'status':'ok','node':node,'value':val}))

    except Exception as e:
        print(f"WebSocket 핸들러 예외: {e}")
    finally:
        print("WebSocket 클라이언트 연결 종료")

async def main():
    async with serve(plc_control, '0.0.0.0', 8777):
        print("WebSocket 서버 시작: ws://0.0.0.0:8777")
        await asyncio.Future()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    finally:
        opc_client.disconnect()
        print("OPC UA 연결 종료")
