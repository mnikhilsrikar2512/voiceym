// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "VoiceymKit",
    platforms: [.iOS(.v17), .macOS(.v14)],
    products: [.library(name: "VoiceymKit", targets: ["VoiceymKit"])],
    targets: [
        .target(name: "VoiceymKit"),
        .testTarget(name: "VoiceymKitTests", dependencies: ["VoiceymKit"])
    ],
    swiftLanguageModes: [.v6]
)
