import CryptoKit
import Foundation

public enum TransferError: Error, LocalizedError {
    case invalidFrame
    case checksumMismatch
    case conflictingTransfer
    case fileMismatch
    case corruptPart(Int)

    public var errorDescription: String? {
        switch self {
        case .invalidFrame: return "Invalid qrpipe frame"
        case .checksumMismatch: return "QR frame or file checksum did not match"
        case .conflictingTransfer: return "This part belongs to a different transfer"
        case .fileMismatch: return "The saved parts do not match the original file"
        case .corruptPart(let index): return "Part \(index) needs to be scanned again"
        }
    }
}

public struct ReceiveSnapshot {
    public let fileName: String?
    public let partIndex: Int
    public let partCount: Int
    public let receivedBlocks: Int
    public let blockCount: Int
    public let completedParts: Int
    public let currentPartReady: Bool
    public let completedFile: URL?
}

private struct Manifest: Codable, Equatable {
    let session: String
    let partIndex: Int
    let partCount: Int
    let totalSize: Int
    let fileName: String
    let mediaType: String
    let totalHash: String
    let partSize: Int
    let partHash: String
    let blockSize: Int
    let blockCount: Int

    static func parse(_ fields: [String]) throws -> Manifest {
        guard fields.count == 13,
              fields[0] == "QP1", fields[1] == "M",
              Self.isHex(fields[2], count: 16),
              let partIndex = Int(fields[3]), let partCount = Int(fields[4]),
              let totalSize = Int(fields[5]),
              let nameData = TransferReceiver.decode64(fields[6]),
              let fileName = String(data: nameData, encoding: .utf8),
              let typeData = TransferReceiver.decode64(fields[7]),
              let mediaType = String(data: typeData, encoding: .utf8),
              Self.isHex(fields[8], count: 64),
              let partSize = Int(fields[9]), Self.isHex(fields[10], count: 64),
              let blockSize = Int(fields[11]), let blockCount = Int(fields[12]),
              (1...64 * 1024 * 1024).contains(totalSize),
              (1...totalSize).contains(partSize),
              (1...min(totalSize, 512)).contains(partCount),
              (partCount != 1 || partSize == totalSize),
              totalSize >= partSize + partCount - 1,
              (0..<partCount).contains(partIndex),
              (1...2118).contains(blockSize),
              blockCount == (partSize + blockSize - 1) / blockSize,
              !fileName.isEmpty, fileName != ".", fileName != "..",
              !fileName.contains("/"), !fileName.contains("\\"),
              !mediaType.isEmpty
        else { throw TransferError.invalidFrame }
        return Manifest(
            session: fields[2], partIndex: partIndex, partCount: partCount,
            totalSize: totalSize, fileName: fileName, mediaType: mediaType,
            totalHash: fields[8], partSize: partSize, partHash: fields[10],
            blockSize: blockSize, blockCount: blockCount
        )
    }

    static func isHex(_ value: String, count: Int) -> Bool {
        value.utf8.count == count && value.utf8.allSatisfy { byte in
            (48...57).contains(byte) || (97...102).contains(byte)
        }
    }
}

private struct SavedTransfer: Codable {
    let session: String
    let partCount: Int
    let totalSize: Int
    let fileName: String
    let mediaType: String
    let totalHash: String
    var partHashes: [Int: String]

    init(_ manifest: Manifest) {
        session = manifest.session
        partCount = manifest.partCount
        totalSize = manifest.totalSize
        fileName = manifest.fileName
        mediaType = manifest.mediaType
        totalHash = manifest.totalHash
        partHashes = [:]
    }

    func matches(_ manifest: Manifest) -> Bool {
        session == manifest.session && partCount == manifest.partCount
            && totalSize == manifest.totalSize && fileName == manifest.fileName
            && mediaType == manifest.mediaType && totalHash == manifest.totalHash
    }
}

private struct Equation {
    var indices: Set<Int>
    var bytes: Data
}

public final class TransferReceiver {
    private let root: URL
    private var manifest: Manifest?
    private var saved: SavedTransfer?
    private var blocks: [Int: Data] = [:]
    private var equations: [Int: Equation] = [:]
    private var completedFile: URL?

