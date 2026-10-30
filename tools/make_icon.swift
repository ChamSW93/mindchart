// Draws MindChart's app icon as a 1024x1024 PNG, using macOS's built-in drawing tools.
// Mid-century modern "Seedling": a sprout with an olive and a teal leaf, in front of a mustard sun, growing from walnut ground.
// Usage: make_icon <output.png>
import AppKit

let size: CGFloat = 1024
let image = NSImage(size: NSSize(width: size, height: size))
image.lockFocus()

func color(_ hex: UInt32, _ alpha: CGFloat = 1) -> NSColor {
    NSColor(srgbRed: CGFloat((hex >> 16) & 0xff) / 255, green: CGFloat((hex >> 8) & 0xff) / 255,
            blue: CGFloat(hex & 0xff) / 255, alpha: alpha)
}

func point(_ x: CGFloat, _ y: CGFloat) -> NSPoint { NSPoint(x: x, y: y) }

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

// The sun.
let sun = NSBezierPath(ovalIn: NSRect(x: 512 - 228, y: 548 - 228, width: 456, height: 456))
NSGradient(starting: color(0xe0ad45), ending: color(0xcb9426))!.draw(in: sun, angle: -90)

// Walnut ground along the bottom, like a credenza, with an olive line of grass.
NSGraphicsContext.saveGraphicsState()
tilePath.addClip()
color(0x3a2a20).setFill()
NSRect(x: tile.minX, y: tile.minY, width: tile.width, height: 92).fill()
color(0x75843a).setFill()
NSRect(x: tile.minX, y: tile.minY + 92, width: tile.width, height: 12).fill()
NSGraphicsContext.restoreGraphicsState()

// The stem, with a slight curve.
let stem = NSBezierPath()
stem.move(to: point(512, 204))
stem.curve(to: point(512, 530), controlPoint1: point(498, 310), controlPoint2: point(526, 430))
stem.lineWidth = 32
stem.lineCapStyle = .round
color(0x3a2a20).setStroke()
stem.stroke()

// A leaf: filled with a gradient, with a faint cream vein from its base to its tip.
func leaf(_ outline: NSBezierPath, from top: UInt32, to bottom: UInt32, vein: NSBezierPath) {
    NSGradient(starting: color(top), ending: color(bottom))!.draw(in: outline, angle: -90)
    vein.lineWidth = 9
    vein.lineCapStyle = .round
    color(0xfaf4ea, 0.45).setStroke()
    vein.stroke()
}

let left = NSBezierPath()
left.move(to: point(512, 418))
left.curve(to: point(306, 578), controlPoint1: point(418, 418), controlPoint2: point(334, 474))
left.curve(to: point(512, 418), controlPoint1: point(409, 587), controlPoint2: point(493, 521))
let leftVein = NSBezierPath()
leftVein.move(to: point(498, 432))
leftVein.curve(to: point(338, 566), controlPoint1: point(440, 470), controlPoint2: point(380, 530))
leaf(left, from: 0x8a9a48, to: 0x66752f, vein: leftVein)

let right = NSBezierPath()
right.move(to: point(512, 512))
right.curve(to: point(746, 709), controlPoint1: point(540, 624), controlPoint2: point(634, 699))
right.curve(to: point(512, 512), controlPoint1: point(737, 587), controlPoint2: point(634, 512))
let rightVein = NSBezierPath()
rightVein.move(to: point(530, 528))
rightVein.curve(to: point(718, 690), controlPoint1: point(590, 580), controlPoint2: point(660, 650))
leaf(right, from: 0x2a8077, to: 0x165652, vein: rightVein)

// The bud at the top of the stem.
color(0xc45f2c).setFill()
NSBezierPath(ovalIn: NSRect(x: 512 - 26, y: 540 - 26, width: 52, height: 52)).fill()

image.unlockFocus()
let bitmap = NSBitmapImageRep(data: image.tiffRepresentation!)!
try! bitmap.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: CommandLine.arguments[1]))
