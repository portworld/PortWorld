import SwiftUI

struct ProfileConfirmationView: View {
  let settings: AppSettingsStore.Settings
  let onSave: () -> Void

  @State private var draft = ProfileDraft()
  @State private var isLoading = true
  @State private var isSaving = false
  @State private var errorMessage = ""
  @FocusState private var focusedField: Field?

  private let profileClient = ProfileAPIClient()

  var body: some View {
    PWOnboardingScaffold(
      style: .leadingContent,
      title: "Review your profile",
      subtitle: "Edit anything Mario captured before you enter the app.",
      content: {
        VStack(alignment: .leading, spacing: PWSpace.xl) {
          if errorMessage.isEmpty == false {
            PWStatusRow(
              title: "Profile sync issue",
              value: errorMessage,
              tone: .error,
              systemImage: "exclamationmark.triangle"
            )
          }

          PWTextFieldRow(
            label: "Name",
            placeholder: "Your name",
            text: $draft.name,
            textInputAutocapitalization: .words,
            submitLabel: .next
          )
          .focused($focusedField, equals: .name)
          .onSubmit { focusedField = .job }

          PWTextFieldRow(
            label: "Job",
            placeholder: "Founder, designer, engineer...",
            text: $draft.job,
            textInputAutocapitalization: .words,
            submitLabel: .next
          )
          .focused($focusedField, equals: .job)
          .onSubmit { focusedField = .company }

          PWTextFieldRow(
            label: "Company",
            placeholder: "Optional",
            text: $draft.company,
            textInputAutocapitalization: .words,
            submitLabel: .next
          )
          .focused($focusedField, equals: .company)
          .onSubmit { focusedField = .preferences }

          PWTextFieldRow(
            label: "Preferences",
            placeholder: "Concise answers, travel help, bilingual support",
            text: $draft.preferencesText,
            message: "Separate multiple preferences with commas.",
            textInputAutocapitalization: .sentences,
            submitLabel: .next
          )
          .focused($focusedField, equals: .preferences)
          .onSubmit { focusedField = .projects }

          PWTextFieldRow(
            label: "Projects",
            placeholder: "PortWorld launch, hiring, sales pipeline",
            text: $draft.projectsText,
            message: "Separate multiple projects with commas.",
            textInputAutocapitalization: .sentences,
            submitLabel: .done
          )
          .focused($focusedField, equals: .projects)
        }
        .redacted(reason: isLoading ? .placeholder : [])
      },
      footer: {
        PWOnboardingButton(
          title: isSaving ? "Saving..." : "Finish setup",
          isDisabled: isLoading || isSaving,
          action: { Task { await saveProfile() } }
        )
      }
    )
    .task {
      await loadProfile()
    }
  }
}

private extension ProfileConfirmationView {
  enum Field {
    case name
    case job
    case company
    case preferences
    case projects
  }

  func loadProfile() async {
    isLoading = true
    errorMessage = ""

    do {
      let response = try await profileClient.getProfile(settings: settings)
      draft = ProfileDraft(profile: response.profile)
      isLoading = false
    } catch let error as ProfileAPIClient.ClientError {
      errorMessage = error.errorDescription ?? "Profile loading failed."
      isLoading = false
    } catch {
      errorMessage = "Profile loading failed."
      isLoading = false
    }
  }

  func saveProfile() async {
    isSaving = true
    errorMessage = ""

    do {
      _ = try await profileClient.putProfile(settings: settings, draft: draft)
      isSaving = false
      onSave()
    } catch let error as ProfileAPIClient.ClientError {
      errorMessage = error.errorDescription ?? "Profile saving failed."
      isSaving = false
    } catch {
      errorMessage = "Profile saving failed."
      isSaving = false
    }
  }
}
