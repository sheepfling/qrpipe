from pathlib import Path
import textwrap

repo_root = Path(__file__).resolve().parents[1]
out = repo_root / 'build/artifacts/QR-Pipe-Optical-Quick-Start.pdf'
out.parent.mkdir(parents=True, exist_ok=True)
sections = [
('QR PIPE - OPTICAL TEST QUICK START', [
'Printable field guide | Current receiver limit: 10 MiB',
]),
('BEFORE YOU START', [
'The phone must open the owned receiver at https://sheepfling.github.io/qrpipe/ over HTTPS.',
'That GitHub Pages site is not enabled/published yet. A Pages workflow must build the static',
'receiver and publish receiver/dist with index.html at the artifact root; enable Pages using',
'GitHub Actions and verify the HTTPS URL and assets before testing. Do not use the phone camera',
'until that hosted page is available. Local Mac localhost is not reachable from the phone.',
'After Pages is available: open the URL on the phone while online to cache the static app.',
'Optionally disable Wi-Fi/cellular afterward for the offline-static-app test. File bytes remain',
'in the browser locally; IndexedDB quota/eviction can vary by device. Camera begins only when you',
'tap Start Camera and grant permission.',
]),
('1. GENERATE THE 1 MiB CONTROL FILE', [
'From the repository root:',
'python3 scripts/make_optical_test_file.py --mib 1',
'shasum -a 256 /tmp/qrpipe-1mib.bin',
'Expected SHA-256: ae1e9846fe2bc0e0fdc583e4f1cb1780a0b60d1bbee91d82c74feef50acbbb49',
]),
('2. START THE SENDER ON THE MAC', [
'Set the receiver URL, then run:',
'RECEIVER_URL=https://sheepfling.github.io/qrpipe/',
'QRPIPE=/Users/rick/GIT/sheepfling/QRPIPE/qrpipe/.venv/bin/qrpipe',
'PYTHONPATH="$PWD/src" "$QRPIPE" stream ' + chr(92),
'  --file /tmp/qrpipe-1mib.bin ' + chr(92),
'  --frame-bytes 512 --fps 12 --receiver-url "$RECEIVER_URL"',
'Open the printed http://127.0.0.1:<port> URL on the Mac display. The phone does not connect',
'to that localhost URL. On the phone receiver, tap Start Camera and grant camera permission.',
]),
('3. CALIBRATE AND VERIFY', [
'Keep distance about 20-30 cm, camera square to the screen, brightness fixed, and QR size at',
'the sender default. At 12 fps, repeat fresh 1 MiB transfers with frame-bytes 512, 1024, then',
'1465. Change one setting at a time. If decoding misses frames, enlarge the QR, move closer,',
'reduce frame bytes, or lower FPS. Record duration, unique frames, and KiB/s goodput.',
'PASS: receiver shows 1,048,576 bytes, verified SHA matches above, and offers the file to save.',
'For a saved copy, confirm with: shasum -a 256 /path/to/saved-file',
]),
('4. LOSS / RELOAD RECOVERY', [
'Keep the sender running. Cover the phone camera for 3-5 seconds mid-transfer, then uncover it.',
'PASS: scanning recovers and the completed file hash matches. For reload recovery, refresh the',
'receiver after partial progress, tap Start Camera again, and continue scanning the same sender',
'session. PASS: saved progress is retained and the part completes. Restarting sender changes the',
'incomplete part session; rescan that part. Completed parts remain available.',
]),
('5. OPTIONAL 10 MiB MULTIPART CHECK', [
'Generate and verify:',
'python3 scripts/make_optical_test_file.py --mib 10',
'shasum -a 256 /tmp/qrpipe-10mib.bin',
'Expected SHA-256: cc7186a333a481f387b35cd54cf4625f582d958544f656d43d207b62c66e9d25',
'Start the 5-part stream:',
'PYTHONPATH="$PWD/src" "$QRPIPE" stream ' + chr(92),
'  --file /tmp/qrpipe-10mib.bin --chunk-size 2MiB ' + chr(92),
'  --frame-bytes 1024 --fps 12 --receiver-url "$RECEIVER_URL"',
'Scan manifest, then five parts. Mark scanned / advance only after phone confirms each part.',
'PASS: 10,485,760 bytes, expected SHA verified, and completed file offered to save.',
]),
('CURRENT LIMITS / LOG', [
'Receiver cap is 10 MiB; each part is at most 2 MiB and the receiver allows 16 parts. Final',
'assembly currently uses memory. 50 MiB (25 parts) and 100 MiB (50 parts) exceed the present',
'part-count ceiling and are not supported. QR transport capacity is distinct from these configured',
'limits and from practical camera goodput. Keep the Markdown plan and CSV log canonical:',
'docs/optical-test-plan.md and docs/optical-test-log-template.csv.',
])]

# Build a compact Letter PDF with built-in Helvetica, no external dependencies.
pages=[]
lines=[]
for heading, body in sections:
    if len(lines) > 0: lines.append(('', ''))
    lines.append((heading, 'H'))
    for item in body:
        for part in textwrap.wrap(item, width=112, break_long_words=True, break_on_hyphens=False) or ['']:
            lines.append((part, 'B'))
# Fit the current quick-start on one Letter page with a modest print margin.
for i in range(0, len(lines), 76): pages.append(lines[i:i+76])
objs=[]
# object 1 catalog, 2 pages tree, 3 font; page/content objects appended
objs.append('<< /Type /Catalog /Pages 2 0 R >>')
objs.append('')
objs.append('<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>')
page_ids=[]
for page_no, page in enumerate(pages, 1):
    content=[]
    content.append('BT')
    y=752
    for txt, kind in page:
        safe=txt.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
        if kind == 'H':
            y -= 4
            content.append(f'/F1 11 Tf 1 0 0 1 48 {y:.1f} Tm ({safe}) Tj')
            y -= 14
        else:
            if txt == '': y -= 5; continue
            content.append(f'/F1 9 Tf 1 0 0 1 52 {y:.1f} Tm ({safe}) Tj')
            y -= 10.7
    content += [f'/F1 8 Tf 1 0 0 1 48 28 Tm (QR Pipe optical test | Page {page_no} of {len(pages)}) Tj', 'ET']
    data='\n'.join(content).encode('ascii')
    stream=f'<< /Length {len(data)} >>\nstream\n'.encode()+data+b'\nendstream'
    content_id=len(objs)+1; objs.append(stream)
    page_id=len(objs)+1; page_ids.append(page_id)
    objs.append(f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>')
objs[1]=f'<< /Type /Pages /Kids [{" ".join(f"{i} 0 R" for i in page_ids)}] /Count {len(page_ids)} >>'
pdf=bytearray(b'%PDF-1.4\n%QRPIPE\n')
offsets=[0]
for n,obj in enumerate(objs,1):
    offsets.append(len(pdf))
    if isinstance(obj,bytes): body=obj
    else: body=obj.encode('ascii')
    pdf += f'{n} 0 obj\n'.encode()+body+b'\nendobj\n'
xref=len(pdf)
pdf += f'xref\n0 {len(objs)+1}\n0000000000 65535 f \n'.encode()
for off in offsets[1:]: pdf += f'{off:010d} 00000 n \n'.encode()
pdf += f'trailer\n<< /Size {len(objs)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode()
out.write_bytes(pdf)
print(out, len(pdf), 'bytes', len(pages), 'pages')