    public init(storageDirectory: URL) throws {
        root = storageDirectory
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    }

    public var snapshot: ReceiveSnapshot {
        ReceiveSnapshot(
            fileName: manifest?.fileName,
            partIndex: manifest?.partIndex ?? 0,
            partCount: manifest?.partCount ?? 0,
            receivedBlocks: blocks.count,
            blockCount: manifest?.blockCount ?? 0,
            completedParts: saved?.partHashes.count ?? 0,
            currentPartReady: saved?.partHashes[manifest?.partIndex ?? -1] != nil,
            completedFile: completedFile
        )
    }

    @discardableResult
    public func accept(_ frame: String) throws -> ReceiveSnapshot {
        guard frame.hasPrefix("QP1:") else { return snapshot }
        let fields = frame.components(separatedBy: ":")
        guard fields.count >= 2 else { throw TransferError.invalidFrame }
        if fields[1] == "M" {
            try begin(Manifest.parse(fields))
        } else if fields[1] == "D" || fields[1] == "R" {
            try ingest(fields)
        } else {
            throw TransferError.invalidFrame
        }
        return snapshot
    }

    private func begin(_ next: Manifest) throws {
        let directory = folder(for: next)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let stateFile = directory.appendingPathComponent("state.json")
        let state: SavedTransfer
        if FileManager.default.fileExists(atPath: stateFile.path) {
            state = try JSONDecoder().decode(SavedTransfer.self, from: Data(contentsOf: stateFile))
            guard state.matches(next) else { throw TransferError.conflictingTransfer }
        } else {
            state = SavedTransfer(next)
            try persist(state, in: directory)
        }
        if manifest != next {
            blocks = [:]
            equations = [:]
        }
        manifest = next
        saved = state
        let finished = directory.appendingPathComponent("finished", isDirectory: true)
            .appendingPathComponent(next.fileName)
        completedFile = FileManager.default.fileExists(atPath: finished.path) ? finished : nil
        if completedFile == nil && state.partHashes.count == next.partCount {
            try completeFile(next)
        }
    }

    private func ingest(_ fields: [String]) throws {
        guard let manifest, let saved,
              fields.count == (fields[1] == "D" ? 7 : 8),
              fields[2] == manifest.session,
              Int(fields[3]) == manifest.partIndex,
              saved.partHashes[manifest.partIndex] == nil,
              let bytes = Self.decode64(fields.last ?? ""),
              !bytes.isEmpty, bytes.count <= manifest.blockSize,
              let declaredCRC = UInt32(fields[fields.count - 2], radix: 16),
              Self.crc32(bytes) == declaredCRC
        else { return }

        if fields[1] == "D" {
            guard let index = Int(fields[4]), (0..<manifest.blockCount).contains(index),
                  bytes.count == expectedLength(index, manifest) else {
                throw TransferError.invalidFrame
            }
            if blocks[index] == nil { blocks[index] = bytes; peel() }
        } else {
            guard let sequence = Int(fields[4]), sequence >= 0,
                  equations[sequence] == nil,
                  let indices = parseIndices(fields[5], count: manifest.blockCount) else {
                throw TransferError.invalidFrame
            }
            equations[sequence] = Equation(indices: indices, bytes: bytes)
            peel()
        }
        if blocks.count == manifest.blockCount { try completePart(manifest) }
    }

    private func parseIndices(_ field: String, count: Int) -> Set<Int>? {
        let indices = field.split(separator: ",").compactMap { Int($0) }
        guard (2...3).contains(indices.count), Set(indices).count == indices.count,
              indices.allSatisfy({ (0..<count).contains($0) }) else { return nil }
        return Set(indices)
    }

    private func peel() {
        guard let manifest else { return }
        var changed = true
        while changed {
            changed = false
            for (sequence, original) in equations {
                var equation = original
                for index in Array(equation.indices) where blocks[index] != nil {
                    Self.xor(into: &equation.bytes, with: blocks[index]!)
                    equation.indices.remove(index)
                }
                if equation.indices.count == 1, let index = equation.indices.first {
                    let length = expectedLength(index, manifest)
                    if blocks[index] == nil && equation.bytes.count >= length {
                        blocks[index] = equation.bytes.prefix(length)
                        changed = true
                    }
                    equations.removeValue(forKey: sequence)
                } else if equation.indices.isEmpty {
                    equations.removeValue(forKey: sequence)
                } else {
                    equations[sequence] = equation
                }
            }
        }
    }

