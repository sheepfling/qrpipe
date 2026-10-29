import AVFoundation
import SwiftUI

@MainActor
final class ReceiverModel: ObservableObject {
    @Published var snapshot: ReceiveSnapshot
    @Published var message = "Tap Start scanning, then point at the QR stream."
    @Published var scanning = false

    let scanner = CameraScanner()
    private let receiver: TransferReceiver?

    init() {
        let root = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("qrpipe", isDirectory: true)
        receiver = try? TransferReceiver(storageDirectory: root)
        snapshot = receiver?.snapshot ?? ReceiveSnapshot(
            fileName: nil, partIndex: 0, partCount: 0, receivedBlocks: 0,
            blockCount: 0, completedParts: 0, currentPartReady: false, completedFile: nil
        )
        if receiver == nil { message = "Cannot open local storage for received files." }
        scanner.onCode = { [weak self] frame in self?.accept(frame) }
        scanner.onError = { [weak self] error in self?.message = error }
    }

    func start() {
        guard receiver != nil else {
            message = "Cannot open local storage for received files."
            return
        }
        switch AVCaptureDevice.authorizationStatus(for: .video) {
        case .authorized:
            scanner.start()
            scanning = true
        case .notDetermined:
            AVCaptureDevice.requestAccess(for: .video) { [weak self] allowed in
                DispatchQueue.main.async {
                    if allowed { self?.start() }
                    else { self?.message = "Allow camera access in Settings to scan QR frames." }
                }
            }
        default:
            message = "Allow camera access in Settings to scan QR frames."
        }
    }

    func stop() {
        scanner.stop()
        scanning = false
    }

    private func accept(_ frame: String) {
        guard let receiver else { return }
        do {
            let previousParts = snapshot.completedParts
            snapshot = try receiver.accept(frame)
            if let file = snapshot.completedFile {
                message = "Verified \(file.lastPathComponent). Save it to Files or share it."
                stop()
            } else if snapshot.completedParts > previousParts {
                message = "Part \(snapshot.partIndex + 1) saved. Tap Next on the computer."
            } else if snapshot.partCount > 0 {
                message = "Scanning part \(snapshot.partIndex + 1) of \(snapshot.partCount)."
            }
        } catch {
            message = error.localizedDescription
        }
    }
}

struct ContentView: View {
    @StateObject private var model = ReceiverModel()

    var body: some View {
        VStack(spacing: 16) {
            Text("qrpipe Receiver").font(.title.bold())
            Text("Screen to camera · no device connection")
                .font(.subheadline).foregroundStyle(.secondary)
            CameraPreview(session: model.scanner.session)
                .frame(maxWidth: .infinity, maxHeight: 380)
                .clipShape(RoundedRectangle(cornerRadius: 16))
                .accessibilityLabel("Camera preview for animated QR transfer")

            Button(model.scanning ? "Stop scanning" : "Start scanning") {
                if model.scanning { model.stop() } else { model.start() }
            }
            .buttonStyle(.borderedProminent)

            Text(model.message).multilineTextAlignment(.center)
            if let name = model.snapshot.fileName {
                Text(name).font(.headline)
                Text("Parts saved: \(model.snapshot.completedParts) / \(model.snapshot.partCount)")
                if !model.snapshot.currentPartReady {
                    ProgressView(
                        value: Double(model.snapshot.receivedBlocks),
                        total: Double(max(model.snapshot.blockCount, 1))
                    )
                    Text("Frames recovered: \(model.snapshot.receivedBlocks) / \(model.snapshot.blockCount)")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            if let file = model.snapshot.completedFile {
                ShareLink(item: file) {
                    Label("Save or share verified file", systemImage: "square.and.arrow.up")
                }
                .buttonStyle(.borderedProminent)
            }
            Spacer(minLength: 0)
        }
        .padding()
        .onDisappear { model.stop() }
    }
}
