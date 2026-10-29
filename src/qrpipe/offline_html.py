"""Save a self-contained QP1 sender page with QR modules baked into the file."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Final, Protocol

from segno import make_qr

from qrpipe.owned import METADATA_INTERVAL, OwnedEncoder
from qrpipe.stream import StreamError

MAX_HTML_IMAGES: Final = 32_768


class _Writer(Protocol):
    def write(self, value: str, /) -> int: ...


_PAGE_START = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy"
content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline';
base-uri 'none'; form-action 'none'">
<title>qrpipe offline sender</title>
<style>
body{margin:0;background:#111;color:#eee;font:16px system-ui;
min-height:100vh;display:grid;place-items:center}
main{display:grid;gap:1rem;justify-items:center;width:min(94vw,1200px);padding:1rem;box-sizing:border-box}
h1{font-size:1.4rem;margin:0}p{margin:0;text-align:center}
canvas{width:min(90vmin,1100px);height:min(90vmin,1100px);background:white;image-rendering:pixelated}
#parts{display:grid;gap:.35rem;width:100%;max-height:10rem;overflow:auto}
#parts label{display:flex;align-items:center;gap:.5rem;padding:.3rem .5rem;border-radius:.35rem}
#parts label.current{background:#333}#parts label.done{color:#8f8}
button{font:inherit;padding:.5rem 1rem;cursor:pointer}button:disabled{cursor:default;opacity:.45}
#controls{display:flex;gap:.5rem;flex-wrap:wrap;justify-content:center}
</style></head><body><main>
<h1>qrpipe offline sender</h1><p id="progress"></p><div id="parts"></div>
<p>Open qrpipe Receiver on the phone and scan the QR. After it saves a part, press Next.</p>
<canvas id="qr" role="img" aria-label="Animated QR transfer"></canvas>
<div id="controls"><button id="next">Part saved on phone → Next</button>
<button id="undo">Undo last</button><button id="pause">Pause</button>
<button id="reset">Reset checklist</button><button id="fullscreen">Fullscreen</button></div>
<p>Everything needed to display this transfer is inside this file.
Keep it private if it contains private data.</p>
</main><script>
const parts="""

_PAGE_END = """;
const canvas=document.querySelector('#qr'),ctx=canvas.getContext('2d');
const list=document.querySelector('#parts'),progress=document.querySelector('#progress');
const nextButton=document.querySelector('#next'),undoButton=document.querySelector('#undo');
const pauseButton=document.querySelector('#pause');
const completed=new Set(),history=[];let current=0,sequence=0,running=true,timer;
const delay=__DELAY__;
function paint(matrix){
  const [size,encoded]=matrix,bytes=atob(encoded),scale=4,border=4;
  canvas.width=canvas.height=(size+border*2)*scale;
  ctx.fillStyle='#fff';ctx.fillRect(0,0,canvas.width,canvas.height);
  ctx.fillStyle='#000';
  for(let y=0;y<size;y++)for(let x=0;x<size;x++){
    const index=y*size+x;
    if(bytes.charCodeAt(index>>3)&(128>>(index&7)))
      ctx.fillRect((x+border)*scale,(y+border)*scale,scale,scale);
  }
}
function tick(){
  const part=parts[current],position=sequence%33;
  const index=((Math.floor(sequence/33)*32+position-1)%part.frames.length);
  paint(position===0?part.meta:part.frames[index]);sequence++;
  if(running)timer=setTimeout(tick,delay);
}
function show(index){clearTimeout(timer);current=index;sequence=0;update();tick()}
for(const [index,part] of parts.entries()){
  const label=document.createElement('label'),input=document.createElement('input');
  const button=document.createElement('button');
  input.type='checkbox';input.onchange=()=>{
    if(input.checked)completed.add(index);else completed.delete(index);
    show(index);
  };
  button.textContent='Show';button.onclick=()=>show(index);
  label.append(input,button,document.createTextNode(part.name));list.append(label);
}
function update(){
  for(const [index,label] of [...list.children].entries()){
    label.classList.toggle('current',index===current);
    label.classList.toggle('done',completed.has(index));
    label.querySelector('input').checked=completed.has(index);
  }
  progress.textContent=completed.size+'/'+parts.length+' parts marked saved — showing '
    +parts[current].name;
  nextButton.disabled=completed.size===parts.length;
  undoButton.disabled=history.length===0;
  pauseButton.textContent=running?'Pause':'Resume';
}
nextButton.onclick=()=>{
  if(!completed.has(current)){completed.add(current);history.push(current)}
  const next=[...parts.keys()].find(index=>!completed.has(index));
  if(next!==undefined)show(next);else update();
};
undoButton.onclick=()=>{const previous=history.pop();
  if(previous!==undefined){completed.delete(previous);show(previous)}
};
pauseButton.onclick=()=>{running=!running;clearTimeout(timer);update();if(running)tick()};
document.querySelector('#reset').onclick=()=>{
  completed.clear();history.length=0;show(0);
};
document.querySelector('#fullscreen').onclick=()=>document.documentElement.requestFullscreen?.();
update();tick();
</script></body></html>
"""


def _matrix(frame: str, error: str) -> tuple[int, str]:
    """Pack black modules row-first, high bit first, for canvas playback."""
    try:
        qr = make_qr(frame, error=error, boost_error=False)
    except (TypeError, ValueError) as exc:
        msg = "QR frames are too dense for offline HTML at this error level"
        raise StreamError(msg) from exc
    size = len(qr.matrix)
    packed = bytearray((size * size + 7) // 8)
    for y, row in enumerate(qr.matrix):
        for x, bit in enumerate(row):
            if bit:
                index = y * size + x
                packed[index >> 3] |= 128 >> (index & 7)
    return size, base64.b64encode(packed).decode("ascii")


def _write_part(output: _Writer, encoder: OwnedEncoder, name: str, error: str) -> None:
    """Write one manifest and its one-cycle direct and repair images."""
    output.write('{"name":')
    output.write(
        json.dumps(name, ensure_ascii=True)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
    output.write(',"meta":')
    json.dump(_matrix(encoder.metadata_frame, error), output)
    output.write(',"frames":[')
    for index in range(encoder.block_count * 2):
        if index:
            output.write(",")
        sequence = index + 1 + index // METADATA_INTERVAL
        json.dump(_matrix(encoder.frame(sequence), error), output)
    output.write("]}")


def save_html(
    encoders: tuple[OwnedEncoder, ...],
    path: Path,
    *,
    names: tuple[str, ...],
    fps: int,
    error: str,
) -> int:
    """Atomically save an offline, network-free multipart QP1 player."""
    if path.suffix.casefold() not in {".html", ".htm"}:
        msg = "offline webpage path must end in .html or .htm"
        raise StreamError(msg)
    if not encoders or len(names) != len(encoders):
        msg = "offline webpage needs one name per part"
        raise StreamError(msg)
    image_count = sum(1 + encoder.block_count * 2 for encoder in encoders)
    if image_count > MAX_HTML_IMAGES:
        msg = (
            f"offline webpage needs {image_count} QR images; limit is {MAX_HTML_IMAGES}; "
            "use the live sender"
        )
        raise StreamError(msg)
    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".html",
            delete=False,
        ) as output:
            temporary_path = Path(output.name)
            output.write(_PAGE_START)
            output.write("[")
            for index, (encoder, name) in enumerate(zip(encoders, names, strict=True)):
                if index:
                    output.write(",")
                _write_part(output.file, encoder, name, error)
            output.write("]")
            output.write(_PAGE_END.replace("__DELAY__", str(round(1000 / fps))))
        temporary_path.replace(path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return image_count
