# qrpipe optical transfer v1

This format is specific to `--protocol qrpipe`. Every QR carries UTF-8 text beginning `QP1:`.
Integers are unsigned decimal. Hashes are lowercase hexadecimal. Binary fields use unpadded
base64url. A session ID is 16 lowercase hex digits, newly generated for each transfer. Part and
block indexes are zero-based.

Metadata, repeated every 32 data frames:

```text
QP1:M:session:partIndex:partCount:totalBytes:name64:mime64:totalSHA256:partBytes:partSHA256:blockBytes:blockCount
```

Direct source block:

```text
QP1:D:session:partIndex:blockIndex:crc32:payload64
```

XOR repair frame:

```text
QP1:R:session:partIndex:sequence:index,index[,index]:crc32:payload64
```

The CRC-32 is the standard IEEE CRC over decoded payload bytes, rendered as eight hex digits.
Repair bytes XOR the listed source blocks, padding a short final block with zeros. A receiver can
recover one missing block from a repair packet once all other listed blocks are known. Every part
transmits all direct blocks, then one repair packet per source block, and cycles until the user
advances. Metadata is interleaved throughout so scanning can start midstream. The indexes in a
repair frame are explicit; receivers do not need to reproduce the sender's random generator.

The receiver checks CRC before accepting a frame, SHA-256 before saving a part, and total SHA-256
before offering the assembled file. A bad or unrelated frame cannot silently become file data.
Verified parts are stored under a transfer-specific session and total hash. A receiver should
reject path separators in filenames and limit allocations to its advertised transfer policy.

Current implementation limits: 64 MiB total, at most 512 parts, at most 2 MiB per part,
256 KiB default part,
256–2953 text characters per QR frame, 32-frame metadata interval. No encryption or reverse
optical acknowledgment is defined. The computer's **Next** button is manual.
