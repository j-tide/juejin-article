import Foundation
import Vision
import ImageIO

// macOS helper: image observations only, no network or model credentials.
if CommandLine.arguments.count != 2 { exit(2) }
let url = URL(fileURLWithPath: CommandLine.arguments[1])
guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
      let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
      let width = properties[kCGImagePropertyPixelWidth] as? Int,
      let height = properties[kCGImagePropertyPixelHeight] as? Int,
      width > 0, height > 0, width <= 4000, height <= 4000, width * height <= 4_000_000,
      let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else { exit(3) }
let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.recognitionLanguages = ["zh-Hans", "en-US"]
request.usesLanguageCorrection = false
do {
    try VNImageRequestHandler(cgImage: image, options: [:]).perform([request])
    let lines: [[String: Any]] = (request.results ?? []).compactMap { observation in
        guard let text = observation.topCandidates(1).first else { return nil }
        let box = observation.boundingBox
        return ["text": text.string, "confidence": text.confidence,
                "box": [box.origin.x, box.origin.y, box.width, box.height]]
    }
    let data = try JSONSerialization.data(withJSONObject: ["lines": lines, "width": image.width, "height": image.height])
    print(String(data: data, encoding: .utf8)!)
} catch { fputs("OCR failed\n", stderr); exit(4) }
