"""
server_fenet_ws.py ── LS‑XGT FEnet 모니터·제어 서버 (WebSocket 연동)
================================================================

◼︎ 개요
---------------------------------------------------------------
* **목적** : ① PLC(XGT/XGB) 변수 실시간 모니터링, ② WebSocket
  클라이언트(브라우저 등)에서 보내는 *write* 명령을 PLC로 중계.
* **구성** :

        ┌─────────┐  FEnet (%DW / %MX)  ┌─────────────┐
        │  PLC 20 │◀───────────────────▶│ SafePLC(20) │
        ├─────────┤                     └─────────────┘
        │  PLC 21 │◀───────────────────▶│ SafePLC(21) │
        └─────────┘                     ▲    ▲   ▲
                                         │    │   │ ThreadPoolExecutor
                                         │    │
        ▲ WebSocket Subscribe            │    └─ watch_plc()
        │ Write(JSON)                    │
   ┌──────────────┐      JSON            │
   │   Browser    │◀─────────────────────┘ Hub.push()
   └──────────────┘

* **주요 스레드/태스크**
  - **watch_plc()** : PLC → 서버. AUTO_WATCH 목록을 주기적으로 읽어
    모든 구독자에게 *push*.
  - **Hub.handler()** : 클라이언트 WebSocket 엔드포인트.
  - **_handle_write()** : WS ‘write’ 요청 → ThreadPool에서 PLC 쓰기.
  - **CLI thread** : 터미널에서 즉석 테스트용 쓰기.

※ *단발 read* 명령은 지원하지 않고, subscribe 푸시만 제공.
"""
from __future__ import annotations

import asyncio
import json
import logging
import socket
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Set

import websockets
from fenet import FEnetClient, FEnetError

# ───────────────────────────── 로깅 설정 ──────────────────────────
logging.basicConfig(
    level=logging.INFO,  # 필요 시 DEBUG 로 변경
    format="[%(levelname)s] %(message)s",
)

# ──────────────────────── WebSocket Hub 정의 ─────────────────────
class Hub:
    """WebSocket 구독/브로드캐스트 허브.

    * rooms[tag] = {ws1, ws2, ...}
    * subscribe : rooms[tag] 에 소켓 추가.
    * push(tag,val) : 해당 tag 구독자에게 값 전송. 끊긴 소켓은 제거.
    """

    def __init__(self) -> None:
        self.rooms: Dict[str, Set[websockets.WebSocketServerProtocol]] = {}

    # ---------- 클라이언트 진입점 ----------
    async def handler(self, ws: websockets.WebSocketServerProtocol):
        """클라이언트 1명과의 수명주기를 담당."""
        try:
            async for msg in ws:
                # 1) JSON 파싱 -------------------------------------------------
                try:
                    req = json.loads(msg)
                except json.JSONDecodeError:
                    await ws.send(json.dumps({"error": "invalid json"}))
                    continue

                cmd = req.get("cmd")

                # 2) subscribe 처리 -------------------------------------------
                if cmd == "subscribe":
                    tag = req.get("tag")
                    if tag:
                        self.rooms.setdefault(tag, set()).add(ws)
                        logging.debug("SUBSCRIBE %s", tag)

                # 3) write 처리 -----------------------------------------------
                elif cmd == "write":
                    await self._handle_write(ws, req)
        finally:
            # 소켓 끊김: 모든 rooms에서 제거
            for subs in self.rooms.values():
                subs.discard(ws)

    # ---------- write 요청 처리 ----------
    async def _handle_write(self, ws, req: dict):
        """WS write → PLC write → broadcast."""
        ip  = str(req.get("ip", "")).strip()
        var = str(req.get("var", "")).strip()
        val_raw = req.get("value")

        # ── value 정수 변환 (str/bool 허용) ───────────────────────
        try:
            if isinstance(val_raw, bool):
                val = 1 if val_raw else 0
            elif isinstance(val_raw, (int, float)):
                val = int(val_raw)
            elif isinstance(val_raw, str) and val_raw.strip().lstrip("-+").isdigit():
                val = int(val_raw)
            else:
                raise ValueError("value must be int‑like")
        except ValueError as e:
            await ws.send(json.dumps({"error": str(e)}))
            return

        # ── 대상 PLC 선택 ────────────────────────────────────────
        plc = PLC.get(ip)
        if not plc:
            await ws.send(json.dumps({"error": f"unknown plc {ip}"}))
            return

        logging.info("WRITE → PLC%s %s = %s", ip, var, val)

        # ── 실제 PLC 쓰기 (스레드 풀) ─────────────────────────────
        try:
            await loop.run_in_executor(pool, plc.write, var, val)
        except (FEnetError, socket.error) as e:
            logging.warning("PLC WRITE ERR %s: %s", ip, e)
            await ws.send(json.dumps({"error": str(e)}))
            return

        # ── ACK & 브로드캐스트 ──────────────────────────────────
        await ws.send(json.dumps({"status": "ok", "var": var, "value": val}))
        await self.push(f"{var}@{ip}", val)

    # ---------- 값 푸시 ----------
    async def push(self, tag: str, val: int):
        """tag 구독자에게 값 전송. 끊긴 소켓은 정리."""
        dead: list[websockets.WebSocketServerProtocol] = []
        for ws in self.rooms.get(tag, set()):
            try:
                await ws.send(json.dumps({"tag": tag, "value": val}))
            except websockets.ConnectionClosed:
                dead.append(ws)
        for ws in dead:
            self.rooms[tag].discard(ws)

