import Foundation
import XCTest
@testable import PortWorld

final class WavFileWriterTests: XCTestCase {
  private var tempFiles: [URL] = []

  override func tearDown() {
    tempFiles.forEach { try? FileManager.default.removeItem(at: $0) }
    tempFiles.removeAll()
    super.tearDown()
  }

  func testWritePCM16ProducesCanonicalRIFFHeaderAndPayload() throws {
    let samples = Data([0x34, 0x12, 0x78, 0x56])
    let outputURL = makeTempFileURL(suffix: "mono.wav")

    let writtenSize = try WavFileWriter.writePCM16(
      samples: samples,
      sampleRate: 16_000,
      channels: 1,
      to: outputURL
    )
    let wav = try Data(contentsOf: outputURL)

    XCTAssertEqual(Int(writtenSize), wav.count)
    XCTAssertEqual(wav.count, 44 + samples.count)
    XCTAssertEqual(wav[0..<4], Data("RIFF".utf8))
    XCTAssertEqual(wav[8..<12], Data("WAVE".utf8))
    XCTAssertEqual(wav[12..<16], Data("fmt ".utf8))
    XCTAssertEqual(wav[36..<40], Data("data".utf8))

    XCTAssertEqual(readUInt32LE(wav, at: 4), UInt32(wav.count - 8))
    XCTAssertEqual(readUInt32LE(wav, at: 16), 16)
    XCTAssertEqual(readUInt16LE(wav, at: 20), 1)
    XCTAssertEqual(readUInt16LE(wav, at: 22), 1)
    XCTAssertEqual(readUInt32LE(wav, at: 24), 16_000)
    XCTAssertEqual(readUInt32LE(wav, at: 28), 32_000)
    XCTAssertEqual(readUInt16LE(wav, at: 32), 2)
    XCTAssertEqual(readUInt16LE(wav, at: 34), 16)
    XCTAssertEqual(readUInt32LE(wav, at: 40), UInt32(samples.count))
    XCTAssertEqual(wav[44..<wav.count], samples)

    XCTAssertEqual(Array(wav[4..<8]), [0x28, 0x00, 0x00, 0x00])
    XCTAssertEqual(Array(wav[24..<28]), [0x80, 0x3E, 0x00, 0x00])
    XCTAssertEqual(Array(wav[28..<32]), [0x00, 0x7D, 0x00, 0x00])
  }

  func testWritePCM16EncodesStereoSizesAndRatesInLittleEndian() throws {
    let samples = Data([0x00, 0x80, 0xFF, 0x7F, 0x34, 0x12, 0x78, 0x56])
    let outputURL = makeTempFileURL(suffix: "stereo.wav")

    _ = try WavFileWriter.writePCM16(
      samples: samples,
      sampleRate: 44_100,
      channels: 2,
      to: outputURL
    )
    let wav = try Data(contentsOf: outputURL)

    XCTAssertEqual(readUInt32LE(wav, at: 4), UInt32(wav.count - 8))
    XCTAssertEqual(readUInt16LE(wav, at: 22), 2)
    XCTAssertEqual(readUInt32LE(wav, at: 24), 44_100)
    XCTAssertEqual(readUInt16LE(wav, at: 32), 4)
    XCTAssertEqual(readUInt32LE(wav, at: 28), 176_400)
    XCTAssertEqual(readUInt32LE(wav, at: 40), UInt32(samples.count))
    XCTAssertEqual(Array(wav[24..<28]), [0x44, 0xAC, 0x00, 0x00])
    XCTAssertEqual(Array(wav[28..<32]), [0x10, 0xB1, 0x02, 0x00])
  }

  private func makeTempFileURL(suffix: String) -> URL {
    let url = FileManager.default.temporaryDirectory
      .appendingPathComponent("WavFileWriterTests-\(UUID().uuidString)-\(suffix)")
    tempFiles.append(url)
    return url
  }

  private func readUInt16LE(_ data: Data, at offset: Int) -> UInt16 {
    XCTAssertGreaterThanOrEqual(data.count, offset + 2)
    return UInt16(data[offset]) | (UInt16(data[offset + 1]) << 8)
  }

  private func readUInt32LE(_ data: Data, at offset: Int) -> UInt32 {
    XCTAssertGreaterThanOrEqual(data.count, offset + 4)
    return UInt32(data[offset])
      | (UInt32(data[offset + 1]) << 8)
      | (UInt32(data[offset + 2]) << 16)
      | (UInt32(data[offset + 3]) << 24)
  }
}
