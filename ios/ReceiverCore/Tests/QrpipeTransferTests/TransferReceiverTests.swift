import Foundation
import XCTest
@testable import QrpipeTransfer

final class TransferReceiverTests: XCTestCase {
    private let firstMeta = "QP1:M:0123456789abcdef:0:2:11:aGVsbG8udHh0:dGV4dC9wbGFpbg:b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9:6:5e3235a8346e5a4585f8c58562f5052b8fe26a3bb122e1e96c76784964dfc461:96:1"
    private let secondMeta = "QP1:M:0123456789abcdef:1:2:11:aGVsbG8udHh0:dGV4dC9wbGFpbg:b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9:5:486ea46224d1bb4fb680f34f7c9ad96a8f24ec88be73ea8e5a6c65260e9cb8a7:96:1"

    func testTwoPartsResumeAndAssemble() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let first = try TransferReceiver(storageDirectory: directory)
        try first.accept(firstMeta)
        let part = try first.accept("QP1:D:0123456789abcdef:0:0:ed81f9f6:aGVsbG8g")
        XCTAssertTrue(part.currentPartReady)
        XCTAssertEqual(part.completedParts, 1)

        let resumed = try TransferReceiver(storageDirectory: directory)
        try resumed.accept(secondMeta)
        let result = try resumed.accept("QP1:D:0123456789abcdef:1:0:3a771143:d29ybGQ")
        XCTAssertEqual(result.completedParts, 2)
        XCTAssertEqual(try Data(contentsOf: XCTUnwrap(result.completedFile)), Data("hello world".utf8))
        XCTAssertEqual(result.completedFile?.lastPathComponent, "hello.txt")
    }

    func testRepairRecoversDroppedSystematicBlock() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let receiver = try TransferReceiver(storageDirectory: directory)
        try receiver.accept("QP1:M:0123456789abcdef:0:1:100:eC50eHQ:dGV4dC9wbGFpbg:2816597888e4a0d3a36b82b83316ab32680eb8f00f8cd3b904d681246d285a0e:100:2816597888e4a0d3a36b82b83316ab32680eb8f00f8cd3b904d681246d285a0e:96:2")
        try receiver.accept("QP1:D:0123456789abcdef:0:1:ad98e545:YWFhYQ")
        let result = try receiver.accept("QP1:R:0123456789abcdef:0:0:0,1:8c62da71:AAAAAGFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFh")
        XCTAssertEqual(try Data(contentsOf: XCTUnwrap(result.completedFile)), Data(repeating: 97, count: 100))
    }

    func testRejectsConflictingMetadataAndDamagedFrame() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let receiver = try TransferReceiver(storageDirectory: directory)
        try receiver.accept(firstMeta)
        let damaged = try receiver.accept("QP1:D:0123456789abcdef:0:0:ed81f9f6:aGVsbG8h")
        XCTAssertEqual(damaged.receivedBlocks, 0)
        let conflict = firstMeta.replacingOccurrences(of: "aGVsbG8udHh0", with: "eC50eHQ")
        XCTAssertThrowsError(try receiver.accept(conflict))
    }
}
