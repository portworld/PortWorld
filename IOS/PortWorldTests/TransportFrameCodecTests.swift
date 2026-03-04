import XCTest
@testable import PortWorld

final class TransportFrameCodecTests: XCTestCase {

  func testEncodeDecodeRoundtripForClientAndServerFrames() throws {
    let cases: [TransportBinaryFrame] = [
      TransportBinaryFrame(
        frameType: .clientAudio,
        timestampMs: 1_705_000_001_234,
        payload: Data([0x01, 0x02, 0x03])
      ),
      TransportBinaryFrame(
        frameType: .serverAudio,
        timestampMs: 1_705_000_009_999,
        payload: Data([0xAA, 0xBB, 0xCC, 0xDD])
      ),
    ]

    for frame in cases {
      let encoded = TransportBinaryFrameCodec.encode(frame)
      let decoded = try TransportBinaryFrameCodec.decode(encoded)
      XCTAssertEqual(decoded, frame)
    }
  }

  func testDecodeShortFrameThrows() {
    let short = Data(repeating: 0x00, count: TransportBinaryFraming.headerSize - 1)

    XCTAssertThrowsError(try TransportBinaryFrameCodec.decode(short)) { error in
      guard case TransportBinaryFrameCodec.DecodeError.frameTooShort(let expectedMinimum, let actual) = error else {
        XCTFail("Expected frameTooShort, got \(error)")
        return
      }
      XCTAssertEqual(expectedMinimum, TransportBinaryFraming.headerSize)
      XCTAssertEqual(actual, short.count)
    }
  }

  func testDecodeUnsupportedFrameTypeThrows() {
    var data = Data([0xFF])
    data.append(Data(repeating: 0x00, count: TransportBinaryFraming.headerSize - 1))

    XCTAssertThrowsError(try TransportBinaryFrameCodec.decode(data)) { error in
      guard case TransportBinaryFrameCodec.DecodeError.unsupportedFrameType(let rawType) = error else {
        XCTFail("Expected unsupportedFrameType, got \(error)")
        return
      }
      XCTAssertEqual(rawType, 0xFF)
    }
  }

  func testDecodeHandlesSlicedDataWithNonZeroStartIndex() throws {
    let frame = TransportBinaryFrame(
      frameType: .serverAudio,
      timestampMs: 987_654_321,
      payload: Data([0xDE, 0xAD, 0xBE, 0xEF])
    )
    let encoded = TransportBinaryFrameCodec.encode(frame)

    var wrapped = Data([0xAA, 0xBB, 0xCC])
    wrapped.append(encoded)
    wrapped.append(0xDD)

    let start = wrapped.index(wrapped.startIndex, offsetBy: 3)
    let end = wrapped.index(start, offsetBy: encoded.count)
    let sliced = wrapped[start..<end]

    let decoded = try TransportBinaryFrameCodec.decode(sliced)
    XCTAssertEqual(decoded, frame)
  }
}
