import SwiftUI
import CoreImage.CIFilterBuiltins

struct DeveloperAuthenticationView: View {
    @ObservedObject var auth: DeveloperAuthentication
    let configuration: DeveloperConfiguration
    let configure: () -> Void
    @State private var code = ""
    @FocusState private var focused: Bool

    var body: some View {
        VStack(spacing: 18) {
            Spacer()
            Image(systemName: "checkmark.shield")
                .font(.system(size: 32)).foregroundStyle(Paper.accent)
            Text("开发者身份验证").font(Paper.font(24).weight(.semibold))
            if let uri = auth.setupURI, let qr = qrImage(uri) {
                Image(nsImage: qr).interpolation(.none).resizable()
                    .frame(width: 190, height: 190)
                    .padding(10).background(.white, in: RoundedRectangle(cornerRadius: 8))
                Text("使用验证器扫码绑定").font(Paper.font(12)).foregroundStyle(Paper.secondary)
            }
            if auth.bound || auth.setupURI != nil {
                TextField("六位验证码", text: $code)
                    .textFieldStyle(.roundedBorder).font(.system(size: 22, design: .monospaced))
                    .multilineTextAlignment(.center).frame(width: 220)
                    .focused($focused)
                    .onChange(of: code) { _, value in
                        let filtered = String(value.filter { $0.isASCII && $0.isNumber }.prefix(6))
                        if filtered != value { code = filtered; return }
                        if filtered.count == 6 { submit() }
                    }
                    .onSubmit { submit() }
                Button("验证身份") { submit() }
                    .buttonStyle(PaperButtonStyle(primary: true))
                    .disabled(code.count != 6 || auth.busy)
            } else {
                Button("绑定验证器") { Task { await auth.beginBinding(configuration) } }
                    .buttonStyle(PaperButtonStyle(primary: true)).disabled(auth.busy)
            }
            if auth.busy { ProgressView().controlSize(.small) }
            if let error = auth.error {
                Text(error).font(Paper.font(12)).foregroundStyle(Paper.error)
                    .multilineTextAlignment(.center).textSelection(.enabled)
            }
            HStack(spacing: 18) {
                if auth.bound && auth.setupURI == nil {
                    Button("重新绑定") { Task { await auth.beginBinding(configuration) } }
                        .buttonStyle(PaperQuietButton()).disabled(auth.busy)
                }
                if auth.setupURI != nil {
                    Button("取消") { auth.cancelBinding(); code = "" }
                        .buttonStyle(PaperQuietButton())
                }
                Button("配置发布工具") { configure() }.buttonStyle(PaperQuietButton())
            }
            Spacer()
        }.frame(maxWidth: .infinity, maxHeight: .infinity)
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
        return NSImage(cgImage: cg, size: NSSize(width: 190, height: 190))
    }
}
