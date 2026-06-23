import CoreGraphics
import Foundation
// Druckt Zeilen "windowId<TAB>width<TAB>height<TAB>ownerName" fuer alle On-Screen-Fenster
// eines Owners, dessen Name das Argument enthaelt. Nutzt CGWindowList (keine AX-Rechte noetig).
let needle = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "ClaudeStudio"
let opts: CGWindowListOption = [.optionOnScreenOnly, .excludeDesktopElements]
guard let infos = CGWindowListCopyWindowInfo(opts, kCGNullWindowID) as? [[String: Any]] else { exit(2) }
for w in infos {
    guard let owner = w[kCGWindowOwnerName as String] as? String, owner.contains(needle) else { continue }
    let num = (w[kCGWindowNumber as String] as? Int) ?? -1
    let b = w[kCGWindowBounds as String] as? [String: Any] ?? [:]
    let wd = (b["Width"] as? Double) ?? 0, ht = (b["Height"] as? Double) ?? 0
    let layer = (w[kCGWindowLayer as String] as? Int) ?? 0
    print("\(num)\t\(Int(wd))\t\(Int(ht))\t\(layer)\t\(owner)")
}
