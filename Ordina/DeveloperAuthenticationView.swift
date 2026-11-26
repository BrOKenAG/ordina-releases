import SwiftUI
import CoreImage.CIFilterBuiltins

struct DeveloperAuthenticationView: View {
    @ObservedObject var auth: DeveloperAuthentication
    let configuration: DeveloperConfiguration
    let configure: () -> Void
    @State private var code = ""
    @FocusState private var focused: Bool
    private let accent = Paper.accent

    var body: some View {
        VStack(spacing: 0) {
            Spacer(minLength: 14)
            Image(systemName: "checkmark.shield")
                .font(.system(size: 29, weight: .medium)).foregroundStyle(accent)
                .frame(width: 64, height: 64)
                .background(Paper.surface, in: RoundedRectangle(cornerRadius: 20))
            Text("开发者身份验证")
                .font(.system(size: 26, weight: .semibold)).foregroundStyle(Paper.ink)
                .padding(.top, 14)
            if let uri = auth.setupURI, let qr = qrImage(uri) {
                Image(nsImage: qr).interpolation(.none).resizable()
                    .frame(width: 140, height: 140)
                    .padding(6).background(.white, in: RoundedRectangle(cornerRadius: 8))
                    .padding(.top, 10)
                Text("使用验证器扫码绑定")
                    .font(.system(size: 12)).foregroundStyle(Paper.secondary).padding(.top, 4)
            } else {
                Text("输入 Authenticator 中的六位动态验证码")
                    .font(.system(size: 13)).foregroundStyle(Paper.secondary).padding(.top, 8)
            }
            if auth.bound || auth.setupURI != nil {
                codeField.padding(.top, auth.setupURI == nil ? 28 : 12)
                Button("验证身份") { submit() }
                    .buttonStyle(.plain).font(.system(size: 15, weight: .medium))
                    .foregroundStyle(Paper.onAccent).frame(maxWidth: .infinity).frame(height: 48)
                    .background(accent.opacity(code.count == 6 && !auth.busy ? 1 : 0.55),
                                in: RoundedRectangle(cornerRadius: 12))
                    .disabled(code.count != 6 || auth.busy)
                    .padding(.top, 18)
            } else {
                Button("绑定验证器") { Task { await auth.beginBinding(configuration) } }
                    .buttonStyle(.plain).font(.system(size: 15, weight: .medium))
                    .foregroundStyle(Paper.onAccent).frame(maxWidth: .infinity).frame(height: 48)
                    .background(accent, in: RoundedRectangle(cornerRadius: 12))
                    .disabled(auth.busy).padding(.top, 28)
            }
            if auth.busy { ProgressView().controlSize(.small).padding(.top, 8) }
            if let error = auth.error {
                Text(error).font(.system(size: 12)).foregroundStyle(Paper.error)
                    .multilineTextAlignment(.center).textSelection(.enabled).padding(.top, 8)
            }
            HStack(spacing: 18) {
                if auth.bound && auth.setupURI == nil {
                    Button("无法访问身份验证器？") { Task { await auth.beginBinding(configuration) } }
                }
                if auth.setupURI != nil {
                    Button("取消") { auth.cancelBinding(); code = "" }
                }
                Button("配置发布工具") { configure() }
            }.buttonStyle(.plain).font(.system(size: 12)).foregroundStyle(Paper.secondary)
                .disabled(auth.busy).padding(.top, 16)
            Spacer(minLength: 14)
        }
        .padding(.horizontal, 52).frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Paper.background)
    }

    private var codeField: some View {
        ZStack {
            HStack(spacing: 8) {
                ForEach(0..<6, id: \.self) { index in
                    let digits = Array(code)
                    Text(index < digits.count ? String(digits[index]) : "–")
                        .font(.system(size: 21, design: .monospaced))
                        .foregroundStyle(index < digits.count ? Paper.ink : Paper.secondary)
                        .frame(width: 44, height: 54)
                        .background(Paper.surface, in: RoundedRectangle(cornerRadius: 9))
                        .overlay(RoundedRectangle(cornerRadius: 9).stroke(
                            focused && index == min(code.count, 5) ? accent : Paper.rule,
                            lineWidth: focused && index == min(code.count, 5) ? 2 : 1))
                }
            }.onTapGesture { focused = true }
            TextField("六位验证码", text: $code)
                .textFieldStyle(.plain).opacity(0.015).focused($focused)
                .onChange(of: code) { _, value in
                    let filtered = String(value.filter { $0.isASCII && $0.isNumber }.prefix(6))
                    if filtered != value { code = filtered; return }
                    if filtered.count == 6 { submit() }
                }
                .onSubmit { submit() }
        }.frame(width: 304, height: 54)
    }

    private func submit() {
        guard code.count == 6, !auth.busy else { return }
        let candidate = code
        Task {
            await auth.verify(candidate, configuration: configuration)
            if !auth.unlocked { code = ""; focused = true }
        }
    }

    private func qrImage(_ text: String) -> NSImage? {
        let filter = CIFilter.qrCodeGenerator()
        filter.message = Data(text.utf8)
        guard let image = filter.outputImage?.transformed(by: CGAffineTransform(scaleX: 8, y: 8)),
              let cg = CIContext().createCGImage(image, from: image.extent) else { return nil }
        return NSImage(cgImage: cg, size: NSSize(width: 140, height: 140))
    }
}
