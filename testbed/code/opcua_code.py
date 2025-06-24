import json
import asyncio
import logging
from opcua import Client, ua
import websockets
from websockets.exceptions import ConnectionClosedOK, ConnectionClosedError

logging.disable(logging.CRITICAL)

def find_node_by_name(node, target_name):
    for child in node.get_children():
        dn = child.get_display_name().Text
        if target_name.lower() in dn.lower():
            return child
        found = find_node_by_name(child, target_name)
        if found:
            return found
    return None

class LSOPCUA:
    def __init__(self, endpoint="opc.tcp://192.168.0.21:4840"):
        self.client = Client(endpoint)

    def connect(self):
        self.client.connect()
        print("[OPC UA] 서버 연결 성공")

    def disconnect(self):
        self.client.disconnect()
        print("[OPC UA] 서버 연결 종료")

    def write(self, var_folder, name, raw):
        node = find_node_by_name(var_folder, name)
        if not node:
            return False
        if raw in ("0", "1"):
            v = bool(int(raw))
            vt = ua.VariantType.Boolean
        else:
            v = int(raw)
            vt = vt = ua.VariantType.UInt16
        dv = ua.DataValue(ua.Variant(v, vt))
        dv.SourceTimestamp = None
        dv.ServerTimestamp = None
        dv.StatusCode = None
        node.set_value(dv)
        return True

    def read(self, var_folder, name):
        node = find_node_by_name(var_folder, name)
        if not node:
            return None
        return node.get_value()

# PLC 연결
plc = LSOPCUA()
plc.connect()
root = plc.client.get_root_node()
objects = root.get_child(["0:Objects"])
try:
    var_folder = objects.get_child(["2:NewPLC", "2:VariableComment"])
except Exception as e:
    print("모듈 경로를 찾을 수 없습니다", e)
    plc.disconnect()
    raise SystemExit

# WebSocket 클라이언트 관리
connected_websockets = set()

# 모니터링
async def plc_monitor():
    target_nodes = ["Initial","dev_flux","dev_tem","dev_rpm","etc_flux","etc_tem","etc_rpm"]  # 모니터링할 노드들
    while True:
        for node_name in target_nodes:
            value = plc.read(var_folder, node_name)
            if value is not None:
                data = json.dumps({'node': node_name, 'value': value})
                for ws in connected_websockets.copy():
                    try:
                        await ws.send(data)
                    except Exception as e:
                        print(f"[에러] 클라이언트 전송 실패: {e}")
                        connected_websockets.discard(ws)
        await asyncio.sleep(1)

async def plc_control(websocket, path):
    connected_websockets.add(websocket)
    try:
        async for msg in websocket:
            data = json.loads(msg)
            name = data.get('node')
            val = data.get('value')
            if not name or val is None:
                await websocket.send(json.dumps({'status': 'error', 'message': 'node 또는 value 누락'}))
                continue
            raw = str(val)
            success = plc.write(var_folder, name, raw)
            if success:
                await websocket.send(json.dumps({'status': 'ok', 'node': name, 'value': val}))
            else:
                await websocket.send(json.dumps({'status': 'error', 'message': f'노드를 찾을 수 없습니다: {name}'}))
    except (ConnectionClosedOK, ConnectionClosedError):
        pass
    except Exception as e:
        print(f"[에러] {e}")
    finally:
        connected_websockets.discard(websocket)

async def main():
    monitor_task = asyncio.create_task(plc_monitor())
    async with websockets.serve(plc_control, '0.0.0.0', 8777, ping_interval=None, ping_timeout=None):
        await asyncio.Future()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    finally:
        plc.disconnect()