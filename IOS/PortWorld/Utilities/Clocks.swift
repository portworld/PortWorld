// Shared clock helpers for producing runtime timestamps.
import Foundation

enum Clocks {
  nonisolated static func nowMs() -> Int64 {
    // Int64 conversion intentionally truncates fractional milliseconds.
    Int64(Date().timeIntervalSince1970 * 1000)
  }
}
