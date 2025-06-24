import json
import asyncio
import websockets

WS_URI = "ws://25.3.90.101:8777"

async def bridge_client():
    try:
        async with websockets.connect(WS_URI, open_timeout=30) as ws:
            print(f"[Bridge] 서버 연결됨 → {WS_URI}")
            while True:
                cmd = input("명령 입력 (start/stop/exit): ").strip().lower()
                if cmd in ("exit", "quit"):
                    print("[Bridge] 종료합니다.")
                    break
                if cmd not in ("start", "stop"):
                    print("[Bridge] 알 수 없는 명령입니다. 'start' 또는 'stop'만 입력하세요.")
                    continue

                message = {"node": cmd, "value": 1}
                await ws.send(json.dumps(message))
                print(f"[Bridge] Sent → {message}")

                reply = await ws.recv()
                try:
                    data = json.loads(reply)
                    print(f"[Bridge] Received ← {data}")
                except json.JSONDecodeError:
                    print(f"[Bridge] Received non-JSON reply: {reply}")

    except Exception as e:
        print(f"[Bridge] 연결 오류: {e}")

if __name__ == '__main__':
    asyncio.run(bridge_client())
