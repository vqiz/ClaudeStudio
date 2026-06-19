// swift-tools-version:6.0
import PackageDescription

let package = Package(
    name: "ClaudeStudio",
    platforms: [
        .macOS(.v14)
    ],
    products: [
        .executable(name: "ClaudeStudio", targets: ["ClaudeStudio"]),
        .library(name: "ClaudeStudioKit", targets: ["ClaudeStudioKit"])
    ],
    targets: [
        // The macOS SwiftUI front-end (the app the user launches).
        .executableTarget(
            name: "ClaudeStudio",
            dependencies: ["ClaudeStudioKit"],
            swiftSettings: [
                .swiftLanguageMode(.v6)
            ]
        ),
        // Transport / protocol layer shared with the Rust core sidecar.
        .target(
            name: "ClaudeStudioKit",
            swiftSettings: [
                .swiftLanguageMode(.v6)
            ]
        ),
        .testTarget(
            name: "ClaudeStudioKitTests",
            dependencies: ["ClaudeStudioKit"],
            swiftSettings: [
                .swiftLanguageMode(.v6)
            ]
        )
    ]
)
