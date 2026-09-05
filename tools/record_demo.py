"""Record the README animation from a real assayer session.

    python tools/record_demo.py

Two halves, both offline:

*staging*
    A local HTTP server hands out the fixture documents and a local JSON-RPC stub
    answers the read-only calls, so the CLI runs end to end over real sockets.
    Between the two snapshots the prospectus on the server is replaced and the
    stub mints more tokens, which is the drift the recording is about.

*rendering*
    Frames are drawn rather than screen-captured, but every character in them is
    stdout from those runs. The animation controls only the pace: characters
    appear as if typed, output lines appear one at a time, and the terminal wraps
    at its own width the way a real one does.

Windows fonts are used for the frames (Consolas, with Segoe UI Symbol as a
fallback for the marker glyphs). On another platform, point MONO and SYMBOL at
any monospace font and any font carrying U+26A0, U+2717, U+2A2F and U+2691.
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
import threading
import time
from functools import partial
from http.server import BaseHTTPRequestHandler, HTTPServer, SimpleHTTPRequestHandler

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from conftest import FakeRpc, code_with           # noqa: E402
from fixtures import pages, pdfs                  # noqa: E402

from assayer.snapshot import chain as chain_mod   # noqa: E402

WORK = ROOT.parent / ".assayer-demo"
SERVE = WORK / "served"
HOME = WORK / "home"
OUT = ROOT / "media" / "demo.gif"

DOC_PORT, RPC_PORT = 8741, 8742
TOKEN = "0x1111111111111111111111111111111111111111"
IMPL = "0x3333333333333333333333333333333333333333"
TIMELOCK = "0x5555555555555555555555555555555555555555"
SAFE = "0x6666666666666666666666666666666666666666"

CONFIG = f"""
[agent]
command = "claude"
args = ["-p", "--output-format", "json"]
response_field = "result"
cost_field = "total_cost_usd"

[budget]
max_usd_per_day = 2.00
max_usd_per_extraction = 0.40

[rpc]
ethereum = "http://127.0.0.1:{RPC_PORT}"

[sources]
allowed_domains = ["127.0.0.1"]

[corpus]
max_doc_mb = 40
ocr = false

[gate]
taint_threshold = 3
halt_on_injection = true

[gate.injection_patterns]
en = ["ignore previous", "disregard the above", "system:", "you are an ai",
      "new instructions", "the report must"]

[language]
forbidden = ["safe", "risky", "buy", "sell", "scam", "reliable", "audited",
             "guaranteed", "risk score", "investment advice", "recommend"]
