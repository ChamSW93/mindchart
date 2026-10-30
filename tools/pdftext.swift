// Prints the text of a PDF or an image (photo, scan, fax) page by page, using macOS's built-in PDFKit and Vision.
// Pages with little or no text layer (scans, faxes, photos) are read with the built-in Vision OCR, which also
// tries sideways and upside-down orientations. Pages the OCR is unsure about are marked "unclear".
// Everything runs on this computer; nothing is sent anywhere.
// Usage: pdftext <file.pdf | image file>
import AppKit
import Foundation
import ImageIO
import PDFKit
import Vision

struct Reading {
    var text: String
    var confidence: Float  // average over the lines read, 0 to 1
    var doubtful: Float  // share of lines the OCR wasn't fully sure of
    var score: Float { Float(text.count) * confidence }
}

func recognize(_ image: CGImage, _ orientation: CGImagePropertyOrientation) -> Reading {
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = true
    try? VNImageRequestHandler(cgImage: image, orientation: orientation).perform([request])
    let lines = (request.results ?? []).compactMap { $0.topCandidates(1).first }
    let confidence = lines.isEmpty ? 0 : lines.map { $0.confidence }.reduce(0, +) / Float(lines.count)
    let doubtful = lines.isEmpty ? 1 : Float(lines.filter { $0.confidence < 1 }.count) / Float(lines.count)
    return Reading(text: lines.map { $0.string }.joined(separator: "\n"), confidence: confidence, doubtful: doubtful)
}

// Read upright first; if that finds little, or reads poorly, try the page turned each other way and keep the best.
func ocr(_ image: CGImage) -> Reading {
    var best = recognize(image, .up)
    if best.text.count < 80 || best.confidence < 0.6 {
        for orientation in [CGImagePropertyOrientation.right, .left, .down] {
            let turned = recognize(image, orientation)
            if turned.score > best.score * 1.3 { best = turned }
        }
    }
    return best
}

func printPage(_ number: Int, _ text: String, scanned: Bool, unclear: Bool) {
    let note = scanned ? (unclear ? " (scanned, unclear)" : " (scanned)") : ""
    print("=== Page \(number)\(note) ===")
    print(text)
}

// Many doubtful lines, or very little text from a page that should have some: probably handwriting or a poor copy.
// (Vision's confidence comes in coarse steps, so the share of doubtful lines says more than the average.)
func isUnclear(_ reading: Reading) -> Bool {
    !reading.text.isEmpty && (reading.confidence < 0.75 || reading.doubtful >= 0.25 || reading.text.count < 40)
}

let path = CommandLine.arguments.count == 2 ? CommandLine.arguments[1] : ""
let url = URL(fileURLWithPath: path)

if url.pathExtension.lowercased() == "pdf" {
    guard let document = PDFDocument(url: url) else {
        FileHandle.standardError.write("Could not open PDF\n".data(using: .utf8)!)
        exit(1)
    }
    for index in 0..<document.pageCount {
        guard let page = document.page(at: index) else { continue }
        var text = page.string ?? ""
        var scanned = false, unclear = false
        // A page with only a little text may be a scan with a stamp on it (a fax header, "CONFIDENTIAL",
        // a page number), so read the picture too, and keep the OCR if it finds clearly more.
        if text.trimmingCharacters(in: .whitespacesAndNewlines).count < 200 {
            let bounds = page.bounds(for: .mediaBox)
            let scale: CGFloat = 3  // about 216 dpi, enough for small print, faxes and neat handwriting
            let image = page.thumbnail(of: NSSize(width: bounds.width * scale, height: bounds.height * scale), for: .mediaBox)
            if let cgImage = image.cgImage(forProposedRect: nil, context: nil, hints: nil) {
                let reading = ocr(cgImage)
                if reading.text.count > text.trimmingCharacters(in: .whitespacesAndNewlines).count + 40 {
                    text = reading.text
                    scanned = true
                    unclear = isUnclear(reading)
                }
            }
        }
        printPage(index + 1, text, scanned: scanned, unclear: unclear)
    }
} else {
    // An image file: each frame is a page (fax TIFFs often hold several).
    guard let source = CGImageSourceCreateWithURL(url as CFURL, nil), CGImageSourceGetCount(source) > 0 else {
        FileHandle.standardError.write("Could not open image\n".data(using: .utf8)!)
        exit(1)
    }
    for index in 0..<CGImageSourceGetCount(source) {
        guard let image = CGImageSourceCreateImageAtIndex(source, index, nil) else { continue }
        // Photos from a phone record which way up they were taken; start from that.
        let properties = CGImageSourceCopyPropertiesAtIndex(source, index, nil) as? [CFString: Any]
        let stored = (properties?[kCGImagePropertyOrientation] as? UInt32).flatMap(CGImagePropertyOrientation.init) ?? .up
        var reading = recognize(image, stored)
        if reading.text.count < 80 || reading.confidence < 0.6 {
            let turned = ocr(image)
            if turned.score > reading.score * 1.3 { reading = turned }
        }
        printPage(index + 1, reading.text, scanned: true, unclear: isUnclear(reading) || reading.text.isEmpty)
    }
}
