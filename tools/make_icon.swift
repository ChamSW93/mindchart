// Draws MindChart's app icon as a 1024x1024 PNG, using macOS's built-in drawing tools.
// Mid-century modern: cream tile, teal circle with a heartbeat line, and a tilted mustard orbit.
// Usage: make_icon <output.png>
import AppKit

let size: CGFloat = 1024
let image = NSImage(size: NSSize(width: size, height: size))
image.lockFocus()
let context = NSGraphicsContext.current!.cgContext

func color(_ hex: UInt32, _ alpha: CGFloat = 1) -> NSColor {
    NSColor(srgbRed: CGFloat((hex >> 16) & 0xff) / 255, green: CGFloat((hex >> 8) & 0xff) / 255,
            blue: CGFloat(hex & 0xff) / 255, alpha: alpha)
}

// The tile: macOS app icons sit on a rounded square inset from the edges, with a soft shadow.
let tile = NSRect(x: 100, y: 100, width: 824, height: 824)
let tilePath = NSBezierPath(roundedRect: tile, xRadius: 186, yRadius: 186)
NSGraphicsContext.saveGraphicsState()
let shadow = NSShadow()
shadow.shadowColor = color(0x3a2a20, 0.22)
shadow.shadowOffset = NSSize(width: 0, height: -8)
shadow.shadowBlurRadius = 22
shadow.set()
color(0xf4ecdf).setFill()
tilePath.fill()
NSGraphicsContext.restoreGraphicsState()
NSGradient(starting: color(0xfaf4ea), ending: color(0xeadcc4))!.draw(in: tilePath, angle: -90)

// A walnut band along the bottom, like a credenza.
NSGraphicsContext.saveGraphicsState()
tilePath.addClip()
color(0x3a2a20).setFill()
NSRect(x: tile.minX, y: tile.minY, width: tile.width, height: 92).fill()
color(0xd6a02f).setFill()
NSRect(x: tile.minX, y: tile.minY + 92, width: tile.width, height: 12).fill()
NSGraphicsContext.restoreGraphicsState()

let center = NSPoint(x: 512, y: 548)

// The orbit: a tilted mustard ellipse passing behind the circle, with a small "electron" in front.
func orbit(ring drawRing: Bool) {
    NSGraphicsContext.saveGraphicsState()
    let transform = NSAffineTransform()
    transform.translateX(by: center.x, yBy: center.y)
    transform.rotate(byDegrees: -24)
    transform.concat()
    if drawRing {
        let ring = NSBezierPath(ovalIn: NSRect(x: -340, y: -122, width: 680, height: 244))
        ring.lineWidth = 26
        color(0xd6a02f).setStroke()
        ring.stroke()
    } else {
        color(0xc45f2c).setFill()
        NSBezierPath(ovalIn: NSRect(x: 272, y: -84, width: 66, height: 66)).fill()
    }
    NSGraphicsContext.restoreGraphicsState()
}

orbit(ring: true)

// The circle, with a thin cream rim.
let circle = NSBezierPath(ovalIn: NSRect(x: center.x - 228, y: center.y - 228, width: 456, height: 456))
NSGradient(starting: color(0x2a8077), ending: color(0x165652))!.draw(in: circle, angle: -90)
circle.lineWidth = 14
color(0xfaf4ea, 0.9).setStroke()
circle.stroke()

// The heartbeat line.
let pulse = NSBezierPath()
let points: [(CGFloat, CGFloat)] = [(-150, 0), (-62, 0), (-26, 74), (22, -112), (64, 58), (90, 0), (150, 0)]
pulse.move(to: NSPoint(x: center.x + points[0].0, y: center.y + points[0].1))
for (x, y) in points.dropFirst() { pulse.line(to: NSPoint(x: center.x + x, y: center.y + y)) }
pulse.lineWidth = 34
pulse.lineCapStyle = .round
pulse.lineJoinStyle = .round
color(0xfaf4ea).setStroke()
pulse.stroke()

orbit(ring: false)
_ = context

image.unlockFocus()
let bitmap = NSBitmapImageRep(data: image.tiffRepresentation!)!
try! bitmap.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: CommandLine.arguments[1]))
