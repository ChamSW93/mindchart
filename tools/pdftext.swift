// Prints the text of a PDF page by page, using macOS's built-in PDFKit.
// Pages with no text layer (scans, faxes) are read with the built-in Vision OCR.
// Everything runs on this computer; nothing is sent anywhere.
// Usage: pdftext <file.pdf>
import AppKit
import Foundation
import PDFKit
import Vision

func ocr(_ page: PDFPage) -> String {
    let bounds = page.bounds(for: .mediaBox)
    let scale: CGFloat = 2.5  // about 180 dpi, enough for typed and faxed documents
    let image = page.thumbnail(of: NSSize(width: bounds.width * scale, height: bounds.height * scale), for: .mediaBox)
    guard let cgImage = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else { return "" }
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = true
    try? VNImageRequestHandler(cgImage: cgImage).perform([request])
    return (request.results ?? []).compactMap { $0.topCandidates(1).first?.string }.joined(separator: "\n")
}

guard CommandLine.arguments.count == 2,
      let document = PDFDocument(url: URL(fileURLWithPath: CommandLine.arguments[1])) else {
    FileHandle.standardError.write("Could not open PDF\n".data(using: .utf8)!)
    exit(1)
}

for index in 0..<document.pageCount {
    guard let page = document.page(at: index) else { continue }
    var text = page.string ?? ""
    var scanned = false
    if text.trimmingCharacters(in: .whitespacesAndNewlines).count < 20 {
        text = ocr(page)
        scanned = true
    }
    print("=== Page \(index + 1)\(scanned ? " (scanned)" : "") ===")
    print(text)
}