    private func expectedLength(_ index: Int, _ manifest: Manifest) -> Int {
        min(manifest.blockSize, manifest.partSize - index * manifest.blockSize)
    }

    private func completePart(_ manifest: Manifest) throws {
        var data = Data(capacity: manifest.partSize)
        for index in 0..<manifest.blockCount {
            guard let block = blocks[index] else { return }
            data.append(block)
        }
        guard data.count == manifest.partSize, Self.sha256(data) == manifest.partHash else {
            blocks = [:]
            equations = [:]
            throw TransferError.checksumMismatch
        }
        let directory = folder(for: manifest)
        try data.write(to: partURL(manifest.partIndex, in: directory), options: .atomic)
        saved?.partHashes[manifest.partIndex] = manifest.partHash
        if let saved { try persist(saved, in: directory) }
        blocks = [:]
        equations = [:]
        if saved?.partHashes.count == manifest.partCount { try completeFile(manifest) }
    }

    private func completeFile(_ manifest: Manifest) throws {
        let directory = folder(for: manifest)
        var file = Data(capacity: manifest.totalSize)
        for index in 0..<manifest.partCount {
            guard let expectedHash = saved?.partHashes[index] else { return }
            let part = try? Data(contentsOf: partURL(index, in: directory))
            guard let part, Self.sha256(part) == expectedHash else {
                saved?.partHashes.removeValue(forKey: index)
                if let saved { try persist(saved, in: directory) }
                throw TransferError.corruptPart(index + 1)
            }
            file.append(part)
        }
        guard file.count == manifest.totalSize,
              Self.sha256(file) == manifest.totalHash else { throw TransferError.fileMismatch }
        let finished = directory.appendingPathComponent("finished", isDirectory: true)
        try FileManager.default.createDirectory(at: finished, withIntermediateDirectories: true)
        let output = finished.appendingPathComponent(manifest.fileName)
        try file.write(to: output, options: .atomic)
        completedFile = output
    }

    private func persist(_ state: SavedTransfer, in directory: URL) throws {
        try JSONEncoder().encode(state).write(
            to: directory.appendingPathComponent("state.json"), options: .atomic
        )
    }

    private func folder(for manifest: Manifest) -> URL {
        root.appendingPathComponent("\(manifest.session)-\(manifest.totalHash.prefix(12))", isDirectory: true)
    }

    private func partURL(_ index: Int, in directory: URL) -> URL {
        directory.appendingPathComponent(String(format: "part-%05d.bin", index))
    }

    private static func sha256(_ data: Data) -> String {
        SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }

    static func decode64(_ value: String) -> Data? {
        let standard = value.replacingOccurrences(of: "-", with: "+")
            .replacingOccurrences(of: "_", with: "/")
        return Data(base64Encoded: standard + String(repeating: "=", count: (4 - standard.count % 4) % 4))
    }

    private static func crc32(_ data: Data) -> UInt32 {
        var crc: UInt32 = 0xffff_ffff
        for byte in data {
            crc ^= UInt32(byte)
            for _ in 0..<8 {
                crc = (crc >> 1) ^ (crc & 1 == 1 ? 0xedb8_8320 : 0)
            }
        }
        return crc ^ 0xffff_ffff
    }

    private static func xor(into target: inout Data, with source: Data) {
        let length = min(target.count, source.count)
        target.withUnsafeMutableBytes { targetBytes in
            source.withUnsafeBytes { sourceBytes in
                guard let dst = targetBytes.baseAddress?.assumingMemoryBound(to: UInt8.self),
                      let src = sourceBytes.baseAddress?.assumingMemoryBound(to: UInt8.self) else { return }
                for offset in 0..<length { dst[offset] ^= src[offset] }
            }
        }
    }
}