"""


# ---------------------------------------------------------------- staging

def build_chain() -> FakeRpc:
    """A token behind an EIP-1967 proxy with a zero-delay timelock."""
    rpc = FakeRpc(block=21_345_678, chain_id=1)
    rpc.code[TOKEN] = code_with("name()", "symbol()", "decimals()", "totalSupply()",
                                "owner()", delegatecall=True, padding=300)
    rpc.code[IMPL] = code_with("upgradeTo(address)", "mint(address,uint256)",
                               "burn(address,uint256)", "pause()", "paused()",
                               "blacklist(address)", "isBlacklisted(address)",
                               "owner()")
    rpc.code[TIMELOCK] = code_with("getMinDelay()")
    rpc.code[SAFE] = code_with("getThreshold()", "getOwners()")
    rpc.set_storage(TOKEN, chain_mod.SLOT_1967_IMPL, IMPL)
    rpc.set_storage(TOKEN, chain_mod.SLOT_1967_ADMIN, TIMELOCK)
    rpc.set_uint(TIMELOCK, "getMinDelay()", 0)
    rpc.set_uint(TOKEN, "decimals()", 18)
    rpc.set_uint(TOKEN, "totalSupply()", 1_240_000 * 10 ** 18)
    rpc.set_address(TOKEN, "owner()", SAFE)
    rpc.set_uint(SAFE, "getThreshold()", 2)
    rpc.set_call(SAFE, "getOwners()",
                 "0x" + format(32, "064x") + format(3, "064x")
                 + "".join("0" * 24 + f"{i}" * 40 for i in (7, 8, 9)))
    rpc.set_call(TOKEN, "paused()", "0x" + "0" * 64)
    return rpc


CHAIN = build_chain()


class RpcHandler(BaseHTTPRequestHandler):
    """The read-only JSON-RPC surface, answered from the fixture chain."""

    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        request = json.loads(self.rfile.read(length) or b"{}")
        try:
            result = CHAIN.call(request.get("method", ""), request.get("params", []))
            body = {"jsonrpc": "2.0", "id": request.get("id"), "result": result}
        except Exception as exc:
            body = {"jsonrpc": "2.0", "id": request.get("id"),
                    "error": {"code": -32000, "message": str(exc)}}
        payload = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class QuietFiles(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def run(argv: list[str]) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONIOENCODING="utf-8")
    return subprocess.run([sys.executable, "-m", "assayer", "--home", str(HOME)]
                          + argv, capture_output=True, text=True, encoding="utf-8",
                          env=env, cwd=str(ROOT))


def stage() -> list[dict]:
    if WORK.exists():
        shutil.rmtree(WORK)
    (HOME / "assets").mkdir(parents=True)
    (HOME / "state").mkdir()
    (HOME / "config.toml").write_text(CONFIG, encoding="utf-8")
    SERVE.mkdir(parents=True)
    (SERVE / "prospectus.pdf").write_bytes(pdfs.clean_prospectus())
    (SERVE / "custody.html").write_text(
        pages.CLEAN_HTML.replace("Statement dated 31 January 2026.",
                                 "Statement dated 31 January 2026. "
                                 "Reference assets: 1,200,000 shares."),
        encoding="utf-8")

    docs = HTTPServer(("127.0.0.1", DOC_PORT),
                      partial(QuietFiles, directory=str(SERVE)))
    rpc = HTTPServer(("127.0.0.1", RPC_PORT), RpcHandler)
    for server in (docs, rpc):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    time.sleep(0.4)

    session: list[dict] = []

    def record(shown: str, argv: list[str], head: int | None = None) -> None:
        proc = run(argv)
        body = (proc.stdout or "").rstrip("\n").split("\n")
        if head and len(body) > head:
            body = body[:head] + ["…"]
            shown += f" | head -{head}"
        session.append({"command": shown, "output": body, "exit": proc.returncode})
        print(f"{shown} -> exit {proc.returncode}, {len(body)} lines")

    base = f"http://127.0.0.1:{DOC_PORT}"
    run(["add", f"ethereum:{TOKEN}", "--label", "Assay Note ASSY",
         "--docs", f"prospectus={base}/prospectus.pdf",
         f"custody={base}/custody.html"])
    asset_file = next((HOME / "assets").glob("*.toml"))
    asset_file.write_text(
        asset_file.read_text(encoding="utf-8")
        + '\n[reserves]\nclaim_doc = "custody"\nclaimed_units = 1200000\n'
          'unit = "share"\nperiod_days = 30\nattested_at = "2026-01-31"\n'
          '\n[alerts]\nmin_timelock_delay_s = 172800\n'
          'max_supply_over_claim_pct = 1.0\n', encoding="utf-8")

    record("assayer doctor", ["doctor"])
    record("assayer snap assay-note", ["snap", "assay-note"])

    # The issuer replaces the prospectus and mints, between two snapshots.
    (SERVE / "prospectus.pdf").write_bytes(pdfs.build_pdf([
        pdfs.visible("ASSAY ISSUER LIMITED - BASE PROSPECTUS", y=740)
        + pdfs.visible(pdfs.PROSPECTUS_TEXT[0], y=700),
        pdfs.visible(pdfs.PROSPECTUS_TEXT[1], y=700)
        + pdfs.visible(pdfs.PROSPECTUS_TEXT[2], y=660),
        pdfs.visible(pdfs.PROSPECTUS_TEXT[3], y=700),
        pdfs.visible("The Issuer may suspend redemptions for any period.", y=700),
    ]))
    CHAIN.set_uint(TOKEN, "totalSupply()", 1_310_000 * 10 ** 18)
    time.sleep(1.1)  # so the two snapshots land on different timestamps

    record("assayer snap assay-note", ["snap", "assay-note"])
    record("assayer show assay-note", ["show", "assay-note"], head=24)
    record("assayer evidence assay-note freeze", ["evidence", "assay-note", "freeze"])

    for server in (docs, rpc):
        server.shutdown()
    return session


# ---------------------------------------------------------------- rendering

MONO = r"C:\Windows\Fonts\consola.ttf"
SYMBOL = r"C:\Windows\Fonts\seguisym.ttf"
FONT_SIZE = 15
COLS, ROWS = 108, 27

BG = (14, 17, 21)
CHROME = (24, 28, 34)
TITLE_FG = (122, 130, 143)
FG = (201, 209, 217)
PROMPT = (126, 168, 255)
COMMAND = (240, 246, 252)
DOTS = ((237, 106, 94), (245, 191, 79), (98, 197, 84))

PAD_X, PAD_TOP, BAR_H = 16, 12, 30
TYPE_MS, LINE_MS = 45, 55
PAUSE_AFTER_CMD, PAUSE_AFTER_OUT, END_MS = 420, 1400, 2600


def render_gif(session: list[dict]) -> None:
    from fontTools.ttLib import TTFont
    from PIL import Image, ImageDraw, ImageFont

    font = TTFont(MONO, fontNumber=0)
    mono_cmap: set[int] = set()
    for table in font["cmap"].tables:
        mono_cmap |= set(table.cmap.keys())
    mono = ImageFont.truetype(MONO, FONT_SIZE)
    symbol = ImageFont.truetype(SYMBOL, FONT_SIZE)

    cell_w = int(round(mono.getlength("M")))
    cell_h = FONT_SIZE + 6
    width = PAD_X * 2 + cell_w * COLS
    height = BAR_H + PAD_TOP + cell_h * ROWS + 12

    def wrap(line: str) -> list[str]:
        out = []
        while len(line) > COLS:
            out.append(line[:COLS])
            line = line[COLS:]
        out.append(line)
        return out

    def base() -> "Image.Image":
        image = Image.new("RGB", (width, height), BG)
        draw = ImageDraw.Draw(image)
        draw.rectangle([0, 0, width, BAR_H], fill=CHROME)
        for index, colour in enumerate(DOTS):
            x = 16 + index * 18
            draw.ellipse([x, BAR_H // 2 - 5, x + 10, BAR_H // 2 + 5], fill=colour)
        title = "assayer  ·  local fixture issuer"
        draw.text((width // 2 - int(mono.getlength(title)) // 2, BAR_H // 2 - 9),
                  title, font=mono, fill=TITLE_FG)
        return image

    def cell(draw, col: int, row: int, char: str, colour) -> None:
        x, y = PAD_X + col * cell_w, BAR_H + PAD_TOP + row * cell_h
        if ord(char) in mono_cmap:
            draw.text((x, y), char, font=mono, fill=colour)
        else:
            advance = symbol.getlength(char)
            draw.text((x + max(0, (cell_w - advance) / 2), y), char, font=symbol,
                      fill=colour)

    def frame(rows: list[tuple[str, str]], cursor: tuple[int, int] | None):
        image = base()
        draw = ImageDraw.Draw(image)
        for row_index, (kind, text) in enumerate(rows[-ROWS:]):
            col, colour = 0, FG
            if kind == "command":
                cell(draw, 0, row_index, "$", PROMPT)
                col, colour = 2, COMMAND
            for char in text:
                if col >= COLS:
                    break
                if char != " ":
                    cell(draw, col, row_index, char, colour)
                col += 1
        if cursor is not None:
            col, row = cursor
            row -= max(0, len(rows) - ROWS)
            if 0 <= row < ROWS and col < COLS:
                x, y = PAD_X + col * cell_w, BAR_H + PAD_TOP + row * cell_h
                draw.rectangle([x, y + 2, x + cell_w - 1, y + cell_h - 3], fill=FG)
        return image

    frames, durations, rows = [], [], []
    for item in session:
        command = item["command"]
        rows.append(("command", ""))
        for cut in range(0, len(command) + 1, 2):
            rows[-1] = ("command", command[:cut])
            frames.append(frame(rows, (2 + cut, len(rows) - 1)))
            durations.append(TYPE_MS)
        rows[-1] = ("command", command)
        frames.append(frame(rows, (2 + len(command), len(rows) - 1)))
        durations.append(PAUSE_AFTER_CMD)
        for line in item["output"]:
            for piece in wrap(line):
                rows.append(("output", piece))
                frames.append(frame(rows, None))
                durations.append(LINE_MS)
        rows.append(("blank", ""))
        frames.append(frame(rows, (0, len(rows) - 1)))
        durations.append(PAUSE_AFTER_OUT)
    frames.append(frame(rows, (0, len(rows) - 1)))
    durations.append(END_MS)

    palette = frames[0].convert("P", palette=Image.ADAPTIVE, colors=64)
    converted = [f.quantize(palette=palette, dither=Image.NONE) for f in frames]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    converted[0].save(OUT, save_all=True, append_images=converted[1:],
                      duration=durations, loop=0, optimize=True, disposal=1)
    print(f"{OUT}  {len(frames)} frames  {width}x{height}  "
          f"{OUT.stat().st_size / 1024:.0f} KiB  {sum(durations) / 1000:.1f}s")


if __name__ == "__main__":
    render_gif(stage())
