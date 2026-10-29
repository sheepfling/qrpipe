// swift-tools-version: 5.10
import PackageDescription

let package = Package(
    name: "QrpipeTransfer",
    platforms: [.iOS(.v16), .macOS(.v13)],
    products: [.library(name: "QrpipeTransfer", targets: ["QrpipeTransfer"])],
    targets: [
        .target(name: "QrpipeTransfer"),
        .testTarget(name: "QrpipeTransferTests", dependencies: ["QrpipeTransfer"]),
    ]
)
