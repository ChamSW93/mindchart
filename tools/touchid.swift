// Touch ID for MindChart: keeps the password sealed by a key in this Mac's Secure Enclave, and opens it after a fingerprint.
// The key never leaves the Secure Enclave, works only on this Mac, and stops working if fingerprints are added or removed.
//   touchid available        prints "yes" if this Mac can use Touch ID
//   touchid enroll FILE      reads the password from standard input and seals it into FILE
//   touchid unlock FILE      asks for Touch ID, then prints the password
// Exit codes: 0 done, 1 error, 2 cancelled, 3 the key no longer opens it (fingerprints changed), 4 Touch ID unavailable.
import CryptoKit
import Foundation
import LocalAuthentication

struct Sealed: Codable {
    let key: Data        // the Secure Enclave key, in a form only this Mac's Secure Enclave can use
    let ephemeral: Data  // the public half of the one-time key the password was sealed with
    let box: Data        // the password, encrypted with AES-GCM
}

func fail(_ message: String, _ code: Int32) -> Never {
    FileHandle.standardError.write(Data((message + "\n").utf8))
    exit(code)
}

// Both sides of the key agreement arrive at the same AES key: the one-time key now, the Secure Enclave key later.
func sealingKey(_ secret: SharedSecret, ephemeral: Data, owner: Data) -> SymmetricKey {
    secret.hkdfDerivedSymmetricKey(using: SHA256.self, salt: ephemeral + owner, sharedInfo: Data("MindChart Touch ID".utf8),
                                   outputByteCount: 32)
}

let args = CommandLine.arguments
switch (args.count > 1 ? args[1] : "", args.count) {
case ("available", 2):
    var error: NSError?
    let biometrics = LAContext().canEvaluatePolicy(.deviceOwnerAuthenticationWithBiometrics, error: &error)
    print(SecureEnclave.isAvailable && biometrics ? "yes" : "no")

case ("enroll", 3):
    let password = FileHandle.standardInput.readDataToEndOfFile()
    guard !password.isEmpty else { fail("No password given", 1) }
    guard let access = SecAccessControlCreateWithFlags(nil, kSecAttrAccessibleWhenUnlockedThisDeviceOnly,
                                                       [.privateKeyUsage, .biometryCurrentSet], nil) else {
        fail("Couldn't set up Touch ID on this Mac", 4)
    }
    do {
        let owner = try SecureEnclave.P256.KeyAgreement.PrivateKey(accessControl: access)
        let ephemeral = P256.KeyAgreement.PrivateKey()
        let secret = try ephemeral.sharedSecretFromKeyAgreement(with: owner.publicKey)
        let key = sealingKey(secret, ephemeral: ephemeral.publicKey.rawRepresentation, owner: owner.publicKey.rawRepresentation)
        guard let box = try AES.GCM.seal(password, using: key).combined else { fail("Couldn't seal the password", 1) }
        let url = URL(fileURLWithPath: args[2])
        let sealed = Sealed(key: owner.dataRepresentation, ephemeral: ephemeral.publicKey.rawRepresentation, box: box)
        try JSONEncoder().encode(sealed).write(to: url, options: .atomic)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
    } catch {
        fail("Couldn't set up Touch ID: \(error.localizedDescription)", 4)
    }

case ("unlock", 3):
    guard let data = try? Data(contentsOf: URL(fileURLWithPath: args[2])),
          let sealed = try? JSONDecoder().decode(Sealed.self, from: data) else { fail("Touch ID isn't set up", 1) }
    let context = LAContext()
    context.localizedFallbackTitle = ""  // no "Enter Password" button: the MindChart password is typed into MindChart itself
    let done = DispatchSemaphore(value: 0)
    var failure: Error?
    context.evaluatePolicy(.deviceOwnerAuthenticationWithBiometrics, localizedReason: "open your MindChart records") { ok, error in
        failure = ok ? nil : (error ?? LAError(.authenticationFailed))
        done.signal()
    }
    done.wait()
    if let failure = failure {
        switch (failure as? LAError)?.code {
        case .userCancel?, .appCancel?, .systemCancel?, .userFallback?: fail("Touch ID was cancelled", 2)
        case .biometryNotAvailable?, .biometryNotEnrolled?, .biometryLockout?: fail(failure.localizedDescription, 4)
        default: fail(failure.localizedDescription, 1)
        }
    }
    do {  // the fingerprint just given unlocks the Secure Enclave key for this one use
        let owner = try SecureEnclave.P256.KeyAgreement.PrivateKey(dataRepresentation: sealed.key, authenticationContext: context)
        let secret = try owner.sharedSecretFromKeyAgreement(with: P256.KeyAgreement.PublicKey(rawRepresentation: sealed.ephemeral))
        let key = sealingKey(secret, ephemeral: sealed.ephemeral, owner: owner.publicKey.rawRepresentation)
        FileHandle.standardOutput.write(try AES.GCM.open(AES.GCM.SealedBox(combined: sealed.box), using: key))
    } catch {
        fail("Touch ID no longer opens MindChart on this Mac (were fingerprints added or removed?)", 3)
    }

default:
    fail("Usage: touchid available | touchid enroll FILE | touchid unlock FILE", 1)
}
