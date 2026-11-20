import SwiftUI

struct OnboardingRootView: View {
    @ObservedObject private var onboarding = OnboardingController.shared
    var body: some View {
        Group {
            if onboarding.needsPresentation {
                OnboardingView()
            } else { WorkspaceView() }
        }.task { onboarding.resume() }
    }
}