# Hub 인스턴스 (전역)
hub = Hub()

# 이벤트 루프 & 쓰레드풀 (전역) ------------------------------
loop: asyncio.AbstractEventLoop  # main()에서 초기화
pool = ThreadPoolExecutor(max_workers=8)

# ──────────────────────── PLC 래퍼 (재접속 지원) ───────────────────
class SafePLC:
    """thread‑safe FEnetClient + 자동 재접속."""

    def __init__(
        self,
        ip: str,
        port: int = 2002,
        timeout: float = 3,
        retry: int = 2,
    ) -> None:
        self.ip = ip
        self.port = port
        self.timeout = timeout
        self.retry = retry
        self._cli = FEnetClient(ip, port=port, timeout=timeout)
        self._lock = threading.Lock()

    # ---------- 내부 유틸 ----------
    def _ensure(self):
        """세션이 죽어 있으면 재연결."""
        try:
            self._cli.ping()  # fenet 0.3.1+
        except Exception:
            self._cli.close()
            self._cli = FEnetClient(self.ip, port=self.port, timeout=self.timeout)

    # ---------- PUBLIC read / write ----------
    def read(self, var: str) -> int:
        with self._lock:
            for _ in range(self.retry):
                try:
                    return self._cli.read(var)
                except (FEnetError, socket.error):
                    self._ensure()
            raise

    def write(self, var: str, value: int):
        with self._lock:
            for _ in range(self.retry):
                try:
                    self._cli.write(var, value)
                    return
                except (FEnetError, socket.error):
                    self._ensure()
            raise

    def close(self):
        self._cli.close()

# ───────────────────────── PLC 목록 ───────────────────────────
PLC: Dict[str, SafePLC] = {
    "20": SafePLC("192.168.0.20", 2002),
    "21": SafePLC("192.168.0.21", 2002),
}

# ──────────────────────── AUTO‑WATCH 테이블 ─────────────────────
AUTO_WATCH: Dict[str, list[str]] = {
    "20": ["%DW2500", "%DW2516", "%MX00072", "%MX00053", "%MX00054"],
    "21": ["%DW2520", "%DW16002", "%MX00072", "%MX00053", "%MX00054"],
}

# ───────────────────────── 모니터 태스크 ─────────────────────────
async def watch_plc(ip: str, interval: float = 1.0):
    """주기적으로 PLC 변수를 읽어 WebSocket 구독자에게 푸시."""
    plc = PLC[ip]
    while True:
        for var in AUTO_WATCH[ip]:
            try:
                val = await loop.run_in
